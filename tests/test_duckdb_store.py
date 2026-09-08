import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import duckdb
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from changelorg import store
from changelorg.cli import app
from changelorg.config import database_path
from changelorg.models import (
    DEFAULT_OWNER_ID,
    ChangeInput,
    ChangeUpdate,
    ProfileCreate,
    SourceCreate,
    SourceUpdate,
    TimeWindow,
)
from changelorg.plugin import PluginManager
from changelorg.seed import DEFAULT_SOURCES, seed_default_sources
from changelorg.service import generate_changes

ACCOUNT = "github:42"
ANONYMOUS = "anon:" + "a" * 64
PUBLISHED = datetime(2001, 1, 1, 12, 30, 15, 123456, tzinfo=timezone(timedelta(hours=3)))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "changelorg.duckdb"


def add_item(
    db_path: Path, owner: str = DEFAULT_OWNER_ID, *, config: dict | None = None, external_id: str | None = "item"
):
    source = store.add_source(
        SourceCreate(name="Feed", config=config or {"url": "https://example.com/feed"}), db_path, owner_id=owner
    )
    store.upsert_changes(
        source.id,
        [
            ChangeInput(
                external_id=external_id,
                title="An item",
                published_at=PUBLISHED,
                metadata={"nested": {"items": [1, True, None]}},
            )
        ],
        db_path,
        owner_id=owner,
    )
    return source, store.list_changes(db_path=db_path, owner_id=owner)[0]


def test_owner_isolation_covers_reads_writes_and_foreign_ids(db_path):
    source, change = add_item(db_path)
    other, other_change = add_item(db_path, ACCOUNT)
    assert source.owner_id == DEFAULT_OWNER_ID
    assert [item.id for item in store.list_sources(db_path=db_path, owner_id=ACCOUNT)] == [other.id]
    assert {item.id for item in store.list_sources(db_path=db_path, owner_id=None)} == {source.id, other.id}
    assert store.list_changes(source_ids=[source.id], db_path=db_path, owner_id=ACCOUNT) == []
    assert [item.id for item in store.list_changes(db_path=db_path, owner_id=ACCOUNT)] == [other_change.id]
    assert len(store.list_changes(db_path=db_path, owner_id=None)) == 2
    forbidden = [
        lambda: store.get_source(source.id, db_path, owner_id=ACCOUNT),
        lambda: store.update_source(
            source.id, SourceUpdate(name="Hijack", config={"profile": "leak"}), db_path, owner_id=ACCOUNT
        ),
        lambda: store.delete_source(source.id, db_path, owner_id=ACCOUNT),
        lambda: store.upsert_changes(source.id, [], db_path, owner_id=ACCOUNT),
        lambda: store.upsert_changes(
            source.id, [ChangeInput(title="Hijack", published_at=PUBLISHED)], db_path, owner_id=ACCOUNT
        ),
        lambda: store.get_change(change.id, db_path, owner_id=ACCOUNT),
        lambda: store.update_change(change.id, ChangeUpdate(note="Hijack"), db_path, owner_id=ACCOUNT),
        lambda: store.update_change(change.id, ChangeUpdate(), db_path, owner_id=ACCOUNT),
    ]
    for operation in forbidden:
        with pytest.raises(KeyError):
            operation()
    assert store.get_source(source.id, db_path) == source
    assert store.get_change(change.id, db_path) == change
    assert [profile.name for profile in store.list_profiles(ACCOUNT, db_path)] == ["dev"]
    store.delete_source(other.id, db_path, owner_id=ACCOUNT)
    assert store.list_changes(db_path=db_path, owner_id=ACCOUNT) == []
    assert store.get_change(change.id, db_path) == change


@pytest.mark.parametrize(
    "owner", ["", "github:", "github:abc", "github:１２", "anon:abc", "anon:" + "g" * 64, "google:42"]
)
def test_invalid_owners_fail_before_writes(db_path, owner):
    with pytest.raises(ValueError):
        store.add_source(SourceCreate(name="Feed"), db_path, owner_id=owner)
    assert not db_path.exists()


@pytest.mark.parametrize("name", ["", " \t ", "a" * 101])
def test_profile_names_reject_invalid_names(name):
    with pytest.raises(ValidationError):
        ProfileCreate(name=name)


