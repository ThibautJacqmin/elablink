"""Inspect, extract and create local ZIP files without changing the server.

@author Thibaut Jacqmin
"""

from concurrent.futures import CancelledError
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


_CHUNK_SIZE = 1024 * 1024
_MAX_ENTRIES = 10_000
_MAX_BYTES = 4 * 1024 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_PATH_DEPTH = 64
_MAX_TREE_NODES = 50_000
_RESERVED_NAME = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$", re.I)


def _limit(value, name):
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
        raise ValueError(f"{name} must be a positive integer or None")
    return value


def _checked_entries(archive, *, max_entries, max_bytes, max_file_bytes):
    entries = archive.infolist()
    if max_entries is not None and len(entries) > max_entries:
        raise ValueError(f"This ZIP has too many entries (maximum {max_entries}).")
    total = 0
    nodes = set()
    for entry in entries:
        if len(entry.filename) > 4096:
            raise ValueError("This ZIP contains a folder path that is too long to browse safely.")
        parts = entry.filename.rstrip("/").split("/")
        if len(parts) > _MAX_PATH_DEPTH:
            raise ValueError("This ZIP contains a folder path that is too deep to browse safely.")
        for index in range(1, len(parts) + 1):
            nodes.add("/".join(parts[:index]))
            if len(nodes) > _MAX_TREE_NODES:
                raise ValueError("This ZIP contains too many folders and files to browse safely.")
        if not entry.is_dir():
            if max_file_bytes is not None and entry.file_size > max_file_bytes:
                raise ValueError("A ZIP entry exceeds the permitted uncompressed file size.")
            total += entry.file_size
            if max_bytes is not None and total > max_bytes:
                raise ValueError("This ZIP exceeds the permitted total uncompressed size.")
    return entries


def list_zip(filepath, *, max_entries=_MAX_ENTRIES, max_bytes=_MAX_BYTES, max_file_bytes=_MAX_FILE_BYTES):
    """List a ZIP within entry-count and uncompressed-size limits."""
    limits = {"max_entries": _limit(max_entries, "max_entries"), "max_bytes": _limit(max_bytes, "max_bytes"),
              "max_file_bytes": _limit(max_file_bytes, "max_file_bytes")}
    with ZipFile(filepath) as archive:
        return [
            {"name": entry.filename, "size": entry.file_size,
             "compressed_size": entry.compress_size, "is_dir": entry.is_dir()}
            for entry in _checked_entries(archive, **limits)
        ]


def _relative_path(entry):
    name = entry.filename
    parts = name.rstrip("/").split("/")
    mode = stat.S_IFMT(entry.external_attr >> 16)
    if (
        entry.orig_filename != name
        or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
        or (mode == stat.S_IFDIR and not entry.is_dir())
        or any(
            not part or part in (".", "..") or part.endswith((" ", "."))
            or any(character in '<>:"\\|?*' or ord(character) < 32 for character in part)
            or _RESERVED_NAME.match(part.split(".")[0].rstrip())
            for part in parts
        )
    ):
        raise ValueError(f"Unsafe ZIP entry: {name!r}")
    if entry.flag_bits & 1:
        raise ValueError("Password-protected ZIP archives are not supported")
    return Path(*parts)


def _check_destination(root, relative, is_directory):
    target = root
    for index, part in enumerate(relative.parts):
        target /= part
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError(f"The extraction path contains a symbolic link: {relative}")
        directory = index < len(relative.parts) - 1 or is_directory
        if target.exists() and (not directory or not target.is_dir()):
            raise FileExistsError(f"Extraction would overwrite {target}")
    return target


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError("Extraction cancelled")


