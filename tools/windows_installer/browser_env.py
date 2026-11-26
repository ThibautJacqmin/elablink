"""Install and verify an isolated Windows eLabLink browser before a shortcut switches to it."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


QT_VERSION = "6.12.1"
_LAUNCHER_NAME = "elablink-browser.pyw"
_READY = ".elablink-ready"


# A shortcut starts pythonw without activating Conda. Prepare the private DLL
# directories before importing Qt, OpenSSL or the PDF renderer.
_LAUNCHER = '''from pathlib import Path
import os
import sys

prefix = Path(sys.prefix)
dll_handles = []
if (prefix / "conda-meta").is_dir():
    directories = [prefix, prefix / "Library" / "bin", prefix / "Scripts"]
    directories = [str(path) for path in directories if path.is_dir()]
    os.environ["PATH"] = os.pathsep.join(directories + [os.environ.get("PATH", "")])
    dll_handles = [os.add_dll_directory(path) for path in directories]

# Use the plugins paired with this Qt installation.
os.environ.pop("QT_PLUGIN_PATH", None)
os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)

if sys.argv[1:2] == ["--verify-install"]:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from importlib.metadata import version
    import ssl
    import pypdfium2
    from PyQt6.QtCore import qVersion
    from PyQt6.QtGui import QImageReader
    from PyQt6.QtWidgets import QApplication
    from elablink._attachment_browser_qt import AttachmentBrowser

    expected_app, expected_qt = sys.argv[2:]
    if version("elablink") != expected_app or version("PyQt6") != expected_qt or qVersion() != expected_qt:
        raise RuntimeError("The installed application or Qt runtime has an unexpected version.")
    app = QApplication([])
    if b"svg" not in [bytes(fmt) for fmt in QImageReader.supportedImageFormats()]:
        raise RuntimeError("The Qt SVG image plugin is unavailable.")
    print(f"Verified eLabLink {expected_app} and Qt {qVersion()}.")
else:
    from elablink.attachment_browser import main
    raise SystemExit(main())
'''


def launcher_args(env_dir: Path, config_path: Path) -> list[str]:
    """Arguments for a shortcut targeting this private environment's pythonw.exe."""
    return ["-I", str(env_dir / _LAUNCHER_NAME), "--config-path", str(config_path)]


def _conda_executable() -> Path | None:
    """Determine the bootstrap interpreter's type, independently of shell activation."""
    base = Path(sys.base_prefix)
    if not (base / "conda-meta").is_dir():
        return None
    candidates = [base / "Scripts" / "conda.exe"]
    if base.parent.name.lower() == "envs":
        candidates.append(base.parent.parent / "Scripts" / "conda.exe")
    configured = os.environ.get("CONDA_EXE")
    if configured and Path(configured).is_absolute():
        candidates.append(Path(configured))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Cannot find conda.exe. Start the installer from your Anaconda Prompt.")


def _python(env_dir: Path, is_conda: bool, *, windowed: bool = False) -> Path:
    base = env_dir if is_conda else env_dir / "Scripts"
    return base / ("pythonw.exe" if windowed else "python.exe")


def _build_environment(env_dir: Path, wheel: Path, conda_exe: Path | None) -> None:
    is_conda = conda_exe is not None
    run_in_env: list[str] = []
    if conda_exe is not None:
        subprocess.run([str(conda_exe), "create", "--yes", "--no-default-packages",
                        "--prefix", str(env_dir), "python=3.12", "pip"], check=True)
        run_in_env = [str(conda_exe), "run", "--no-capture-output", "--prefix", str(env_dir)]
    else:
        subprocess.run([sys.executable, "-I", "-m", "venv", str(env_dir)], check=True)

    pip = run_in_env + [str(_python(env_dir, is_conda)), "-I", "-m", "pip",
                        "--disable-pip-version-check"]
    subprocess.run(pip + ["install", "--only-binary=:all:", f"{wheel}[browser]",
                          f"PyQt6=={QT_VERSION}", f"PyQt6-Qt6=={QT_VERSION}"], check=True)
    subprocess.run(pip + ["check"], check=True)
    (env_dir / _LAUNCHER_NAME).write_text(_LAUNCHER, encoding="utf-8")


def _verify_environment(env_dir: Path, version: str, is_conda: bool) -> None:
    """Probe the actual runtime using the same bootstrap as an Explorer shortcut."""
    if not _python(env_dir, is_conda, windowed=True).is_file():
        raise FileNotFoundError("The private Python GUI launcher is missing.")
    launcher = env_dir / _LAUNCHER_NAME
    if launcher.read_text(encoding="utf-8") != _LAUNCHER:
        raise RuntimeError("The installed browser launcher has changed.")
    subprocess.run([str(_python(env_dir, is_conda)), "-I", str(launcher),
                    "--verify-install", version, QT_VERSION], check=True, timeout=60, cwd=env_dir)


def prepare_environment(install_root: Path, wheel: Path) -> tuple[Path, Path, bool]:
    """Return a verified (pythonw.exe, environment directory, is_conda) tuple.

    Each build uses a new directory. Construction and health checks never modify
    an older install, even if it is still running or two installers overlap.
    """
    wheel = Path(wheel).resolve(strict=True)
    match = re.fullmatch(r"elablink-([0-9][A-Za-z0-9.!+_]*)-py3-none-any\.whl", wheel.name)
    if not match:
        raise ValueError("Expected the eLabLink wheel supplied with this installer.")
    version = match.group(1)
    conda_exe = _conda_executable()
    is_conda = conda_exe is not None
    identity = hashlib.sha256(wheel.read_bytes())
    identity.update(f"{QT_VERSION}/{is_conda}/".encode("ascii"))
    identity.update(_LAUNCHER.encode("utf-8"))
    fingerprint = identity.hexdigest()
    slots = Path(install_root).resolve() / "envs"
    slots.mkdir(parents=True, exist_ok=True)
    prefix = f"{fingerprint[:12]}-"
    for candidate in sorted(slots.glob(prefix + "*")):
        try:
            if (candidate / _READY).read_text(encoding="ascii") != fingerprint:
                continue
            _verify_environment(candidate, version, is_conda)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            continue
        return _python(candidate, is_conda, windowed=True), candidate, is_conda

    # Keep a venv at its final path: generated Python launchers contain absolute paths.
    candidate = Path(tempfile.mkdtemp(prefix=prefix, dir=slots))
    try:
        _build_environment(candidate, wheel, conda_exe)
        _verify_environment(candidate, version, is_conda)
        (candidate / _READY).write_text(fingerprint, encoding="ascii")
    except BaseException:
        # Only this attempt's private directory may be discarded.
        if candidate.resolve().parent == slots and candidate.name.startswith(prefix):
            shutil.rmtree(candidate, ignore_errors=True)
        raise
    return _python(candidate, is_conda, windowed=True), candidate, is_conda