def test_empty_profiles_and_rename_preserve_membership_state_and_owner(db_path):
    assert not store.has_data(ACCOUNT, db_path)
    profile = store.create_profile("  Empty  ", ACCOUNT, db_path)
    assert profile.name == "Empty"
    assert profile.source_count == 0
    assert store.has_data(ACCOUNT, db_path)
    source, change = add_item(
        db_path, ACCOUNT, config={"profile": " old ", "url": "https://example.com", "nested": {"a": None}}
    )
    other, _ = add_item(db_path, config={"profile": "old"})
    store.update_change(change.id, ChangeUpdate(saved=True, dismissed=True, note="Private"), db_path, owner_id=ACCOUNT)
    renamed = store.rename_profile("old", " new ", ACCOUNT, db_path)
    assert renamed.name == "new"
    assert renamed.source_count == 1
    assert store.rename_profile("new", "new", ACCOUNT, db_path).source_count == 1
    updated = store.get_source(source.id, db_path, owner_id=ACCOUNT)
    assert updated.config == {**source.config, "profile": "new"}
    assert store.get_source(other.id, db_path).config["profile"] == "old"
    updated_change = store.get_change(change.id, db_path, owner_id=ACCOUNT)
    assert updated_change.source_profile == "new"
    assert updated_change.note == "Private"
    assert updated_change.saved and updated_change.dismissed
    with pytest.raises(ValueError):
        store.rename_profile("new", "Empty", ACCOUNT, db_path)
    with pytest.raises(KeyError):
        store.rename_profile("old", "other", ACCOUNT, db_path)
    assert {p.name: p.source_count for p in store.list_profiles(ACCOUNT, db_path)} == {"Empty": 0, "new": 1}
    store.delete_profile("new", ACCOUNT, db_path)
    assert store.list_sources(db_path=db_path, owner_id=ACCOUNT) == []
    assert store.list_changes(db_path=db_path, owner_id=ACCOUNT, include_dismissed=True) == []
    assert store.get_source(other.id, db_path).id == other.id
    store.delete_profile("Empty", ACCOUNT, db_path)
    assert not store.has_data(ACCOUNT, db_path)
    with pytest.raises(KeyError):
        store.delete_profile("old", ACCOUNT, db_path)


def test_source_update_creates_profile_and_leaves_old_empty_profile(db_path):
    source, _ = add_item(db_path, ACCOUNT)
    updated = store.update_source(
        source.id, SourceUpdate(config={"profile": " games ", "url": "other"}, enabled=False), db_path, owner_id=ACCOUNT
    )
    assert updated.config == {"profile": "games", "url": "other"}
    assert not updated.enabled
    assert {p.name: p.source_count for p in store.list_profiles(ACCOUNT, db_path)} == {"dev": 0, "games": 0}


def test_profile_counts_exclude_disabled_sources_without_losing_membership(db_path):
    enabled, _ = add_item(db_path, ACCOUNT)
    disabled, _ = add_item(db_path, ACCOUNT)
    other, _ = add_item(db_path)
    store.update_source(disabled.id, SourceUpdate(enabled=False), db_path, owner_id=ACCOUNT)
    assert store.list_profiles(ACCOUNT, db_path)[0].source_count == 1
    renamed = store.rename_profile("dev", "renamed", ACCOUNT, db_path)
    assert renamed.source_count == 1
    assert store.list_profiles(ACCOUNT, db_path) == [renamed]
    assert store.get_source(disabled.id, db_path, owner_id=ACCOUNT).config["profile"] == "renamed"
    assert store.get_source(other.id, db_path).config["profile"] == "dev"
    store.update_source(enabled.id, SourceUpdate(enabled=False), db_path, owner_id=ACCOUNT)
    renamed = store.rename_profile("renamed", "disabled", ACCOUNT, db_path)
    assert renamed.source_count == 0
    assert store.list_profiles(ACCOUNT, db_path) == [renamed]
    assert store.rename_profile("disabled", "disabled", ACCOUNT, db_path) == renamed


