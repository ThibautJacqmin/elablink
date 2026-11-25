"""Local paths that must be checked for links before resolving them."""

from pathlib import Path
import stat
import sys


def absolute_storage_path(value: str | Path) -> Path:
    """Expand a path, following only macOS's standard root-owned storage aliases."""
    path = Path(value).expanduser().absolute()
    if sys.platform != "darwin" or path.parts[:2] not in (("/", "tmp"), ("/", "var")):
        return path

    alias = Path("/", path.parts[1])
    target = Path("/private", alias.name)
    try:
        info = alias.lstat()
        if stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and alias.parent / alias.readlink() == target:
            return target.joinpath(*path.parts[2:])
    except FileNotFoundError:
        pass
    return path
