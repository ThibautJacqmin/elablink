"""Launch the attachment browser independently of the calling Python process.

@author Thibaut Jacqmin
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from .config import browser_storage_directory, resolve_browser_folder


def _require_browser_dependencies():
    # Inspect metadata without importing Qt into a running notebook or Spyder.
    for package in ("PyQt6", "pypdfium2"):
        try:
            version(package)
        except PackageNotFoundError as error:
            raise ImportError(
                f"{package} is missing from this Python environment. "
                "Install this eLabLink prerelease with its elablink[browser] extra."
            ) from error


def main(argv=None):
    """Start the experimental attachment browser in the current process."""
    _require_browser_dependencies()
    from ._attachment_browser_qt import main as run_browser

    return run_browser(argv)


def browse_files(entity, folder=None) -> subprocess.Popen:
    """Open a PyQt6 window for one experiment or resource and return immediately.

    The child process reads the same configuration file and owns its API client.
    No API keys or live Python objects are passed on the command line.
    """
    _require_browser_dependencies()
    if entity.entity_type not in ("experiments", "items"):
        raise ValueError("The attachment browser needs an experiment or resource")
    if not isinstance(entity.ID, int) or isinstance(entity.ID, bool) or entity.ID <= 0:
        raise ValueError("Save or load the record before browsing its attachments")
    configuration = Path(entity.api.config_path).expanduser().resolve()
    if not configuration.is_file():
        raise FileNotFoundError(f"eLabLink configuration file not found: {configuration}")
    folder = resolve_browser_folder(folder)

    executable = Path(sys.executable)
    if sys.platform == "win32":
        windowed = executable.with_name("pythonw.exe")
        if windowed.is_file():
            executable = windowed
    command = [
        str(executable), "-I", "-m", "elablink._attachment_browser_qt",
        "--config-path", str(configuration),
        "--entity-type", entity.entity_type, "--entity-id", str(entity.ID),
    ]
    if folder is not None:
        command.extend(["--folder", str(folder)])
    return _launch_window(command, "browser")


def open_h5viewer(filepath) -> subprocess.Popen:
    """Open one downloaded Labmate H5 file in an independent optional viewer."""
    if find_spec("h5viewer") is None:
        raise ImportError(
            "Opening H5 files requires h5viewer in this Python environment. "
            "Install a version compatible with current Labmate; for a local "
            "checkout use: python -m pip install -e <path-to-h5viewer>"
        )
    filepath = Path(filepath).expanduser().resolve()
    if not filepath.is_file():
        raise FileNotFoundError(filepath)
    executable = Path(sys.executable)
    if sys.platform == "win32":
        windowed = executable.with_name("pythonw.exe")
        if windowed.is_file():
            executable = windowed
    command = [
        str(executable), "-I", "-c", "from h5viewer.main import main; main()", str(filepath),
    ]
    return _launch_window(command, "h5viewer", cwd=str(filepath.parent))


def _find_klayout() -> str | None:
    """Find the desktop application without importing its optional Python package."""
    candidates = []
    if sys.platform == "win32":
        for variable, fallback in (("ProgramFiles", r"C:\Program Files"),
                                   ("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            directory = Path(os.environ.get(variable) or fallback) / "KLayout"
            candidates.extend(directory / name for name in ("klayout_app.exe", "klayout.exe"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            for directory in (Path(local) / "KLayout", Path(local) / "Programs" / "KLayout"):
                candidates.extend(directory / name for name in ("klayout_app.exe", "klayout.exe"))
    elif sys.platform == "darwin":
        for directory in (Path("/Applications"), Path.home() / "Applications"):
            candidates.append(directory / "klayout.app" / "Contents" / "MacOS" / "klayout")
    installed = next((str(path) for path in candidates if path.is_file()), None)
    if installed is not None:
        return installed
    search_path = os.pathsep.join(
        entry for entry in os.get_exec_path()
        if entry and Path(entry).is_absolute() and Path(entry).resolve() != Path.cwd().resolve()
    )
    for name in ("klayout_app", "klayout"):
        executable = shutil.which(name, path=search_path)
        if executable and Path(executable).resolve().parent != Path.cwd().resolve():
            return executable
    return None


def open_klayout(filepath) -> subprocess.Popen:
    """Open a local GDS file in the installed KLayout desktop application."""
    filepath = Path(filepath).expanduser().resolve()
    if not filepath.is_file():
        raise FileNotFoundError(filepath)
    executable = _find_klayout()
    if executable is None:
        raise ImportError(
            "Opening GDS files requires the KLayout desktop application. "
            "Install KLayout or add its executable to PATH."
        )
    return _launch_window([executable, str(filepath)], "klayout", cwd=str(filepath.parent))


def _launch_window(command, log_name, *, cwd=None) -> subprocess.Popen:
    """Keep desktop windows independent and retain startup errors in a log."""
    log_directory = browser_storage_directory("browser-logs")
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / f"{log_name}-{uuid.uuid4().hex}.log"
    options = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 1  # SW_SHOWNORMAL, even when the caller is hidden.
        options["startupinfo"] = startup
    with log_path.open("ab") as error_log:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=error_log, cwd=cwd, **options,
        )
    process.browser_log_path = log_path
    return process
