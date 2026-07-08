from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from changelorg.config import plugin_dirs
from changelorg.models import SourceCreate, SourceUpdate
from changelorg.plugin import default_plugin_manager
from changelorg.service import generate_changes
from changelorg.store import add_source, delete_source, init_db, list_changes, list_sources, update_source
from changelorg.timeutils import parse_window


app = typer.Typer(help="Track changelog/news sources and generate time-windowed change feeds.")
sources_app = typer.Typer(help="Manage subscribed sources.")
changes_app = typer.Typer(help="Generate and list changes.")
app.add_typer(sources_app, name="sources")
app.add_typer(changes_app, name="changes")
console = Console()


def _dump_json(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


def _parse_config(pairs: list[str]) -> dict[str, str | list[str]]:
    config: dict[str, str | list[str]] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"config must be KEY=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter("config key cannot be empty")
        existing = config.get(key)
        if existing is None:
            config[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            config[key] = [existing, value]
    return config


@app.callback()
def main(
    db: Annotated[Path | None, typer.Option("--db", help="SQLite database path.")] = None,
) -> None:
    if db is not None:
        os.environ["CHANGELORG_DB"] = str(db.expanduser())


@app.command("init")
def init() -> None:
    path = init_db()
    console.print(f"Initialized database at {path}")


@app.command("plugins")
def plugins(json_output: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False) -> None:
    manager = default_plugin_manager()
    infos = [info.model_dump() for info in manager.list()]
    if json_output:
        _dump_json(infos)
        return

    table = Table(title="Plugins")
    table.add_column("Key")
    table.add_column("Name")
    table.add_column("Description")
    for info in manager.list():
        table.add_row(info.key, info.name, info.description)
    console.print(table)
    console.print("Plugin dirs: " + ", ".join(str(path) for path in plugin_dirs()))


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="Host to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Enable development reload.")] = False,
) -> None:
    init_db()
    uvicorn.run("changelorg.api:app", host=host, port=port, reload=reload)


@sources_app.command("add")
def add(
    name: Annotated[str, typer.Argument(help="Human-readable source name.")],
    plugin: Annotated[str, typer.Option("--plugin", "-p", help="Plugin key.")] = "rss-atom",
    config: Annotated[list[str], typer.Option("--config", "-c", help="Plugin config KEY=VALUE.")] = [],
) -> None:
    source = add_source(SourceCreate(name=name, plugin=plugin, config=_parse_config(config)))
    console.print(f"Added source {source.id}: {source.name}")


@sources_app.command("add-rss")
def add_rss(
    name: Annotated[str, typer.Argument(help="Human-readable source name.")],
    url: Annotated[str, typer.Argument(help="RSS or Atom feed URL.")],
) -> None:
    source = add_source(SourceCreate(name=name, plugin="rss-atom", config={"url": url}))
    console.print(f"Added RSS/Atom source {source.id}: {source.name}")


@sources_app.command("list")
def list_(
    enabled: Annotated[bool | None, typer.Option(help="Filter by enabled state.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    sources = list_sources(enabled=enabled)
    if json_output:
        _dump_json([source.model_dump(mode="json") for source in sources])
        return

    table = Table(title="Sources")
    table.add_column("ID", justify="right")
    table.add_column("Name")
    table.add_column("Plugin")
    table.add_column("Enabled")
    table.add_column("Config")
    for source in sources:
        table.add_row(str(source.id), source.name, source.plugin, str(source.enabled), json.dumps(source.config))
    console.print(table)


@sources_app.command("remove")
def remove(source_id: Annotated[int, typer.Argument(help="Source ID.")]) -> None:
    try:
        delete_source(source_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Removed source {source_id}")


@sources_app.command("enable")
def enable(source_id: Annotated[int, typer.Argument(help="Source ID.")]) -> None:
    try:
        source = update_source(source_id, SourceUpdate(enabled=True))
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Enabled source {source.id}: {source.name}")


@sources_app.command("disable")
def disable(source_id: Annotated[int, typer.Argument(help="Source ID.")]) -> None:
    try:
        source = update_source(source_id, SourceUpdate(enabled=False))
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Disabled source {source.id}: {source.name}")


@changes_app.command("generate")
def generate(
    since: Annotated[str, typer.Option(help="Duration like 7d or ISO datetime/date.")] = "7d",
    until: Annotated[str | None, typer.Option(help="ISO datetime/date; defaults to now.")] = None,
    source: Annotated[list[int] | None, typer.Option("--source", "-s", help="Source ID filter.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum changes to return.", min=1, max=500)] = 100,
    json_output: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    window = parse_window(since=since, until=until)
    result = generate_changes(window=window, source_ids=source, limit=limit)
    if json_output:
        _dump_json(result.model_dump(mode="json"))
        return
    _print_changes(result.changes, title="Generated Changes")
    for error in result.errors:
        console.print(f"[red]Source {error.source_id} ({error.source_name}) failed:[/red] {error.message}")


@changes_app.command("list")
def list_cached(
    since: Annotated[str | None, typer.Option(help="Duration like 7d or ISO datetime/date.")] = None,
    until: Annotated[str | None, typer.Option(help="ISO datetime/date; defaults to now.")] = None,
    source: Annotated[list[int] | None, typer.Option("--source", "-s", help="Source ID filter.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum changes to return.", min=1, max=500)] = 100,
    json_output: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    window = parse_window(since=since, until=until) if since is not None or until is not None else None
    changes = list_changes(window=window, source_ids=source, limit=limit)
    if json_output:
        _dump_json([change.model_dump(mode="json") for change in changes])
        return
    _print_changes(changes, title="Cached Changes")


def _print_changes(changes: list[object], title: str) -> None:
    table = Table(title=title)
    table.add_column("Published")
    table.add_column("Source")
    table.add_column("Title")
    table.add_column("URL")
    for change in changes:
        table.add_row(
            getattr(change, "published_at").strftime("%Y-%m-%d %H:%M"),
            getattr(change, "source_name"),
            getattr(change, "title"),
            getattr(change, "url") or "",
        )
    console.print(table)
