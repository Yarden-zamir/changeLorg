from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Protocol

from changelorg.config import plugin_dirs
from changelorg.models import ChangeInput, PluginInfo, Source, TimeWindow


class PluginError(Exception):
    """Base error for plugin problems."""


class PluginConfigError(PluginError):
    """Raised when a source config is invalid for a plugin."""


class SourcePlugin(Protocol):
    key: str
    name: str
    description: str
    config_schema: dict[str, object]

    def fetch(self, source: Source, window: TimeWindow) -> list[ChangeInput]:
        """Fetch changes for a source in the requested window."""


def plugin_info(plugin: SourcePlugin) -> PluginInfo:
    return PluginInfo(
        key=plugin.key,
        name=plugin.name,
        description=getattr(plugin, "description", ""),
        config_schema=getattr(plugin, "config_schema", {}),
    )


class PluginManager:
    def __init__(self, plugins: Sequence[SourcePlugin] | None = None) -> None:
        self._plugins: dict[str, SourcePlugin] = {}
        for plugin in plugins or []:
            self.register(plugin)

    def register(self, plugin: SourcePlugin) -> None:
        key = getattr(plugin, "key", "")
        fetch = getattr(plugin, "fetch", None)
        if not key:
            raise PluginError("plugin key is required")
        if not callable(fetch):
            raise PluginError(f"plugin {key} must provide a fetch(source, window) method")
        self._plugins[key] = plugin

    def get(self, key: str) -> SourcePlugin:
        try:
            return self._plugins[key]
        except KeyError as exc:
            raise PluginError(f"plugin {key!r} is not registered") from exc

    def list(self) -> Sequence[PluginInfo]:
        return [plugin_info(plugin) for plugin in sorted(self._plugins.values(), key=lambda item: item.key)]


def _load_module(path: Path) -> ModuleType:
    module_name = f"changelorg_local_plugin_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"could not load plugin module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin_from_module(module: ModuleType, path: Path) -> SourcePlugin:
    if hasattr(module, "get_plugin"):
        plugin = module.get_plugin()
    elif hasattr(module, "plugin"):
        plugin = module.plugin
    else:
        raise PluginError(f"{path} must expose plugin or get_plugin()")
    return plugin


def load_local_plugins(directories: list[Path] | None = None) -> list[SourcePlugin]:
    loaded: list[SourcePlugin] = []
    for directory in directories or plugin_dirs():
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise PluginError(f"plugin path {directory} is not a directory")
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module = _load_module(path)
            loaded.append(_plugin_from_module(module, path))
    return loaded


def default_plugin_manager() -> PluginManager:
    from changelorg.plugins.html_news import HtmlNewsPlugin
    from changelorg.plugins.rss_atom import RssAtomPlugin
    from changelorg.plugins.x import XPlugin

    manager = PluginManager([RssAtomPlugin(), HtmlNewsPlugin(), XPlugin()])
    for plugin in load_local_plugins():
        manager.register(plugin)
    return manager
