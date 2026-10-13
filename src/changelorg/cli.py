from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from changelorg import remote
from changelorg.config import plugin_dirs
from changelorg.models import (
    DEFAULT_OWNER_ID,
    Change,
    SourceCreate,
    SourceUpdate,
    utc_now,
    validate_owner_id,
)
from changelorg.plugin import default_plugin_manager
from changelorg.service import generate_changes
from changelorg.store import (
    add_source,
    delete_source,
    init_db,
    list_changes,
    list_sources,
    update_source,
)
from changelorg.timeutils import parse_window

app = typer.Typer(help="Track changelog/news sources and generate time-windowed change feeds.")
sources_app = typer.Typer(help="Manage subscribed sources.")
changes_app = typer.Typer(help="Generate and list changes.")
app.add_typer(sources_app, name="sources")
app.add_typer(changes_app, name="changes")
auth_app = typer.Typer(help="Give this machine read access to your account on a changelorg server.")
app.add_typer(auth_app, name="auth")
ServerOption = Annotated[
    str | None,
    typer.Option("--server", help=f"Server origin. Defaults to CHANGELORG_SERVER, then {remote.DEFAULT_SERVER}."),
]
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
    ctx: typer.Context,
    db: Annotated[Path | None, typer.Option("--db", help="DuckDB path. Existing SQLite files migrate once without a backup.")] = None,
    owner: Annotated[str, typer.Option("--owner", help="Source owner: github:<numeric> or anon:<sha256>.")] = DEFAULT_OWNER_ID,
) -> None:
    try:
        ctx.obj = {"owner_id": validate_owner_id(owner)}
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--owner") from exc
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
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Human-readable source name.")],
    plugin: Annotated[str, typer.Option("--plugin", "-p", help="Plugin key.")] = "rss-atom",
    config: Annotated[list[str], typer.Option("--config", "-c", help="Plugin config KEY=VALUE.")] = [],
) -> None:
    source = add_source(SourceCreate(name=name, plugin=plugin, config=_parse_config(config)), owner_id=ctx.obj["owner_id"])
    console.print(f"Added source {source.id}: {source.name}")


@sources_app.command("add-rss")
def add_rss(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Human-readable source name.")],
    url: Annotated[str, typer.Argument(help="RSS or Atom feed URL.")],
) -> None:
    source = add_source(SourceCreate(name=name, plugin="rss-atom", config={"url": url}), owner_id=ctx.obj["owner_id"])
    console.print(f"Added RSS/Atom source {source.id}: {source.name}")


