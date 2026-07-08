from __future__ import annotations

from pathlib import Path

from changelorg.models import GenerationError, GenerationResult, TimeWindow
from changelorg.plugin import PluginManager, default_plugin_manager
from changelorg.store import list_changes, list_sources, upsert_changes


def generate_changes(
    window: TimeWindow,
    source_ids: list[int] | None = None,
    limit: int = 100,
    db_path: Path | None = None,
    plugin_manager: PluginManager | None = None,
) -> GenerationResult:
    manager = plugin_manager or default_plugin_manager()
    requested_ids = set(source_ids or [])
    sources = list_sources(enabled=True, db_path=db_path)
    if requested_ids:
        sources = [source for source in sources if source.id in requested_ids]

    errors: list[GenerationError] = []
    for source in sources:
        try:
            plugin = manager.get(source.plugin)
            changes = plugin.fetch(source, window)
            upsert_changes(source.id, changes, db_path=db_path)
        except Exception as exc:
            errors.append(
                GenerationError(
                    source_id=source.id,
                    source_name=source.name,
                    plugin=source.plugin,
                    message=str(exc),
                )
            )

    return GenerationResult(
        window=window,
        changes=list_changes(window=window, source_ids=source_ids, limit=limit, db_path=db_path),
        errors=errors,
    )
