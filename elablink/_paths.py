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


def is_junction(path: Path) -> bool:
    check = getattr(path, "is_junction", None)
    if check is not None:
        return check()
    # Path.is_junction was added in Python 3.12.
    try:
        information = path.lstat()
    except FileNotFoundError:
        return False
    return getattr(information, "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def checked_working_folder(value: str | Path) -> Path:
    """Check every component before returning the canonical working directory."""
    path = absolute_storage_path(value)
    for part in (path, *path.parents):
        if part.is_symlink() or is_junction(part):
            raise ValueError("The working folder cannot contain symbolic links or junctions")
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise NotADirectoryError(path)
    return path
