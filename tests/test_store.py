from datetime import datetime, timezone

from changelorg.models import ChangeInput, ChangeUpdate, SourceCreate, TimeWindow
from changelorg.store import add_source, init_db, list_changes, list_sources, update_change, upsert_changes


def test_source_and_change_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "changelorg.db"
    init_db(db_path)
    source = add_source(SourceCreate(name="GitHub Blog", config={"url": "https://github.blog/changelog/feed/"}), db_path)

    upsert_changes(
        source.id,
        [
            ChangeInput(
                external_id="item-1",
                title="A change",
                url="https://example.com/change",
                published_at=datetime(2026, 7, 4, 10, tzinfo=timezone.utc),
            )
        ],
        db_path,
    )

    sources = list_sources(db_path=db_path)
    changes = list_changes(
        window=TimeWindow(
            start=datetime(2026, 7, 4, 0, tzinfo=timezone.utc),
            end=datetime(2026, 7, 5, 0, tzinfo=timezone.utc),
        ),
        db_path=db_path,
    )

    assert sources[0].name == "GitHub Blog"
    assert changes[0].title == "A change"
    assert changes[0].source_name == "GitHub Blog"


def test_upsert_changes_deduplicates_by_external_id(tmp_path) -> None:
    db_path = tmp_path / "changelorg.db"
    source = add_source(SourceCreate(name="Feed", config={"url": "https://example.com/feed.xml"}), db_path)
    published_at = datetime(2026, 7, 4, 10, tzinfo=timezone.utc)

    upsert_changes(source.id, [ChangeInput(external_id="same", title="Old", published_at=published_at)], db_path)
    upsert_changes(source.id, [ChangeInput(external_id="same", title="New", published_at=published_at)], db_path)

    changes = list_changes(db_path=db_path)

    assert len(changes) == 1
    assert changes[0].title == "New"


def test_update_change_state_survives_upsert(tmp_path) -> None:
    db_path = tmp_path / "changelorg.db"
    source = add_source(SourceCreate(name="Feed", config={"url": "https://example.com/feed.xml"}), db_path)
    published_at = datetime(2026, 7, 4, 10, tzinfo=timezone.utc)
    upsert_changes(source.id, [ChangeInput(external_id="same", title="Old", published_at=published_at)], db_path)
    change = list_changes(db_path=db_path)[0]

    updated = update_change(change.id, ChangeUpdate(saved=True, note="Read later"), db_path)
    upsert_changes(source.id, [ChangeInput(external_id="same", title="New", published_at=published_at)], db_path)
    refreshed = list_changes(db_path=db_path)[0]

    assert updated.saved is True
    assert refreshed.title == "New"
    assert refreshed.saved is True
    assert refreshed.note == "Read later"


def test_list_changes_hides_dismissed_by_default(tmp_path) -> None:
    db_path = tmp_path / "changelorg.db"
    source = add_source(SourceCreate(name="Feed", config={"url": "https://example.com/feed.xml"}), db_path)
    published_at = datetime(2026, 7, 4, 10, tzinfo=timezone.utc)
    upsert_changes(source.id, [ChangeInput(external_id="same", title="Item", published_at=published_at)], db_path)
    change = list_changes(db_path=db_path)[0]

    update_change(change.id, ChangeUpdate(dismissed=True), db_path)

    assert list_changes(db_path=db_path) == []
    assert len(list_changes(include_dismissed=True, db_path=db_path)) == 1


def test_dismissed_change_stays_hidden_after_refetch(tmp_path) -> None:
    db_path = tmp_path / "changelorg.db"
    source = add_source(SourceCreate(name="Feed", config={"url": "https://example.com/feed.xml"}), db_path)
    published_at = datetime(2026, 7, 4, 10, tzinfo=timezone.utc)
    upsert_changes(source.id, [ChangeInput(external_id="same", title="Old", published_at=published_at)], db_path)
    change = list_changes(db_path=db_path)[0]

    update_change(change.id, ChangeUpdate(dismissed=True), db_path)
    upsert_changes(source.id, [ChangeInput(external_id="same", title="New", published_at=published_at)], db_path)

    assert list_changes(db_path=db_path) == []
    dismissed = list_changes(include_dismissed=True, db_path=db_path)
    assert dismissed[0].title == "New"
    assert dismissed[0].dismissed is True