def test_restore_preserves_state_normalizes_duplicates_and_returns_complete_changes(db_path):
    _, first = add_item(db_path, ACCOUNT)
    _, second = add_item(db_path, ACCOUNT)
    _, untouched = add_item(db_path, ACCOUNT)
    first = store.update_change(
        first.id, ChangeUpdate(dismissed=True, saved=True, note="Keep"), db_path, owner_id=ACCOUNT
    )
    restored = store.restore_changes([second.id, first.id, second.id], ACCOUNT, db_path)
    assert [change.id for change in restored] == [second.id, first.id]
    for before, after in zip([second, first], restored, strict=True):
        assert after.state_updated_at is not None
        assert after == before.model_copy(update={"dismissed": False, "state_updated_at": after.state_updated_at})
        assert store.get_change(after.id, db_path, owner_id=ACCOUNT) == after
    assert store.get_change(untouched.id, db_path, owner_id=ACCOUNT) == untouched
    assert store.restore_changes([], ACCOUNT, db_path) == []


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_restore_prevalidates_entire_batch_without_partial_writes(db_path, monkeypatch, foreign, invalid_first):
    _, owned = add_item(db_path, ACCOUNT)
    _, other = add_item(db_path)
    owned = store.update_change(owned.id, ChangeUpdate(dismissed=True), db_path, owner_id=ACCOUNT)
    invalid_id = other.id if foreign else other.id + 100
    ids = [invalid_id, owned.id] if invalid_first else [owned.id, invalid_id]
    clock = Mock(side_effect=AssertionError("state mutation before ownership validation"))
    monkeypatch.setattr(store, "utc_now", clock)
    with pytest.raises(KeyError, match="one or more changes not found"):
        store.restore_changes(ids, ACCOUNT, db_path)
    clock.assert_not_called()
    assert store.get_change(owned.id, db_path, owner_id=ACCOUNT) == owned
    assert store.get_change(other.id, db_path) == other


def test_browser_state_import_is_owned_idempotent_and_preserves_content(db_path):
    source, owned = add_item(db_path, ACCOUNT)
    other_source, other = add_item(db_path)
    state = {
        f"{source.id}:item": ChangeUpdate(dismissed=True, saved=True, note="Browser note"),
        f"{source.id}:missing": ChangeUpdate(saved=True),
        f"{other_source.id}:item": ChangeUpdate(note="Foreign"),
        f"{other_source.id + 100}:item": ChangeUpdate(note="Missing"),
    }
    assert store.import_browser_state(state, ACCOUNT, db_path) == {"created": 1, "skipped": 3}
    imported = store.get_change(owned.id, db_path, owner_id=ACCOUNT)
    assert imported.state_updated_at is not None
    assert imported == owned.model_copy(
        update={"dismissed": True, "saved": True, "note": "Browser note", "state_updated_at": imported.state_updated_at}
    )
    assert store.get_change(other.id, db_path) == other
    assert store.import_browser_state(state, ACCOUNT, db_path) == {"created": 0, "skipped": 4}
    assert store.get_change(owned.id, db_path, owner_id=ACCOUNT) == imported
    assert store.import_browser_state({}, ACCOUNT, db_path) == {"created": 0, "skipped": 0}


@pytest.mark.parametrize(
    "update",
    [
        ChangeUpdate(),
        ChangeUpdate(saved=False),
        ChangeUpdate(note=""),
        ChangeUpdate(dismissed=True),
        ChangeUpdate(saved=True),
        ChangeUpdate(note="Note"),
    ],
)
def test_browser_state_import_completes_partial_state_and_marks_defaults(db_path, update):
    source, change = add_item(db_path, ACCOUNT)
    key = f"{source.id}:item"
    assert store.import_browser_state({key: update}, ACCOUNT, db_path) == {"created": 1, "skipped": 0}
    imported = store.get_change(change.id, db_path, owner_id=ACCOUNT)
    assert imported.dismissed == (update.dismissed or False)
    assert imported.saved == (update.saved or False)
    assert imported.note == (update.note or "")
    assert imported.state_updated_at is not None
    assert store.import_browser_state({key: ChangeUpdate(note="Overwrite")}, ACCOUNT, db_path) == {
        "created": 0,
        "skipped": 1,
    }
    assert store.get_change(change.id, db_path, owner_id=ACCOUNT) == imported


@pytest.mark.parametrize(
    "update,clear_timestamp",
    [
        (ChangeUpdate(dismissed=False, saved=False, note=""), False),
        (ChangeUpdate(dismissed=True), True),
        (ChangeUpdate(saved=True), True),
        (ChangeUpdate(note="Server note"), True),
    ],
)
def test_browser_state_import_keeps_any_existing_server_state(db_path, update, clear_timestamp):
    source, change = add_item(db_path, ACCOUNT)
    store.update_change(change.id, update, db_path, owner_id=ACCOUNT)
    if clear_timestamp:
        with duckdb.connect(str(db_path)) as connection:
            connection.execute("UPDATE changes SET state_updated_at = NULL WHERE id = ?", [change.id])
    before = store.get_change(change.id, db_path, owner_id=ACCOUNT)
    assert store.import_browser_state(
        {f"{source.id}:item": ChangeUpdate(dismissed=True, saved=True, note="Browser")}, ACCOUNT, db_path
    ) == {"created": 0, "skipped": 1}
    assert store.get_change(change.id, db_path, owner_id=ACCOUNT) == before