def extract_zip(filepath, directory, *, cancel=None, progress=None, max_entries=_MAX_ENTRIES,
                max_bytes=_MAX_BYTES, max_file_bytes=_MAX_FILE_BYTES):
    """Extract a ZIP to a local directory, refusing unsafe paths and overwrites.

    Validate every entry before creating any files. Existing directories may be
    reused; existing files are never replaced. Cancellation or an I/O error can
    leave successfully extracted files in place, but removes the incomplete file.
    Defaults allow 10,000 entries, 4 GiB in total and 2 GiB per file. Size/count
    limits accept explicit overrides, or None, for trusted archives.
    """
    root = Path(directory).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(root)
    _cancelled(cancel)
    limits = {"max_entries": _limit(max_entries, "max_entries"), "max_bytes": _limit(max_bytes, "max_bytes"),
              "max_file_bytes": _limit(max_file_bytes, "max_file_bytes")}
    with ZipFile(filepath) as archive:
        planned = []
        names = {}
        for entry in _checked_entries(archive, **limits):
            _cancelled(cancel)
            relative = _relative_path(entry)
            key = relative.as_posix().casefold()
            if key in names:
                raise ValueError(f"Several ZIP entries use the same path: {entry.filename!r}")
            names[key] = entry.is_dir()
            target = _check_destination(root, relative, entry.is_dir())
            planned.append((entry, relative, target))
        for entry, relative, target in planned:
            for parent in relative.parents:
                if names.get(parent.as_posix().casefold()) is False:
                    raise ValueError(f"A ZIP file is also used as a directory: {parent}")

        total = sum(entry.file_size for entry, _, _ in planned if not entry.is_dir())
        existing = root
        while not existing.exists():
            existing = existing.parent
        if total > shutil.disk_usage(existing).free:
            raise OSError("There is not enough free disk space to extract this ZIP.")
        done = 0
        extracted = []
        root.mkdir(parents=True, exist_ok=True)
        for entry, relative, target in planned:
            _cancelled(cancel)
            _check_destination(root, relative, entry.is_dir())
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if progress:
                progress(done, total, entry.filename)
            # Opening exclusively also protects files created after validation.
            with target.open("xb") as output:
                try:
                    file_bytes = 0
                    with archive.open(entry) as source:
                        while True:
                            _cancelled(cancel)
                            block = source.read(_CHUNK_SIZE)
                            if not block:
                                break
                            file_bytes += len(block)
                            if max_file_bytes is not None and file_bytes > max_file_bytes:
                                raise ValueError("A ZIP entry exceeds the permitted uncompressed file size.")
                            if max_bytes is not None and done + len(block) > max_bytes:
                                raise ValueError("ZIP extraction exceeds the permitted total uncompressed size.")
                            output.write(block)
                            done += len(block)
                            if progress:
                                progress(done, total, entry.filename)
                except BaseException:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise
            extracted.append(target)
        return extracted


def create_zip(paths, destination, *, overwrite=False, cancel=None, progress=None):
    """Save selected local files as one flat ZIP, committing only a complete archive.

    Sources are left untouched. Existing destinations require explicit overwrite;
    cancellation or failure removes the temporary archive and keeps the old file.
    """
    paths = [Path(path).expanduser().resolve() for path in paths]
    destination = Path(destination).expanduser().resolve()
    if not paths:
        raise ValueError("Select at least one file to export.")
    names = set()
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        _relative_path(ZipInfo(path.name))
        key = path.name.casefold()
        if key in names:
            raise ValueError(f"Several selected files are named {path.name!r}; save them separately.")
        names.add(key)
        if path == destination:
            raise ValueError("The ZIP destination cannot replace one of its source files.")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    _cancelled(cancel)
    total = sum(path.stat().st_size for path in paths)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".elablink-", suffix=".zip.part", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        done = 0
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for path in paths:
                _cancelled(cancel)
                if progress:
                    progress(done, total, path.name)
                with path.open("rb") as source, archive.open(path.name, "w", force_zip64=True) as output:
                    while True:
                        _cancelled(cancel)
                        block = source.read(_CHUNK_SIZE)
                        if not block:
                            break
                        output.write(block)
                        done += len(block)
                        if progress:
                            progress(done, total, path.name)
        _cancelled(cancel)
        if overwrite:
            os.replace(temporary, destination)
        elif os.name == "nt":
            os.rename(temporary, destination)
        else:
            # A hard link commits without replacing a concurrently created file.
            os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
