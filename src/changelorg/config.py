from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "changelorg"


def data_dir() -> Path:
    configured = os.environ.get("CHANGELORG_DATA_DIR")
    if configured:
        return Path(configured).expanduser()

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / APP_NAME

    return Path.home() / ".local" / "share" / APP_NAME


def database_path() -> Path:
    configured = os.environ.get("CHANGELORG_DB")
    if configured:
        return Path(configured).expanduser()
    return data_dir() / "changelorg.db"


def plugin_dirs() -> list[Path]:
    dirs: list[Path] = []
    configured = os.environ.get("CHANGELORG_PLUGIN_PATH")
    if configured:
        dirs.extend(Path(part).expanduser() for part in configured.split(os.pathsep) if part)

    dirs.append(Path.cwd() / "plugins")

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg_config_home).expanduser() if xdg_config_home else Path.home() / ".config"
    dirs.append(config_home / APP_NAME / "plugins")

    unique_dirs: list[Path] = []
    seen: set[Path] = set()
    for directory in dirs:
        resolved = directory.resolve() if directory.exists() else directory
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_dirs.append(directory)
    return unique_dirs