@pytest.mark.parametrize(
    "external_id,url,identity",
    [
        ("urn:item:1", "https://example.com/item", "urn:item:1"),
        ("", "https://example.com/item", "https://example.com/item"),
        ("", None, "An item"),
        ("", "", "An item"),
    ],
)
def test_browser_state_import_matches_exact_frontend_key_precedence(db_path, external_id, url, identity):
    source, change = add_item(db_path, ACCOUNT)
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("UPDATE changes SET external_id = ?, url = ? WHERE id = ?", [external_id, url, change.id])
    aliases = {value for value in [external_id, url, change.title] if value and value != identity}
    state = {f"{source.id}:{alias}": ChangeUpdate(note="Wrong alias") for alias in aliases}
    state[f"{source.id}:{identity}"] = ChangeUpdate(note="Exact key")
    assert store.import_browser_state(state, ACCOUNT, db_path) == {"created": 1, "skipped": len(aliases)}
    assert store.get_change(change.id, db_path, owner_id=ACCOUNT).note == "Exact key"


@pytest.mark.parametrize(
    "key",
    [
        "",
        "1",
        ":item",
        "1:",
        "0:item",
        "-1:item",
        "+1:item",
        "01:item",
        " 1:item",
        "1 :item",
        "1.0:item",
        "abc:item",
        "\uff11:item",
        "9223372036854775808:item",
        "1:" + "a" * 2999,
        1,
    ],
)
def test_browser_state_import_rejects_malformed_keys_before_any_write(db_path, key):
    source, change = add_item(db_path, ACCOUNT)
    with pytest.raises(ValueError):
        store.import_browser_state(
            {f"{source.id}:item": ChangeUpdate(saved=True), key: ChangeUpdate(saved=True)}, ACCOUNT, db_path
        )
    assert store.get_change(change.id, db_path, owner_id=ACCOUNT) == change


def test_browser_state_import_accepts_maximum_key_length(db_path):
    assert store.import_browser_state({"1:" + "a" * 2998: ChangeUpdate()}, ACCOUNT, db_path) == {
        "created": 0,
        "skipped": 1,
    }


@pytest.mark.parametrize("operation", [store.restore_changes, store.import_browser_state])
def test_batch_state_operations_reject_invalid_owner_before_database_access(db_path, operation):
    with pytest.raises(ValueError):
        operation([] if operation is store.restore_changes else {}, "", db_path)
    assert not db_path.exists()


def test_import_copies_empty_profiles_sources_and_state_without_overwrite(db_path):
    store.create_profile("Empty", ANONYMOUS, db_path)
    anonymous, anonymous_change = add_item(
        db_path, ANONYMOUS, config={"url": "same", "profile": "dev", "nested": {"b": 2, "a": 1}}
    )
    account, account_change = add_item(
        db_path, ACCOUNT, config={"nested": {"a": 1, "b": 2}, "profile": "dev", "url": "same"}
    )
    store.update_source(account.id, SourceUpdate(name="Account name", enabled=False), db_path, owner_id=ACCOUNT)
    store.update_change(account_change.id, ChangeUpdate(saved=True, note="Account note"), db_path, owner_id=ACCOUNT)
    store.update_change(
        anonymous_change.id, ChangeUpdate(dismissed=True, note="Anonymous note"), db_path, owner_id=ANONYMOUS
    )
    store.upsert_changes(
        anonymous.id,
        [ChangeInput(external_id="extra", title="Only anonymous", published_at=PUBLISHED)],
        db_path,
        owner_id=ANONYMOUS,
    )
    extra = next(
        change for change in store.list_changes(db_path=db_path, owner_id=ANONYMOUS) if change.external_id == "extra"
    )
    store.update_change(
        extra.id, ChangeUpdate(saved=True, dismissed=True, note="Copy this"), db_path, owner_id=ANONYMOUS
    )
    different, _ = add_item(db_path, ANONYMOUS, config={"profile": "dev", "url": "different"})
    before = store.list_changes(db_path=db_path, owner_id=ANONYMOUS, include_dismissed=True)
    assert store.import_anonymous(ANONYMOUS, ACCOUNT, db_path) == {"profiles": 1, "sources": 1, "changes": 2}
    assert store.import_anonymous(ANONYMOUS, ACCOUNT, db_path) == {"profiles": 0, "sources": 0, "changes": 0}
    assert store.list_changes(db_path=db_path, owner_id=ANONYMOUS, include_dismissed=True) == before
    assert store.get_source(anonymous.id, db_path, owner_id=ANONYMOUS).id == anonymous.id
    account_source = store.get_source(account.id, db_path, owner_id=ACCOUNT)
    assert account_source.name == "Account name"
    assert not account_source.enabled
    preserved = store.get_change(account_change.id, db_path, owner_id=ACCOUNT)
    assert preserved.note == "Account note"
    assert preserved.saved and not preserved.dismissed
    copied = store.list_changes(db_path=db_path, owner_id=ACCOUNT, include_dismissed=True)
    copied_extra = next(change for change in copied if change.external_id == "extra")
    assert copied_extra.saved and copied_extra.dismissed
    assert copied_extra.note == "Copy this"
    assert copied_extra.state_updated_at == store.get_change(extra.id, db_path, owner_id=ANONYMOUS).state_updated_at
    assert any(
        source.config == different.config and source.id != different.id
        for source in store.list_sources(db_path=db_path, owner_id=ACCOUNT)
    )
    assert {p.name: p.source_count for p in store.list_profiles(ACCOUNT, db_path)} == {"dev": 1, "Empty": 0}


