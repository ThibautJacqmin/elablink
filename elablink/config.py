"""Locate private eLabFTW configuration without notebook-specific paths.

@author Thibaut Jacqmin
"""

import os
from pathlib import Path
import sys
import tempfile

from ._paths import absolute_storage_path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _existing_file(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"eLabLink configuration file not found: {path}")
    return path


def _project_settings(directory: Path) -> dict | None:
    project = directory / "pyproject.toml"
    if not project.is_file():
        return None
    with project.open("rb") as stream:
        try:
            data = tomllib.load(stream)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Invalid TOML in {project}") from exc
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise ValueError(f"The tool table in {project} must be a table")
    if "elablink" not in tool:
        return None
    settings = tool["elablink"]
    if not isinstance(settings, dict):
        raise ValueError(f"[tool.elablink] in {project} must be a table")
    return settings


def _project_config(directory: Path) -> Path | None:
    settings = _project_settings(directory)
    if settings is None or set(settings) == {"browser_folder"}:
        return None
    project = directory / "pyproject.toml"
    configured = settings.get("config_path")
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError(f"Set config_path to a file path in [tool.elablink] in {project}")
    path = Path(configured).expanduser()
    return _existing_file(path if path.is_absolute() else directory / path)


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """Return the absolute configuration path without reading credentials.

    An explicit path is used exactly as supplied, relative to the current working
    directory. Otherwise look for ``[tool.elablink].config_path`` in the nearest
    declaring ``pyproject.toml``, interpreting it relative to that project file.
    A declared path that is missing or invalid is an error, not a reason to
    silently use another server's configuration.

    Without a project setting, look for ``elablink.conf`` in the current directory
    and its parents. An editable checkout's own project setting is the final
    fallback, allowing its examples to run from outside the project as well.
    """
    if config_path is not None:
        return _existing_file(Path(config_path))
    current = Path.cwd().resolve()
    directories = (current, *current.parents)
    for directory in directories:
        configured = _project_config(directory)
        if configured is not None:
            return configured
    for directory in directories:
        candidate = directory / "elablink.conf"
        if candidate.is_file():
            return candidate.resolve()
    configured = _project_config(_PROJECT_ROOT)
    if configured is not None:
        return configured
    raise FileNotFoundError(
        "No eLabLink configuration found. Set config_path in [tool.elablink] in "
        "your project's pyproject.toml, or put elablink.conf in the project root."
    )


def resolve_browser_folder(folder: str | Path | None = None) -> Path | None:
    """Resolve an optional browser working folder without reading credentials.

    Explicit paths are relative to the current directory. Project defaults use
    ``[tool.elablink].browser_folder`` relative to the declaring pyproject.toml.
    No setting leaves the browser using temporary cached copies.
    """
    if folder is not None:
        if not str(folder).strip():
            raise ValueError("Choose a non-empty browser folder path")
        path = absolute_storage_path(folder)
        if path.exists() and not path.is_dir():
            raise NotADirectoryError(path)
        return path
    current = Path.cwd().resolve()
    for directory in (current, *current.parents, _PROJECT_ROOT):
        settings = _project_settings(directory)
        if settings is None or "browser_folder" not in settings:
            continue
        configured = settings["browser_folder"]
        if not isinstance(configured, str) or not configured.strip():
            raise ValueError(f"Set browser_folder to a folder path in [tool.elablink] in {directory / 'pyproject.toml'}")
        path = Path(configured).expanduser()
        return resolve_browser_folder(path if path.is_absolute() else directory / path)
    return None


def browser_storage_directory(area: str) -> Path:
    """Choose a per-user cache root for browser attachments and diagnostic logs."""
    if sys.platform == "win32":
        temporary = Path(tempfile.gettempdir())
        if area == "attachments":
            return temporary / "elablink-attachments"
        return temporary / "elablink" / area
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", ""))
        if not base.is_absolute():
            base = Path.home() / ".cache"
    root = base / "elablink"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / area
