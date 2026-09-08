from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

import duckdb

from changelorg.config import database_path
from changelorg.models import (
    DEFAULT_OWNER_ID,
    Change,
    ChangeInput,
    ChangeUpdate,
    Profile,
    ProfileCreate,
    Source,
    SourceCreate,
    SourceUpdate,
    TimeWindow,
    utc_now,
    validate_owner_id,
)

# One process owns the database. Revisit this lock and storage choice before deployment with multiple processes.
# Transactions serialize all reads and writes; network fetches stay outside this lock.
_LOCK = RLock()
MAX_SOURCES = 100
MAX_PROFILES = 30
_CHANGE_SELECT = """
    SELECT changes.*, sources.name AS source_name, sources.config AS source_config, sources.plugin
    FROM changes JOIN sources ON sources.id = changes.source_id
"""


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    assert connection.description is not None
    names = [column[0] for column in connection.description]
    return [dict(zip(names, row, strict=True)) for row in connection.fetchall()]


def _source_from_row(row: dict[str, Any]) -> Source:
    return Source.model_validate({**row, "config": json.loads(row["config"])})


def _change_from_row(row: dict[str, Any]) -> Change:
    return Change.model_validate(
        {
            **row,
            "metadata": json.loads(row["metadata"]),
            "source_profile": json.loads(row["source_config"])["profile"],
        }
    )


def _config(config: dict[str, Any]) -> dict[str, Any]:
    return {**config, "profile": ProfileCreate(name=config.get("profile", "dev")).name}


