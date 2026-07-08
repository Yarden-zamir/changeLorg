from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from changelorg.config import database_path
from changelorg.models import Change, ChangeInput, ChangeUpdate, Source, SourceCreate, SourceUpdate, TimeWindow, utc_now


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _parse_optional_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _parse_dt(value)


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def init_db(db_path: Path | None = None) -> Path:
    path = db_path or database_path()
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                plugin TEXT NOT NULL,
                config TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                summary TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                metadata TEXT NOT NULL,
                dismissed INTEGER NOT NULL DEFAULT 0,
                saved INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                state_updated_at TEXT,
                UNIQUE(source_id, external_id)
            );

            CREATE INDEX IF NOT EXISTS idx_changes_published_at ON changes(published_at);
            CREATE INDEX IF NOT EXISTS idx_changes_source_id ON changes(source_id);
            """
        )
        _ensure_change_state_columns(connection)
    return path


def _ensure_change_state_columns(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(changes)").fetchall()}
    migrations = {
        "dismissed": "ALTER TABLE changes ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0",
        "saved": "ALTER TABLE changes ADD COLUMN saved INTEGER NOT NULL DEFAULT 0",
        "note": "ALTER TABLE changes ADD COLUMN note TEXT NOT NULL DEFAULT ''",
        "state_updated_at": "ALTER TABLE changes ADD COLUMN state_updated_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)


def _source_from_row(row: sqlite3.Row) -> Source:
    raw_config = row["config"]
    config = json.loads(raw_config) if raw_config else {}
    if not isinstance(config, dict):
        raise ValueError(f"source {row['id']} config is not an object")
    return Source(
        id=row["id"],
        name=row["name"],
        plugin=row["plugin"],
        config=config,
        enabled=bool(row["enabled"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _change_from_row(row: sqlite3.Row) -> Change:
    raw_metadata = row["metadata"]
    metadata = json.loads(raw_metadata) if raw_metadata else {}
    if not isinstance(metadata, dict):
        raise ValueError(f"change {row['id']} metadata is not an object")
    return Change(
        id=row["id"],
        source_id=row["source_id"],
        source_name=row["source_name"],
        plugin=row["plugin"],
        external_id=row["external_id"],
        title=row["title"],
        url=row["url"],
        summary=row["summary"],
        content=row["content"],
        published_at=_parse_dt(row["published_at"]),
        fetched_at=_parse_dt(row["fetched_at"]),
        metadata=metadata,
        dismissed=bool(row["dismissed"]),
        saved=bool(row["saved"]),
        note=row["note"],
        state_updated_at=_parse_optional_dt(row["state_updated_at"]),
    )


def add_source(source: SourceCreate, db_path: Path | None = None) -> Source:
    init_db(db_path)
    now = utc_now()
    with _connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO sources (name, plugin, config, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source.name, source.plugin, _json(source.config), int(source.enabled), _dt(now), _dt(now)),
        )
        source_id = int(cursor.lastrowid)
    return get_source(source_id, db_path=db_path)


def get_source(source_id: int, db_path: Path | None = None) -> Source:
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise KeyError(f"source {source_id} not found")
    return _source_from_row(row)


def list_sources(enabled: bool | None = None, db_path: Path | None = None) -> list[Source]:
    init_db(db_path)
    query = "SELECT * FROM sources"
    params: tuple[Any, ...] = ()
    if enabled is not None:
        query += " WHERE enabled = ?"
        params = (int(enabled),)
    query += " ORDER BY name COLLATE NOCASE, id"
    with _connect(db_path) as connection:
        rows = connection.execute(query, params).fetchall()
    return [_source_from_row(row) for row in rows]


def update_source(source_id: int, update: SourceUpdate, db_path: Path | None = None) -> Source:
    init_db(db_path)
    existing = get_source(source_id, db_path=db_path)
    updated = SourceCreate(
        name=update.name if update.name is not None else existing.name,
        plugin=update.plugin if update.plugin is not None else existing.plugin,
        config=update.config if update.config is not None else existing.config,
        enabled=update.enabled if update.enabled is not None else existing.enabled,
    )
    now = utc_now()
    with _connect(db_path) as connection:
        connection.execute(
            """
            UPDATE sources
            SET name = ?, plugin = ?, config = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (updated.name, updated.plugin, _json(updated.config), int(updated.enabled), _dt(now), source_id),
        )
    return get_source(source_id, db_path=db_path)