@sources_app.command("list")
def list_(
    ctx: typer.Context,
    enabled: Annotated[bool | None, typer.Option(help="Filter by enabled state.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    sources = list_sources(enabled=enabled, owner_id=ctx.obj["owner_id"])
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
def remove(ctx: typer.Context, source_id: Annotated[int, typer.Argument(help="Source ID.")]) -> None:
    try:
        delete_source(source_id, owner_id=ctx.obj["owner_id"])
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Removed source {source_id}")


@sources_app.command("enable")
def enable(ctx: typer.Context, source_id: Annotated[int, typer.Argument(help="Source ID.")]) -> None:
    try:
        source = update_source(source_id, SourceUpdate(enabled=True), owner_id=ctx.obj["owner_id"])
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Enabled source {source.id}: {source.name}")


@sources_app.command("disable")
def disable(ctx: typer.Context, source_id: Annotated[int, typer.Argument(help="Source ID.")]) -> None:
    try:
        source = update_source(source_id, SourceUpdate(enabled=False), owner_id=ctx.obj["owner_id"])
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Disabled source {source.id}: {source.name}")


@changes_app.command("generate")
def generate(
    ctx: typer.Context,
    since: Annotated[str, typer.Option(help="Duration like 7d or ISO datetime/date.")] = "7d",
    until: Annotated[str | None, typer.Option(help="ISO datetime/date; defaults to now.")] = None,
    source: Annotated[list[int] | None, typer.Option("--source", "-s", help="Source ID filter.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum changes to return.", min=1, max=500)] = 100,
    json_output: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    window = parse_window(since=since, until=until)
    result = generate_changes(window=window, source_ids=source, limit=limit, owner_id=ctx.obj["owner_id"])
    if json_output:
        _dump_json(result.model_dump(mode="json"))
        return
    _print_changes(result.changes, title="Generated Changes")
    for error in result.errors:
        console.print(f"[red]Source {error.source_id} ({error.source_name}) failed:[/red] {error.message}")


@changes_app.command("list")
def list_cached(
    ctx: typer.Context,
    since: Annotated[str | None, typer.Option(help="Duration like 7d or ISO datetime/date.")] = None,
    until: Annotated[str | None, typer.Option(help="ISO datetime/date; defaults to now.")] = None,
    source: Annotated[list[int] | None, typer.Option("--source", "-s", help="Source ID filter.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum changes to return.", min=1, max=500)] = 100,
    json_output: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    window = parse_window(since=since, until=until) if since is not None or until is not None else None
    changes = list_changes(window=window, source_ids=source, limit=limit, owner_id=ctx.obj["owner_id"])
    if json_output:
        _dump_json([change.model_dump(mode="json") for change in changes])
        return
    _print_changes(changes, title="Cached Changes")


def _print_changes(changes: list[Change], title: str) -> None:
    table = Table(title=title)
    table.add_column("Published")
    table.add_column("Source")
    table.add_column("Title")
    table.add_column("URL")
    for change in changes:
        table.add_row(
            change.published_at.strftime("%Y-%m-%d %H:%M"),
            change.source_name,
            change.title,
            change.url or "",
        )
    console.print(table)


def _access(scope: remote.Scope) -> str:
    return "read and write" if scope == "write" else "read only"


def _remote_failure(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)


@auth_app.command("login")
def auth_login(
    server: ServerOption = None,
    name: Annotated[str | None, typer.Option("--name", help="Client name on the approval page.")] = None,
    write: Annotated[bool, typer.Option("--write", help="Ask for read and write access instead of read only.")] = False,
) -> None:
    """Ask for approval in the browser, then save the token."""
    client_name = (name or f"changelorg CLI on {socket.gethostname()}")[:100]
    scope: remote.Scope = "write" if write else "read"
    try:
        origin = remote.server_origin(server)
        credentials = remote.load_credentials()
        with remote.client(origin) as http:
            device = remote.start_login(http, client_name, scope)
            typer.echo(f"Open this link in your browser: {device.verification_uri_complete}")
            typer.echo(f"Check that the page shows the code {device.user_code} and {_access(scope)} access, then approve.")
            typer.echo(f"Waiting for approval. The link expires in {device.expires_in // 60} minutes.")
            grant = remote.wait_for_token(http, device)
        credentials.servers[origin] = remote.SavedToken(
            token=grant.access_token,
            scope=grant.scope,
            owner_id=grant.owner_id,
            login=grant.login,
            name=client_name,
            saved_at=utc_now(),
        )
        path = remote.save_credentials(credentials)
    except remote.RemoteError as exc:
        _remote_failure(str(exc))
    except httpx.HTTPError:
        _remote_failure("Cannot reach the server. Check the connection and the --server value.")
    typer.echo(f"Approved for {grant.login} ({grant.owner_id}) with {_access(grant.scope)} access. The token is saved in {path}.")


@auth_app.command("status")
def auth_status(server: ServerOption = None) -> None:
    """Check the saved token. Exit 0 when it works, else exit 1."""
    try:
        origin = remote.server_origin(server)
        saved = remote.load_credentials().servers.get(origin)
        if saved is None:
            _remote_failure(f"Not signed in to {origin}. Run changelorg auth login.")
        with remote.client(origin) as http:
            identity = remote.request_json(http, saved.token, "/me")
    except remote.RemoteError as exc:
        _remote_failure(str(exc))
    except httpx.HTTPError:
        _remote_failure("Cannot reach the server. Check the connection and the --server value.")
    if not isinstance(identity, dict) or identity.get("id") != saved.owner_id:
        _remote_failure("The server returned a different account for the saved token. Run changelorg auth login.")
    typer.echo(f"Signed in to {origin} as {saved.login} ({saved.owner_id}) with {_access(saved.scope)} access.")


@auth_app.command("logout")
def auth_logout(server: ServerOption = None) -> None:
    """Revoke the saved token on the server, then delete it locally."""
    try:
        origin = remote.server_origin(server)
        credentials = remote.load_credentials()
        saved = credentials.servers.get(origin)
        if saved is None:
            typer.echo(f"Not signed in to {origin}.")
            return
        with remote.client(origin) as http:
            remote.revoke(http, saved.token)
        del credentials.servers[origin]
        remote.save_credentials(credentials)
    except remote.RemoteError as exc:
        _remote_failure(str(exc))
    except httpx.HTTPError:
        _remote_failure("Cannot reach the server, so the token is still valid. Retry, or revoke it in the account menu.")
    typer.echo(f"Signed out of {origin}. The server no longer accepts the token.")


@app.command("api")
def api_get(
    path: Annotated[str, typer.Argument(help="Server path, for example '/changes?profile=dev&since=10d'.")],
    server: ServerOption = None,
    method: Annotated[str, typer.Option("--method", "-X", help="GET, POST, PATCH, or DELETE.")] = "GET",
    data: Annotated[str | None, typer.Option("--data", "-d", help="JSON request body.")] = None,
) -> None:
    """Send an authenticated request to the server and print the JSON response."""
    verb = method.upper()
    if verb not in {"GET", "POST", "PATCH", "DELETE"}:
        _remote_failure("The method must be GET, POST, PATCH, or DELETE.")
    try:
        body = None if data is None else json.loads(data)
    except json.JSONDecodeError:
        _remote_failure("--data must be valid JSON.")
    try:
        origin = remote.server_origin(server)
        saved = remote.load_credentials().servers.get(origin)
        if saved is None:
            _remote_failure(f"Not signed in to {origin}. Run changelorg auth login.")
        with remote.client(origin) as http:
            value = remote.request_json(http, saved.token, path, method=verb, body=body)
    except remote.RemoteError as exc:
        _remote_failure(str(exc))
    except httpx.HTTPError:
        _remote_failure("Cannot reach the server. Check the connection and the --server value.")
    if value is None:
        typer.echo("Done. The server returned no content.")
        return
    _dump_json(value)