@pytest.mark.parametrize(
    "config,plugin",
    [
        ({"profile": "games", "url": "same"}, "rss-atom"),
        ({"profile": "dev", "url": "same", "limit": 5}, "rss-atom"),
        ({"profile": "dev", "url": "same"}, "html-news"),
    ],
)
def test_import_source_identity_includes_profile_plugin_and_full_config(db_path, config, plugin):
    add_item(db_path, ACCOUNT, config={"url": "same"})
    store.add_source(SourceCreate(name="Different", config=config, plugin=plugin), db_path, owner_id=ANONYMOUS)
    assert store.import_anonymous(ANONYMOUS, ACCOUNT, db_path)["sources"] == 1


def test_import_rejects_wrong_namespaces(db_path):
    with pytest.raises(ValueError):
        store.import_anonymous(ACCOUNT, ANONYMOUS, db_path)


@pytest.mark.parametrize("existing_account", [False, True])
def test_import_derived_ids_deduplicate_on_import_and_refetch(db_path, existing_account):
    anonymous, change = add_item(db_path, ANONYMOUS, external_id=None)
    store.update_change(change.id, ChangeUpdate(saved=True, note="Anonymous state"), db_path, owner_id=ANONYMOUS)
    if existing_account:
        add_item(db_path, ACCOUNT, external_id=None)
    assert store.import_anonymous(ANONYMOUS, ACCOUNT, db_path)["changes"] == int(not existing_account)
    assert store.import_anonymous(ANONYMOUS, ACCOUNT, db_path) == {"profiles": 0, "sources": 0, "changes": 0}
    source = store.list_sources(db_path=db_path, owner_id=ACCOUNT)[0]
    store.upsert_changes(source.id, [ChangeInput(title="An item", published_at=PUBLISHED)], db_path, owner_id=ACCOUNT)
    changes = store.list_changes(db_path=db_path, owner_id=ACCOUNT)
    assert len(changes) == 1
    assert changes[0].saved == (not existing_account)
    assert changes[0].note == ("" if existing_account else "Anonymous state")
    assert store.get_source(anonymous.id, db_path, owner_id=ANONYMOUS).id == anonymous.id


def test_upsert_batch_failure_rolls_back_prior_items(db_path):
    source, original = add_item(db_path)
    with pytest.raises(ValueError):
        store.upsert_changes(
            source.id,
            [
                ChangeInput(external_id="item", title="Must roll back", published_at=PUBLISHED),
                ChangeInput(
                    external_id="invalid",
                    title="Invalid JSON",
                    published_at=PUBLISHED,
                    metadata={"number": float("nan")},
                ),
            ],
            db_path,
        )
    assert store.list_changes(db_path=db_path) == [original]