def delete_source(source_id: int, db_path: Path | None = None) -> None:
    init_db(db_path)
    with _connect(db_path) as connection:
        cursor = connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    if cursor.rowcount == 0:
        raise KeyError(f"source {source_id} not found")


def _fallback_external_id(source_id: int, change: ChangeInput) -> str:
    parts = [str(source_id), change.url or "", change.title, _dt(change.published_at)]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def upsert_changes(source_id: int, changes: list[ChangeInput], db_path: Path | None = None) -> None:
    if not changes:
        return

    init_db(db_path)
    fetched_at = utc_now()
    with _connect(db_path) as connection:
        for change in changes:
            external_id = change.external_id or _fallback_external_id(source_id, change)
            connection.execute(
                """
                INSERT INTO changes (
                    source_id, external_id, title, url, summary, content,
                    published_at, fetched_at, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    summary = excluded.summary,
                    content = excluded.content,
                    published_at = excluded.published_at,
                    fetched_at = excluded.fetched_at,
                    metadata = excluded.metadata
                """,
                (
                    source_id,
                    external_id,
                    change.title,
                    change.url,
                    change.summary,
                    change.content,
                    _dt(change.published_at),
                    _dt(fetched_at),
                    _json(change.metadata),
                ),
            )


def list_changes(
    window: TimeWindow | None = None,
    source_ids: list[int] | None = None,
    include_dismissed: bool = False,
    saved: bool | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[Change]:
    init_db(db_path)
    clauses: list[str] = []
    params: list[Any] = []

    if window is not None:
        clauses.append("changes.published_at >= ? AND changes.published_at <= ?")
        params.extend([_dt(window.start), _dt(window.end)])

    if source_ids:
        placeholders = ",".join("?" for _ in source_ids)
        clauses.append(f"changes.source_id IN ({placeholders})")
        params.extend(source_ids)

    if not include_dismissed:
        clauses.append("changes.dismissed = 0")

    if saved is not None:
        clauses.append("changes.saved = ?")
        params.append(int(saved))

    query = """
        SELECT
            changes.*,
            sources.name AS source_name,
            sources.plugin AS plugin
        FROM changes
        JOIN sources ON sources.id = changes.source_id
    """
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY changes.published_at DESC, changes.id DESC LIMIT ?"
    params.append(limit)

    with _connect(db_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_change_from_row(row) for row in rows]


def update_change(change_id: int, update: ChangeUpdate, db_path: Path | None = None) -> Change:
    init_db(db_path)
    fields: list[str] = []
    params: list[Any] = []
    if update.dismissed is not None:
        fields.append("dismissed = ?")
        params.append(int(update.dismissed))
    if update.saved is not None:
        fields.append("saved = ?")
        params.append(int(update.saved))
    if update.note is not None:
        fields.append("note = ?")
        params.append(update.note)

    if not fields:
        return get_change(change_id, db_path=db_path)

    fields.append("state_updated_at = ?")
    params.append(_dt(utc_now()))
    params.append(change_id)

    with _connect(db_path) as connection:
        cursor = connection.execute(f"UPDATE changes SET {', '.join(fields)} WHERE id = ?", tuple(params))
    if cursor.rowcount == 0:
        raise KeyError(f"change {change_id} not found")
    return get_change(change_id, db_path=db_path)


def get_change(change_id: int, db_path: Path | None = None) -> Change:
    init_db(db_path)
    query = """
        SELECT
            changes.*,
            sources.name AS source_name,
            sources.plugin AS plugin
        FROM changes
        JOIN sources ON sources.id = changes.source_id
        WHERE changes.id = ?
    """
    with _connect(db_path) as connection:
        row = connection.execute(query, (change_id,)).fetchone()
    if row is None:
        raise KeyError(f"change {change_id} not found")
    return _change_from_row(row)