def _schema(connection: duckdb.DuckDBPyConnection, source_start: int = 1, change_start: int = 1) -> None:
    connection.execute(f"CREATE SEQUENCE IF NOT EXISTS source_ids START {source_start}")
    connection.execute(f"CREATE SEQUENCE IF NOT EXISTS change_ids START {change_start}")
    # DuckDB does not support cascading foreign keys. All dependent deletes use the same transaction.
    # Add database foreign keys when DuckDB supports parent/child deletes within one transaction.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            owner_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
            PRIMARY KEY (owner_id, name)
        );
        CREATE TABLE IF NOT EXISTS sources (
            id BIGINT PRIMARY KEY DEFAULT nextval('source_ids'),
            owner_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            plugin VARCHAR NOT NULL,
            config JSON NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE IF NOT EXISTS changes (
            id BIGINT PRIMARY KEY DEFAULT nextval('change_ids'),
            source_id BIGINT NOT NULL,
            external_id VARCHAR NOT NULL,
            title VARCHAR NOT NULL,
            url VARCHAR,
            summary VARCHAR NOT NULL DEFAULT '',
            content VARCHAR NOT NULL DEFAULT '',
            published_at TIMESTAMPTZ NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL,
            metadata JSON NOT NULL,
            dismissed BOOLEAN NOT NULL DEFAULT FALSE,
            saved BOOLEAN NOT NULL DEFAULT FALSE,
            note VARCHAR NOT NULL DEFAULT '',
            state_updated_at TIMESTAMPTZ,
            UNIQUE (source_id, external_id)
        );
        CREATE TABLE IF NOT EXISTS store_metadata (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_sources_owner ON sources(owner_id);
        CREATE INDEX IF NOT EXISTS idx_changes_published_at ON changes(published_at);
    """)


def _is_sqlite(path: Path) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as file:
        return file.read(16) == b"SQLite format 3\x00"


def _migrate_sqlite(legacy: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.migration")
    try:
        with closing(sqlite3.connect(f"{legacy.as_uri()}?mode=rw", uri=True)) as old, old:
            old.row_factory = sqlite3.Row
            old.execute("BEGIN IMMEDIATE")
            sources = [dict(row) for row in old.execute("SELECT * FROM sources")]
            changes = [dict(row) for row in old.execute("SELECT * FROM changes")]
            tables = {row["name"] for row in old.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            sequences = (
                {row["name"]: row["seq"] for row in old.execute("SELECT * FROM sqlite_sequence")}
                if "sqlite_sequence" in tables
                else {}
            )
            source_start = max([sequences.get("sources", 0), *(row["id"] for row in sources)]) + 1
            change_start = max([sequences.get("changes", 0), *(row["id"] for row in changes)]) + 1
            source_ids = {row["id"] for row in sources}
            with duckdb.connect(str(temporary)) as connection:
                connection.execute("BEGIN TRANSACTION")
                try:
                    _schema(connection, source_start, change_start)
                    for row in sources:
                        config = json.loads(row["config"])
                        profile = config.get("profile")
                        if not isinstance(profile, str) or not profile.strip():
                            config["profile"] = "dev"
                        config = _config(config)
                        connection.execute(
                            "INSERT INTO profiles VALUES (?, ?) ON CONFLICT DO NOTHING",
                            [DEFAULT_OWNER_ID, config["profile"]],
                        )
                        connection.execute(
                            """
                            INSERT INTO sources (id, owner_id, name, plugin, config, enabled, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                            [
                                row["id"],
                                DEFAULT_OWNER_ID,
                                row["name"],
                                row["plugin"],
                                _json(config),
                                bool(row["enabled"]),
                                row["created_at"],
                                row["updated_at"],
                            ],
                        )
                    for row in changes:
                        if row["source_id"] not in source_ids:
                            raise ValueError(f"change {row['id']} refers to a missing source")
                        metadata = json.loads(row["metadata"])
                        if not isinstance(metadata, dict):
                            raise TypeError(f"change {row['id']} metadata is not an object")
                        connection.execute(
                            """
                            INSERT INTO changes (
                                id, source_id, external_id, title, url, summary, content, published_at,
                                fetched_at, metadata, dismissed, saved, note, state_updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                            [
                                row["id"],
                                row["source_id"],
                                row["external_id"],
                                row["title"],
                                row["url"],
                                row["summary"],
                                row["content"],
                                row["published_at"],
                                row["fetched_at"],
                                _json(metadata),
                                bool(row.get("dismissed", False)),
                                bool(row.get("saved", False)),
                                row.get("note", ""),
                                row.get("state_updated_at"),
                            ],
                        )
                    # An existing installation must not restore default sources that its user deleted.
                    connection.execute("INSERT INTO store_metadata VALUES ('default_sources_seeded', 'true')")
                    connection.execute("INSERT INTO store_metadata VALUES ('sqlite_imported', ?)", [str(legacy)])
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute("CHECKPOINT")
        os.replace(temporary, target)
        if legacy != target:
            legacy.unlink()
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(legacy) + suffix).unlink(missing_ok=True)
        logging.getLogger(__name__).warning(
            "SQLite migration completed: %s sources and %s changes assigned to %s; no backup retained",
            len(sources), len(changes), DEFAULT_OWNER_ID,
        )
    except Exception as exc:
        raise RuntimeError(f"SQLite migration from {legacy} to {target} failed: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
        Path(str(temporary) + ".wal").unlink(missing_ok=True)


def init_db(db_path: Path | None = None) -> Path:
    path = (db_path or database_path()).expanduser().absolute()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if _is_sqlite(path):
            _migrate_sqlite(path, path)
        elif not path.exists() and path.name == "changelorg.duckdb" and _is_sqlite(path.with_name("changelorg.db")):
            _migrate_sqlite(path.with_name("changelorg.db"), path)
        with duckdb.connect(str(path)) as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                _schema(connection)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
    return path


@contextmanager
def _transaction(db_path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    with _LOCK:
        path = init_db(db_path)
        with duckdb.connect(str(path)) as connection:
            connection.execute("SET TimeZone = 'UTC'")
            connection.execute("BEGIN TRANSACTION")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise


def _ensure_profile(connection: duckdb.DuckDBPyConnection, name: str, owner_id: str) -> None:
    if connection.execute("SELECT 1 FROM profiles WHERE owner_id = ? AND name = ?", [owner_id, name]).fetchone():
        return
    row = connection.execute("SELECT count(*) FROM profiles WHERE owner_id = ?", [owner_id]).fetchone()
    assert row is not None
    if row[0] >= MAX_PROFILES:
        raise ValueError(f"an owner can have at most {MAX_PROFILES} profiles")
    connection.execute("INSERT INTO profiles VALUES (?, ?)", [owner_id, name])


def _insert_source(connection: duckdb.DuckDBPyConnection, source: SourceCreate, owner_id: str) -> Source:
    row = connection.execute("SELECT count(*) FROM sources WHERE owner_id = ?", [owner_id]).fetchone()
    assert row is not None
    if row[0] >= MAX_SOURCES:
        raise ValueError(f"an owner can have at most {MAX_SOURCES} sources")
    config = _config(source.config)
    _ensure_profile(connection, config["profile"], owner_id)
    now = utc_now()
    rows = _rows(
        connection.execute(
            """
        INSERT INTO sources (owner_id, name, plugin, config, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *
    """,
            [owner_id, source.name, source.plugin, _json(config), source.enabled, now, now],
        )
    )
    return _source_from_row(rows[0])


def add_source(source: SourceCreate, db_path: Path | None = None, *, owner_id: str = DEFAULT_OWNER_ID) -> Source:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        return _insert_source(connection, source, owner_id)


def get_source(source_id: int, db_path: Path | None = None, *, owner_id: str = DEFAULT_OWNER_ID) -> Source:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        rows = _rows(connection.execute("SELECT * FROM sources WHERE id = ? AND owner_id = ?", [source_id, owner_id]))
        if not rows:
            raise KeyError(f"source {source_id} not found")
        return _source_from_row(rows[0])


def list_sources(
    enabled: bool | None = None, db_path: Path | None = None, *, owner_id: str | None = DEFAULT_OWNER_ID
) -> list[Source]:
    """Use owner_id=None only for internal all-owner refreshes."""
    clauses, params = [], []
    if owner_id is not None:
        clauses.append("owner_id = ?")
        params.append(validate_owner_id(owner_id))
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(enabled)
    query = "SELECT * FROM sources" + (" WHERE " + " AND ".join(clauses) if clauses else "")
    with _transaction(db_path) as connection:
        return [_source_from_row(row) for row in _rows(connection.execute(query + " ORDER BY lower(name), id", params))]


def update_source(
    source_id: int, update: SourceUpdate, db_path: Path | None = None, *, owner_id: str = DEFAULT_OWNER_ID
) -> Source:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        rows = _rows(connection.execute("SELECT * FROM sources WHERE id = ? AND owner_id = ?", [source_id, owner_id]))
        if not rows:
            raise KeyError(f"source {source_id} not found")
        source = _source_from_row(rows[0])
        updated = SourceCreate.model_validate({**source.model_dump(), **update.model_dump(exclude_none=True)})
        config = _config(updated.config)
        _ensure_profile(connection, config["profile"], owner_id)
        rows = _rows(
            connection.execute(
                """
            UPDATE sources SET name = ?, plugin = ?, config = ?, enabled = ?, updated_at = ?
            WHERE id = ? AND owner_id = ? RETURNING *
        """,
                [updated.name, updated.plugin, _json(config), updated.enabled, utc_now(), source_id, owner_id],
            )
        )
        return _source_from_row(rows[0])


def delete_source(source_id: int, db_path: Path | None = None, *, owner_id: str = DEFAULT_OWNER_ID) -> None:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        connection.execute(
            "DELETE FROM changes WHERE source_id IN (SELECT id FROM sources WHERE id = ? AND owner_id = ?)",
            [source_id, owner_id],
        )
        if not connection.execute(
            "DELETE FROM sources WHERE id = ? AND owner_id = ? RETURNING id", [source_id, owner_id]
        ).fetchone():
            raise KeyError(f"source {source_id} not found")


def _fallback_external_id(source_id: int, change: ChangeInput) -> str:
    parts = [str(source_id), change.url or "", change.title, change.published_at.astimezone(timezone.utc).isoformat()]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def upsert_changes(
    source_id: int, changes: list[ChangeInput], db_path: Path | None = None, *, owner_id: str = DEFAULT_OWNER_ID
) -> None:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        if not connection.execute(
            "SELECT 1 FROM sources WHERE id = ? AND owner_id = ?", [source_id, owner_id]
        ).fetchone():
            raise KeyError(f"source {source_id} not found")
        fetched_at = utc_now()
        for change in changes:
            connection.execute(
                """
                INSERT INTO changes (source_id, external_id, title, url, summary, content, published_at, fetched_at, metadata)
                SELECT id, ?, ?, ?, ?, ?, ?, ?, ? FROM sources WHERE id = ? AND owner_id = ?
                ON CONFLICT (source_id, external_id) DO UPDATE SET
                    title = excluded.title, url = excluded.url, summary = excluded.summary,
                    content = excluded.content, published_at = excluded.published_at,
                    fetched_at = excluded.fetched_at, metadata = excluded.metadata
            """,
                [
                    change.external_id or _fallback_external_id(source_id, change),
                    change.title,
                    change.url,
                    change.summary,
                    change.content,
                    change.published_at,
                    fetched_at,
                    _json(change.metadata),
                    source_id,
                    owner_id,
                ],
            )


def list_changes(
    window: TimeWindow | None = None,
    source_ids: list[int] | None = None,
    include_dismissed: bool = False,
    saved: bool | None = None,
    limit: int = 100,
    db_path: Path | None = None,
    *,
    owner_id: str | None = DEFAULT_OWNER_ID,
) -> list[Change]:
    """Retain changes indefinitely. Use owner_id=None only for internal all-owner refreshes."""
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    clauses: list[str] = []
    params: list[Any] = []
    if owner_id is not None:
        clauses.append("sources.owner_id = ?")
        params.append(validate_owner_id(owner_id))
    if window is not None:
        clauses.append("changes.published_at >= ? AND changes.published_at <= ?")
        params.extend([window.start, window.end])
    if source_ids is not None:
        clauses.append("changes.source_id IN (SELECT unnest(?))")
        params.append(source_ids)
    if not include_dismissed:
        clauses.append("NOT changes.dismissed")
    if saved is not None:
        clauses.append("changes.saved = ?")
        params.append(saved)
    query = _CHANGE_SELECT + (" WHERE " + " AND ".join(clauses) if clauses else "")
    query += " ORDER BY changes.published_at DESC, changes.id DESC LIMIT ?"
    params.append(limit)
    with _transaction(db_path) as connection:
        return [_change_from_row(row) for row in _rows(connection.execute(query, params))]


def get_change(change_id: int, db_path: Path | None = None, *, owner_id: str = DEFAULT_OWNER_ID) -> Change:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        rows = _rows(
            connection.execute(_CHANGE_SELECT + " WHERE changes.id = ? AND sources.owner_id = ?", [change_id, owner_id])
        )
        if not rows:
            raise KeyError(f"change {change_id} not found")
        return _change_from_row(rows[0])


def update_change(
    change_id: int, update: ChangeUpdate, db_path: Path | None = None, *, owner_id: str = DEFAULT_OWNER_ID
) -> Change:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        values = update.model_dump(exclude_none=True)
        if values:
            values["state_updated_at"] = utc_now()
            fields = ", ".join(f"{field} = ?" for field in values)
            connection.execute(
                f"""
                UPDATE changes SET {fields} WHERE id = ?
                AND source_id IN (SELECT id FROM sources WHERE owner_id = ?)
            """,
                [*values.values(), change_id, owner_id],
            )
        rows = _rows(
            connection.execute(_CHANGE_SELECT + " WHERE changes.id = ? AND sources.owner_id = ?", [change_id, owner_id])
        )
        if not rows:
            raise KeyError(f"change {change_id} not found")
        return _change_from_row(rows[0])


def restore_changes(ids: list[int], owner_id: str, db_path: Path | None = None) -> list[Change]:
    validate_owner_id(owner_id)
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    with _transaction(db_path) as connection:
        query = _CHANGE_SELECT + " WHERE changes.id IN (SELECT unnest(?)) AND sources.owner_id = ?"
        rows = _rows(connection.execute(query, [ids, owner_id]))
        if len(rows) != len(ids):
            raise KeyError("one or more changes not found")
        connection.execute(
            "UPDATE changes SET dismissed = FALSE, state_updated_at = ? WHERE id IN (SELECT unnest(?))",
            [utc_now(), ids],
        )
        changes = {row["id"]: _change_from_row(row) for row in _rows(connection.execute(query, [ids, owner_id]))}
        return [changes[change_id] for change_id in ids]


def import_browser_state(state: dict[str, ChangeUpdate], owner_id: str, db_path: Path | None = None) -> dict[str, int]:
    validate_owner_id(owner_id)
    entries: list[tuple[int, str, ChangeUpdate]] = []
    for key, update in state.items():
        if not isinstance(key, str) or len(key) > 3000:
            raise ValueError("browser state keys must be strings of at most 3000 characters")
        source_id, separator, identity = key.partition(":")
        if (
            not separator
            or not identity
            or not source_id.isascii()
            or not source_id.isdecimal()
            or source_id.startswith("0")
            or int(source_id) > 2**63 - 1
        ):
            raise ValueError("browser state keys require a positive source ID and a nonempty identity")
        entries.append((int(source_id), identity, update))
    counts = {"created": 0, "skipped": 0}
    if not entries:
        return counts
    with _transaction(db_path) as connection:
        now = utc_now()
        for source_id, identity, update in entries:
            rows = connection.execute(
                """
                UPDATE changes SET dismissed = ?, saved = ?, note = ?, state_updated_at = ?
                WHERE source_id = ? AND source_id IN (SELECT id FROM sources WHERE owner_id = ?)
                AND coalesce(nullif(external_id, ''), nullif(url, ''), title) = ?
                AND state_updated_at IS NULL AND NOT dismissed AND NOT saved AND note = ''
                RETURNING id
            """,
                [
                    update.dismissed or False,
                    update.saved or False,
                    update.note or "",
                    now,
                    source_id,
                    owner_id,
                    identity,
                ],
            ).fetchall()
            counts["created" if rows else "skipped"] += 1
    return counts


def list_profiles(owner_id: str, db_path: Path | None = None) -> list[Profile]:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        rows = _rows(
            connection.execute(
                """
            SELECT profiles.name, count(sources.id) AS source_count FROM profiles
            LEFT JOIN sources ON sources.owner_id = profiles.owner_id AND (sources.config->>'profile') = profiles.name
                AND sources.enabled
            WHERE profiles.owner_id = ? GROUP BY profiles.name ORDER BY lower(profiles.name), profiles.name
        """,
                [owner_id],
            )
        )
        return [Profile.model_validate(row) for row in rows]


def create_profile(name: str, owner_id: str, db_path: Path | None = None) -> Profile:
    validate_owner_id(owner_id)
    name = ProfileCreate(name=name).name
    with _transaction(db_path) as connection:
        if connection.execute("SELECT 1 FROM profiles WHERE owner_id = ? AND name = ?", [owner_id, name]).fetchone():
            raise ValueError(f"profile {name!r} already exists")
        _ensure_profile(connection, name, owner_id)
        return Profile(name=name, source_count=0)


def rename_profile(name: str, new_name: str, owner_id: str, db_path: Path | None = None) -> Profile:
    validate_owner_id(owner_id)
    name, new_name = ProfileCreate(name=name).name, ProfileCreate(name=new_name).name
    with _transaction(db_path) as connection:
        if not connection.execute(
            "SELECT 1 FROM profiles WHERE owner_id = ? AND name = ?", [owner_id, name]
        ).fetchone():
            raise KeyError(f"profile {name!r} not found")
        if (
            name != new_name
            and connection.execute(
                "SELECT 1 FROM profiles WHERE owner_id = ? AND name = ?", [owner_id, new_name]
            ).fetchone()
        ):
            raise ValueError(f"profile {new_name!r} already exists")
        connection.execute("UPDATE profiles SET name = ? WHERE owner_id = ? AND name = ?", [new_name, owner_id, name])
        rows = connection.execute(
            """
            UPDATE sources SET config = json_merge_patch(config, ?::JSON), updated_at = ?
            WHERE owner_id = ? AND (config->>'profile') = ? RETURNING enabled
        """,
            [_json({"profile": new_name}), utc_now(), owner_id, name],
        ).fetchall()
        return Profile(name=new_name, source_count=sum(enabled for (enabled,) in rows))


def delete_profile(name: str, owner_id: str, db_path: Path | None = None) -> None:
    validate_owner_id(owner_id)
    name = ProfileCreate(name=name).name
    with _transaction(db_path) as connection:
        if not connection.execute(
            "SELECT 1 FROM profiles WHERE owner_id = ? AND name = ?", [owner_id, name]
        ).fetchone():
            raise KeyError(f"profile {name!r} not found")
        connection.execute(
            """
            DELETE FROM changes WHERE source_id IN (SELECT id FROM sources WHERE owner_id = ? AND (config->>'profile') = ?)
        """,
            [owner_id, name],
        )
        connection.execute("DELETE FROM sources WHERE owner_id = ? AND (config->>'profile') = ?", [owner_id, name])
        connection.execute("DELETE FROM profiles WHERE owner_id = ? AND name = ?", [owner_id, name])


def has_data(owner_id: str, db_path: Path | None = None) -> bool:
    validate_owner_id(owner_id)
    with _transaction(db_path) as connection:
        row = connection.execute("SELECT EXISTS (SELECT 1 FROM profiles WHERE owner_id = ?)", [owner_id]).fetchone()
        assert row is not None
        return row[0]


def import_anonymous(anonymous_owner: str, github_owner: str, db_path: Path | None = None) -> dict[str, int]:
    validate_owner_id(anonymous_owner)
    validate_owner_id(github_owner)
    if not anonymous_owner.startswith("anon:") or not github_owner.startswith("github:"):
        raise ValueError("import requires an anonymous source owner and a GitHub destination owner")
    counts = {"profiles": 0, "sources": 0, "changes": 0}
    with _transaction(db_path) as connection:
        profiles = connection.execute(
            "SELECT name FROM profiles WHERE owner_id = ? ORDER BY name", [anonymous_owner]
        ).fetchall()
        for (name,) in profiles:
            if not connection.execute(
                "SELECT 1 FROM profiles WHERE owner_id = ? AND name = ?", [github_owner, name]
            ).fetchone():
                _ensure_profile(connection, name, github_owner)
                counts["profiles"] += 1
        sources = _rows(connection.execute("SELECT * FROM sources WHERE owner_id = ? ORDER BY id", [anonymous_owner]))
        account_sources = [
            _source_from_row(row)
            for row in _rows(connection.execute("SELECT * FROM sources WHERE owner_id = ? ORDER BY id", [github_owner]))
        ]
        identities = {(source.plugin, _json(source.config)): source.id for source in reversed(account_sources)}
        for row in sources:
            source = _source_from_row(row)
            identity = (source.plugin, _json(source.config))
            target_id = identities.get(identity)
            if target_id is None:
                target = _insert_source(connection, source, github_owner)
                target_id = target.id
                identities[identity] = target_id
                connection.execute(
                    "UPDATE sources SET created_at = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                    [source.created_at, source.updated_at, target_id, github_owner],
                )
                counts["sources"] += 1
            changes = _rows(
                connection.execute(
                    _CHANGE_SELECT + " WHERE sources.id = ? AND sources.owner_id = ?", [source.id, anonymous_owner]
                )
            )
            for row in changes:
                change = _change_from_row(row)
                external_id = change.external_id
                # Derived IDs include the source ID. Rebase them so a later account fetch finds the imported state.
                if external_id == _fallback_external_id(source.id, change):
                    external_id = _fallback_external_id(target_id, change)
                # Existing account content and state win; only absent external IDs enter the account.
                copied = connection.execute(
                    """
                    INSERT INTO changes (
                        source_id, external_id, title, url, summary, content, published_at, fetched_at,
                        metadata, dismissed, saved, note, state_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (source_id, external_id) DO NOTHING RETURNING id
                """,
                    [
                        target_id,
                        external_id,
                        change.title,
                        change.url,
                        change.summary,
                        change.content,
                        change.published_at,
                        change.fetched_at,
                        _json(change.metadata),
                        change.dismissed,
                        change.saved,
                        change.note,
                        change.state_updated_at,
                    ],
                ).fetchone()
                counts["changes"] += int(copied is not None)
    return counts


def seed_sources_once(sources: list[SourceCreate], db_path: Path | None = None) -> int:
    with _transaction(db_path) as connection:
        if connection.execute("SELECT 1 FROM store_metadata WHERE key = 'default_sources_seeded'").fetchone():
            return 0
        existing = _rows(connection.execute("SELECT config FROM sources WHERE owner_id = ?", [DEFAULT_OWNER_ID]))
        urls = {url for row in existing if isinstance(url := json.loads(row["config"]).get("url"), str)}
        added = 0
        for source in sources:
            url = source.config.get("url")
            if isinstance(url, str) and url not in urls:
                _insert_source(connection, source, DEFAULT_OWNER_ID)
                urls.add(url)
                added += 1
        connection.execute("INSERT INTO store_metadata VALUES ('default_sources_seeded', 'true')")
        return added