def test_limits_and_import_failure_roll_back_all_changes(db_path, monkeypatch):
    assert store.MAX_SOURCES == 100
    assert store.MAX_PROFILES == 30
    monkeypatch.setattr(store, "MAX_SOURCES", 2)
    monkeypatch.setattr(store, "MAX_PROFILES", 2)
    for owner in (ACCOUNT, ANONYMOUS):
        store.add_source(SourceCreate(name="First"), db_path, owner_id=owner)
        store.add_source(SourceCreate(name="Second", config={"url": owner}), db_path, owner_id=owner)
    with pytest.raises(ValueError, match="sources"):
        store.add_source(SourceCreate(name="Overflow", config={"profile": "new"}), db_path, owner_id=ACCOUNT)
    assert [p.name for p in store.list_profiles(ACCOUNT, db_path)] == ["dev"]
    store.create_profile("Empty", ANONYMOUS, db_path)
    with pytest.raises(ValueError, match="sources"):
        store.import_anonymous(ANONYMOUS, ACCOUNT, db_path)
    assert [p.name for p in store.list_profiles(ACCOUNT, db_path)] == ["dev"]
    store.create_profile("Empty", ACCOUNT, db_path)
    with pytest.raises(ValueError, match="profiles"):
        store.create_profile("Overflow", ACCOUNT, db_path)
    with pytest.raises(ValueError, match="profiles"):
        source = store.list_sources(db_path=db_path, owner_id=ACCOUNT)[0]
        store.update_source(source.id, SourceUpdate(config={"profile": "Overflow"}), db_path, owner_id=ACCOUNT)


def test_native_types_timezone_json_retention_and_refetch(db_path):
    source, change = add_item(db_path)
    store.update_change(change.id, ChangeUpdate(saved=True, note="Keep"), db_path)
    store.upsert_changes(
        source.id,
        [
            ChangeInput(
                external_id="item",
                title="Updated",
                published_at=PUBLISHED,
                metadata={"unicode": "\u05d0", "array": [False, {"n": 1.5}]},
            )
        ],
        db_path,
    )
    store.init_db(db_path)
    fetched = store.get_change(change.id, db_path)
    assert fetched.published_at == PUBLISHED
    assert fetched.published_at.utcoffset() == timedelta(0)
    assert fetched.fetched_at.tzinfo is not None
    assert fetched.state_updated_at.tzinfo is not None
    assert fetched.metadata == {"unicode": "\u05d0", "array": [False, {"n": 1.5}]}
    assert fetched.saved and fetched.note == "Keep"
    assert fetched.title == "Updated"
    assert len(store.list_changes(db_path=db_path, saved=True)) == 1
    assert store.list_changes(db_path=db_path, saved=False) == []
    with duckdb.connect(str(db_path)) as connection:
        assert connection.execute(
            "SELECT typeof(config), typeof(enabled), typeof(created_at) FROM sources"
        ).fetchone() == ("JSON", "BOOLEAN", "TIMESTAMP WITH TIME ZONE")
        assert connection.execute(
            "SELECT typeof(metadata), typeof(saved), typeof(published_at) FROM changes"
        ).fetchone() == ("JSON", "BOOLEAN", "TIMESTAMP WITH TIME ZONE")
    assert not list(db_path.parent.glob("*.bak"))


def make_sqlite(path: Path, *, state: bool = True, invalid: bool = False):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, plugin TEXT NOT NULL,
                config TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL, external_id TEXT NOT NULL,
                title TEXT NOT NULL, url TEXT, summary TEXT NOT NULL, content TEXT NOT NULL,
                published_at TEXT NOT NULL, fetched_at TEXT NOT NULL, metadata TEXT NOT NULL,
                UNIQUE(source_id, external_id)
            );
        """)
        if state:
            connection.executescript("""
                ALTER TABLE changes ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE changes ADD COLUMN saved INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE changes ADD COLUMN note TEXT NOT NULL DEFAULT '';
                ALTER TABLE changes ADD COLUMN state_updated_at TEXT;
            """)
        timestamp = PUBLISHED.isoformat()
        connection.execute(
            "INSERT INTO sources VALUES (17, 'Old feed', 'rss-atom', ?, 0, ?, ?)",
            [json.dumps({"url": "legacy", "profile": "games"}), timestamp, timestamp],
        )
        connection.execute(
            "INSERT INTO changes (id, source_id, external_id, title, url, summary, content, published_at, fetched_at, metadata) VALUES (91, 17, 'old', 'Old item', NULL, '', '', ?, ?, ?)",
            [timestamp, timestamp, "not json" if invalid else '{"old":true}'],
        )
        if state:
            connection.execute(
                "UPDATE changes SET dismissed = 1, saved = 1, note = 'Preserve', state_updated_at = ?", [timestamp]
            )
        connection.execute("UPDATE sqlite_sequence SET seq = 200 WHERE name = 'sources'")
        connection.execute("UPDATE sqlite_sequence SET seq = 300 WHERE name = 'changes'")


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("state", [False, True])
@pytest.mark.parametrize(
    "profile_config,expected_profile",
    [
        ({"profile": "games"}, "games"),
        ({"profile": " games "}, "games"),
        ({}, "dev"),
        ({"profile": None}, "dev"),
        ({"profile": 42}, "dev"),
        ({"profile": False}, "dev"),
        ({"profile": []}, "dev"),
        ({"profile": {}}, "dev"),
        ({"profile": ""}, "dev"),
        ({"profile": " \t\n "}, "dev"),
    ],
)
def test_sqlite_migration_preserves_ids_state_and_sequences_without_backup(
    tmp_path, monkeypatch, explicit, state, profile_config, expected_profile
):
    legacy = tmp_path / "changelorg.db"
    make_sqlite(legacy, state=state)
    with closing(sqlite3.connect(legacy)) as connection, connection:
        connection.execute("UPDATE sources SET config = ?", [json.dumps({"url": "legacy", **profile_config})])
    monkeypatch.setenv("CHANGELORG_DATA_DIR", str(tmp_path))
    if explicit:
        monkeypatch.setenv("CHANGELORG_DB", str(legacy))
    else:
        monkeypatch.delenv("CHANGELORG_DB", raising=False)
    target = store.init_db()
    assert target == (legacy if explicit else tmp_path / "changelorg.duckdb")
    assert sorted(path.name for path in tmp_path.iterdir()) == [target.name]
    source = store.get_source(17)
    assert source.id == 17
    assert source.owner_id == DEFAULT_OWNER_ID
    assert source.config == {"url": "legacy", "profile": expected_profile}
    assert not source.enabled
    assert source.created_at == PUBLISHED
    change = store.get_change(91)
    assert change.id == 91
    assert change.source_id == 17
    assert change.source_profile == expected_profile
    assert change.published_at == PUBLISHED
    assert change.metadata == {"old": True}
    assert change.saved == state and change.dismissed == state
    assert change.note == ("Preserve" if state else "")
    assert change.state_updated_at == (PUBLISHED if state else None)
    assert store.list_sources(owner_id=ACCOUNT) == []
    assert seed_default_sources() == 0
    assert [p.name for p in store.list_profiles(DEFAULT_OWNER_ID)] == [expected_profile]
    new = store.add_source(SourceCreate(name="New"))
    assert new.id > 200
    store.upsert_changes(new.id, [ChangeInput(title="New", published_at=PUBLISHED)])
    assert store.list_changes(source_ids=[new.id])[0].id > 300
    store.delete_source(17)
    store.init_db()
    assert seed_default_sources() == 0
    with pytest.raises(KeyError):
        store.get_source(17)


@pytest.mark.parametrize("explicit", [False, True])
def test_failed_sqlite_migration_keeps_original_and_no_partial_target(tmp_path, explicit):
    legacy = tmp_path / "changelorg.db"
    make_sqlite(legacy, invalid=True)
    before = legacy.read_bytes()
    target = legacy if explicit else tmp_path / "changelorg.duckdb"
    with pytest.raises(RuntimeError, match="SQLite migration"):
        store.init_db(target)
    assert legacy.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [legacy.name]
    with closing(sqlite3.connect(legacy)) as connection:
        assert connection.execute("SELECT id FROM sources").fetchone() == (17,)


def test_checkpoint_failure_does_not_replace_sqlite(tmp_path, monkeypatch):
    legacy = tmp_path / "changelorg.db"
    make_sqlite(legacy)
    before = legacy.read_bytes()
    original_connect = duckdb.connect

    class FailingCheckpoint:
        def __init__(self, *args, **kwargs):
            self.connection = original_connect(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.connection.close()

        def execute(self, sql, *args):
            if sql == "CHECKPOINT":
                raise RuntimeError("checkpoint failed")
            return self.connection.execute(sql, *args)

    monkeypatch.setattr(duckdb, "connect", FailingCheckpoint)
    with pytest.raises(RuntimeError, match="checkpoint failed"):
        store.init_db(legacy)
    assert legacy.read_bytes() == before
    assert list(tmp_path.iterdir()) == [legacy]


def test_existing_duckdb_does_not_import_sibling_again(db_path):
    source, _ = add_item(db_path)
    legacy = db_path.with_name("changelorg.db")
    make_sqlite(legacy)
    store.init_db(db_path)
    assert [item.id for item in store.list_sources(db_path=db_path)] == [source.id]
    assert legacy.exists()


def test_default_database_path_and_one_time_owner_seed(db_path, monkeypatch):
    monkeypatch.delenv("CHANGELORG_DB", raising=False)
    monkeypatch.setenv("CHANGELORG_DATA_DIR", str(db_path.parent))
    assert database_path() == db_path
    assert seed_default_sources(db_path) == len(DEFAULT_SOURCES)
    assert store.list_sources(db_path=db_path, owner_id=ACCOUNT) == []
    source = store.list_sources(db_path=db_path)[0]
    store.delete_source(source.id, db_path)
    assert seed_default_sources(db_path) == 0
    assert len(store.list_sources(db_path=db_path)) == len(DEFAULT_SOURCES) - 1
    for profile in store.list_profiles(DEFAULT_OWNER_ID, db_path):
        store.delete_profile(profile.name, DEFAULT_OWNER_ID, db_path)
    assert seed_default_sources(db_path) == 0
    assert not store.has_data(DEFAULT_OWNER_ID, db_path)


def test_seed_ignores_non_string_plugin_urls(db_path):
    store.add_source(SourceCreate(name="Custom", config={"url": ["custom"]}), db_path)
    assert seed_default_sources(db_path) == len(DEFAULT_SOURCES)


def test_generation_empty_filter_owner_scope_and_network_lock(db_path):
    first, _ = add_item(db_path)
    second, _ = add_item(db_path, ACCOUNT)
    plugin = Mock(key="rss-atom")
    plugin.fetch.return_value = [ChangeInput(external_id="fetch", title="Fetched", published_at=PUBLISHED)]
    manager = PluginManager([plugin])
    window = TimeWindow(start=PUBLISHED - timedelta(days=1), end=PUBLISHED + timedelta(days=1))
    assert generate_changes(window, [], db_path=db_path, plugin_manager=manager).changes == []
    plugin.fetch.assert_not_called()
    result = generate_changes(window, db_path=db_path, plugin_manager=manager, owner_id=ACCOUNT)
    assert not result.errors
    assert {change.source_id for change in result.changes} == {second.id}
    assert [call.args[0].id for call in plugin.fetch.call_args_list] == [second.id]
    plugin.fetch.reset_mock()
    result = generate_changes(window, db_path=db_path, plugin_manager=manager, owner_id=None)
    assert not result.errors
    assert {call.args[0].id for call in plugin.fetch.call_args_list} == {first.id, second.id}

    entered, release = Event(), Event()

    def fetch(source, window):
        entered.set()
        assert release.wait(10)
        return []

    plugin.fetch.side_effect = fetch
    with ThreadPoolExecutor(max_workers=2) as executor:
        generation = executor.submit(generate_changes, window, [first.id], db_path=db_path, plugin_manager=manager)
        try:
            assert entered.wait(10)
            read = executor.submit(store.list_sources, db_path=db_path)
            assert read.result(timeout=10)[0].id == first.id
        finally:
            release.set()
        assert not generation.result(timeout=10).errors


def test_concurrent_transactions_keep_ids_and_profiles_consistent(db_path):
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(store.add_source, SourceCreate(name=f"Feed {index}"), db_path, owner_id=ACCOUNT)
            for index in range(12)
        ]
        sources = [future.result(timeout=30) for future in futures]
    assert len({source.id for source in sources}) == 12
    assert store.list_profiles(ACCOUNT, db_path)[0].source_count == 12


def test_cli_explicit_owner_and_duckdb_help(db_path, monkeypatch):
    monkeypatch.setenv("CHANGELORG_DB", str(db_path))
    runner = CliRunner()
    result = runner.invoke(app, ["--owner", ACCOUNT, "sources", "add-rss", "Feed", "https://example.com"])
    assert result.exit_code == 0, result.output
    source = store.list_sources(db_path=db_path, owner_id=ACCOUNT)[0]
    assert store.list_sources(db_path=db_path) == []
    assert runner.invoke(app, ["sources", "remove", str(source.id)]).exit_code != 0
    assert runner.invoke(app, ["--owner", ACCOUNT, "sources", "disable", str(source.id)]).exit_code == 0
    assert not store.get_source(source.id, db_path, owner_id=ACCOUNT).enabled
    result = runner.invoke(app, ["--owner", ACCOUNT, "sources", "list", "--json"])
    assert json.loads(result.output)[0]["owner_id"] == ACCOUNT
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "DuckDB" in result.output
