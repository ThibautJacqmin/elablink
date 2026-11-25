"""Background transfers for the attachment browser.

@author Thibaut Jacqmin
"""

from __future__ import annotations

from concurrent.futures import CancelledError
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode

from ._paths import absolute_storage_path
from .client import Client
from .config import browser_storage_directory
from .remote_analysis import RemoteSource, check_source_manifest, read_source, write_source_manifest


_CHUNK_SIZE = 1024 * 1024
_REQUEST_TIMEOUT = (5, 30)
_UPLOAD_TIMEOUT = (300, 300)
_RESERVED_NAME = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$", re.I)


def _junction(path):
    check = getattr(path, "is_junction", None)
    if check is not None:
        return check()
    # Path.is_junction was added in Python 3.12.
    try:
        information = path.lstat()
    except FileNotFoundError:
        return False
    return getattr(information, "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def _filename(value: Any) -> str:
    """Require one portable filename, never a path supplied by the server."""
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or value.endswith((" ", "."))
        or any(character in '<>:"/\\|?*' or ord(character) < 32 for character in value)
        or _RESERVED_NAME.match(value.split(".")[0].rstrip())
    ):
        raise ValueError(f"Invalid attachment filename: {value!r}")
    return value


def _cancelled(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError("Transfer cancelled")


def _size(value: Any) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return 0


class AttachmentStore:
    """Keep an independent API client on the browser's single transfer worker.

    The client is created lazily on the first operation. Cached copies persist
    after the window closes so clipboard and desktop drag operations remain valid.
    """

    def __init__(
        self,
        config_path,
        entity_type: str,
        entity_id: int,
        cache_directory=None,
        client_factory: Callable[[], Any] | None = None,
        *,
        folder=None,
    ) -> None:
        if entity_type not in ("experiments", "items"):
            raise ValueError("entity_type must be 'experiments' or 'items'")
        if isinstance(entity_id, bool) or not isinstance(entity_id, int) or entity_id <= 0:
            raise ValueError("entity_id must be a positive integer")
        self.config_path = config_path
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.cache_directory = Path(
            cache_directory or browser_storage_directory("attachments")
        ).expanduser().resolve()
        self._client_factory = client_factory
        self._client = None
        self._entity = None
        self._owner = None
        self.title = ""
        self.folder = None
        self.set_folder(folder)

    def folder_info(self) -> dict[str, Any]:
        """Return the selected working root; None means temporary cached copies."""
        return {"folder": self.folder}

    def set_folder(self, folder) -> dict[str, Any]:
        """Choose a protected working folder without moving or deleting any files."""
        if self._owner is not None and self._owner != threading.get_ident():
            raise RuntimeError("Use AttachmentStore from its transfer worker only")
        if folder is None:
            self.folder = None
            return self.folder_info()
        if not str(folder).strip():
            raise ValueError("Choose a non-empty working folder path")
        path = absolute_storage_path(folder)
        for part in (path, *path.parents):
            if part.is_symlink() or _junction(part):
                raise ValueError("The working folder cannot contain symbolic links or junctions")
        path = path.resolve()
        if path.exists() and not path.is_dir():
            raise NotADirectoryError(path)
        if path.is_relative_to(self.cache_directory) or self.cache_directory.is_relative_to(path):
            raise ValueError("Choose a working folder outside the browser's temporary cache")
        self.folder = path
        return self.folder_info()

    def _connect(self) -> None:
        owner = threading.get_ident()
        if self._owner is not None and self._owner != owner:
            raise RuntimeError("Use AttachmentStore from its transfer worker only")
        if self._client is not None:
            return
        self._owner = owner
        client = self._client_factory() if self._client_factory else Client(self.config_path)
        original = client.api_client.call_api

        def bounded_call(*args, **kwargs):
            if kwargs.get("_request_timeout") is None:
                # urllib3 uses its connect timeout while sending the body too.
                # Both new uploads and replacements carry a multipart file.
                kwargs["_request_timeout"] = _UPLOAD_TIMEOUT if kwargs.get("files") else _REQUEST_TIMEOUT
            return original(*args, **kwargs)

        client.api_client.call_api = bounded_call
        load = client.load_experiment if self.entity_type == "experiments" else client.load_resource
        entity = load(ID=self.entity_id)
        self._client, self._entity = client, entity
        self.title = entity.title

    def list_files(self) -> list[dict[str, Any]]:
        """Fetch current attachments; model instances are kept off the GUI thread."""
        self._connect()
        self._entity.refresh()
        self.title = self._entity.title
        return [{key: value for key, value in row.items() if key != "raw"}
                for row in self._entity.list_files()]

    def _web_root(self) -> str:
        self._connect()
        host = self._client.configuration.host.rstrip("/")
        return host[:-7] if host.endswith("/api/v2") else host

    def record_link(self) -> str:
        """Return the ordinary eLabFTW page URL, without API credentials."""
        page = "experiments.php" if self.entity_type == "experiments" else "database.php"
        return f"{self._web_root()}/{page}?{urlencode({'mode': 'view', 'id': self.entity_id})}"

    def attachment_links(self, rows) -> list[str]:
        """Return the download URLs used by eLabFTW's attachment interface."""
        selected = self._selected(rows, validate_names=False)
        host = self._web_root()
        links = []
        for row in selected:
            name = row.get("real_name") or row.get("name")
            if not row.get("long_name"):
                raise ValueError(f"The server did not provide a download link for {name!r}.")
            query = urlencode({"f": row["long_name"], "name": name,
                               "storage": 1 if row.get("storage") is None else row["storage"]})
            links.append(f"{host}/app/download.php?{query}")
        return links

    def _selected(self, rows: Iterable[Mapping[str, Any]], *, validate_names=True) -> list[dict[str, Any]]:
        identifiers = [int(row["id"]) for row in rows]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("An attachment was selected more than once")
        current = {int(row["id"]): row for row in self.list_files()}
        result = []
        for identifier in identifiers:
            if identifier not in current:
                raise FileNotFoundError("An attachment changed or disappeared; refresh the file list")
            row = current[identifier]
            if validate_names:
                _filename(row.get("real_name") or row.get("name"))
            result.append(row)
        return result

    def upload(self, paths, *, replace=False, progress=None, cancel=None):
        """Upload local files; require explicit permission to replace names."""
        _cancelled(cancel)
        paths = [Path(path).expanduser().resolve() for path in paths]
        names = set()
        for path in paths:
            _filename(path.name)
            if not path.is_file():
                raise ValueError(f"Choose files, not directories: {path.name}")
            if path.name.casefold() in names:
                raise FileExistsError(f"Several selected files have the name {path.name!r}")
            names.add(path.name.casefold())
        existing = self.list_files()
        replacements = []
        for path in paths:
            matches = [row for row in existing
                       if str(row.get("real_name") or row.get("name")).casefold() == path.name.casefold()]
            if len(matches) > 1:
                raise FileExistsError(f"Several attachments are named {path.name!r}; rename them first")
            if matches and not replace:
                raise FileExistsError(f"Attachment {path.name!r} already exists")
            replacements.append(matches[0] if matches else None)
        for path, previous in zip(paths, replacements):
            _cancelled(cancel)
            size = path.stat().st_size
            if progress:
                progress(0, size, path.name)
            if previous is None:
                self._client.uploads.post_upload(self.entity_type, self.entity_id, file=str(path))
            else:
                self._client.replace_upload(
                    self.entity_type, self.entity_id, int(previous["id"]), file_path=str(path)
                )
            if progress:
                progress(size, size, path.name)
        return self.list_files()

    def rename(self, upload_id: int, new_name: str):
        """Rename one server attachment, leaving its contents and ID intact."""
        new_name = _filename(new_name)
        rows = self.list_files()
        selected = next((row for row in rows if int(row["id"]) == int(upload_id)), None)
        if selected is None:
            raise FileNotFoundError("The attachment disappeared; refresh the file list")
        for row in rows:
            name = str(row.get("real_name") or row.get("name"))
            if int(row["id"]) != int(upload_id) and name.casefold() == new_name.casefold():
                raise FileExistsError(f"Attachment {new_name!r} already exists")
        if (selected.get("real_name") or selected.get("name")) != new_name:
            self._client.uploads.patch_upload(
                self.entity_type, self.entity_id, int(upload_id), body={"real_name": new_name}
            )
        return self.list_files()

    def delete(self, rows, *, cancel=None):
        """Delete selected server attachments without changing local or cached files.

        The browser asks for confirmation before calling this method. Check the
        full selection before deleting anything; cancellation stops between files.
        """
        _cancelled(cancel)
        rows = self._selected(rows, validate_names=False)
        for row in rows:
            _cancelled(cancel)
            self._client.uploads.delete_upload(self.entity_type, self.entity_id, int(row["id"]))
        return self.list_files()

    def _cache_target(self, row) -> Path:
        version = "|".join(str(row.get(key) or "") for key in
                           ("hash", "created_at", "filesize", "long_name"))
        version = hashlib.sha256(version.encode()).hexdigest()[:16]
        name = _filename(row.get("real_name") or row.get("name"))
        target = self._cache_record_directory()
        target = target / f"{int(row['id'])}_{version}" / name
        self._check_managed_target(target, self.cache_directory)
        return target.resolve()

    def _working_target(self, row) -> Path:
        self._connect()
        host = self._client.configuration.host.rstrip("/")
        server = hashlib.sha256(host.encode()).hexdigest()[:16]
        name = _filename(row.get("real_name") or row.get("name"))
        identifier = int(row["id"])
        if identifier <= 0:
            raise ValueError("The attachment has no valid upload ID")
        target = self.folder / server / f"{self.entity_type}_{self.entity_id}" / str(identifier) / name
        self._check_managed_target(target, self.folder)
        return target.resolve()

    @classmethod
    def _check_managed_target(cls, target, root):
        try:
            relative = target.relative_to(root)
        except ValueError as error:
            raise ValueError("The attachment path points outside its folder") from error
        current = root
        cls._check_cache_path(current, root)
        for part in relative.parts:
            current = current / part
            cls._check_cache_path(current, root)

    def _cache_record_directory(self) -> Path:
        self._connect()
        host = self._client.configuration.host.rstrip("/")
        server = hashlib.sha256(host.encode()).hexdigest()[:16]
        server_directory = self.cache_directory / server
        directory = server_directory / f"{self.entity_type}_{self.entity_id}"
        for path in (server_directory, directory):
            self._check_cache_path(path, self.cache_directory)
        return directory

    @staticmethod
    def _check_cache_path(path, directory):
        junction = _junction(path)
        if path.is_symlink() or junction or not path.resolve().is_relative_to(directory):
            raise ValueError("The attachment cache contains a symbolic link or an unsafe path")

    def _cache_entries(self):
        directory = self._cache_record_directory()
        files, folders = [], []
        if directory.exists():
            if not directory.is_dir():
                raise NotADirectoryError(directory)
            pending = [directory]
            while pending:
                parent = pending.pop()
                self._check_cache_path(parent, directory)
                for path in parent.iterdir():
                    self._check_cache_path(path, directory)
                    if path.is_dir():
                        folders.append(path)
                        pending.append(path)
                    elif path.is_file():
                        files.append((path, path.stat().st_size))
                    else:
                        raise ValueError("The attachment cache contains an unsupported file")
        return directory, files, folders

    def cache_info(self) -> dict[str, Any]:
        """Measure only this record's cached downloads, without creating folders."""
        directory, files, _ = self._cache_entries()
        return {"path": directory, "bytes": sum(size for _, size in files), "files": len(files)}

    def clear_cache(self, *, cancel=None) -> dict[str, Any]:
        """Remove this record's cached copies; retain saved files and other records.

        The browser stops its preview worker first. Validate the complete tree
        before deleting files, then check each path again before removing it.
        Already removed files remain removed if cancellation or an I/O error occurs.
        """
        _cancelled(cancel)
        directory, files, folders = self._cache_entries()
        removed_files = removed_bytes = 0
        try:
            for path, size in files:
                _cancelled(cancel)
                self._check_cache_path(path, directory)
                path.unlink()
                removed_files += 1
                removed_bytes += size
            for path in sorted(folders, key=lambda item: len(item.parts), reverse=True):
                _cancelled(cancel)
                self._check_cache_path(path, directory)
                path.rmdir()
        except OSError as error:
            raise OSError("Could not clear all cached downloads. Close applications using them and try again.") from error
        result = self.cache_info()
        result.update(removed_files=removed_files, removed_bytes=removed_bytes)
        return result

    @staticmethod
    def _expected_hash(row) -> str | None:
        value = row.get("hash")
        algorithm = str(row.get("hash_algorithm") or "").lower()
        return str(value).lower() if value and algorithm in ("", "sha256") else None

    def _cached(self, path, row) -> bool:
        if not path.is_file():
            return False
        expected = self._expected_hash(row)
        if expected:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(_CHUNK_SIZE), b""):
                    digest.update(block)
            return digest.hexdigest() == expected
        size = row.get("filesize")
        return size is None or path.stat().st_size == _size(size)

    @staticmethod
    def _download_limit(max_bytes):
        if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0):
            raise ValueError("max_bytes must be a positive integer or None")
        return max_bytes

    @staticmethod
    def _check_download_size(size, max_bytes):
        if max_bytes is not None and _size(size) > max_bytes:
            raise ValueError("This attachment exceeds the preview download limit. Use Save as... for the full file.")

    def _analysis_source(self, row, filepath):
        if (self.entity_type != "experiments" or Path(filepath).suffix != ".h5"
                or Path(str(row.get("real_name") or row.get("name"))).suffix != ".h5"):
            return None
        self._connect()
        title = self.title if isinstance(self.title, str) and self.title.strip() else f"Experiment {self.entity_id}"
        return RemoteSource(self.entity_id, title, self._client.configuration.host.rstrip("/"))

    def _check_analysis_source(self, row, filepath):
        name = Path(filepath).name.casefold()
        if name == ".elablink.json" or name.endswith(".h5.elablink.json"):
            raise ValueError("This filename is reserved for eLabLink source metadata; choose another filename.")
        source = self._analysis_source(row, filepath)
        if source is not None:
            check_source_manifest(filepath, source)
        elif Path(filepath).suffix == ".h5" and read_source(filepath) is not None:
            raise ValueError(
                "This local file is associated with an eLabFTW experiment. Choose another destination filename."
            )

    def _remember_analysis_source(self, row, filepath):
        source = self._analysis_source(row, filepath)
        if source is not None:
            write_source_manifest(filepath, source)

    def _download(self, row, destination, *, overwrite, progress, cancel, max_bytes=None, managed_root=None):
        _cancelled(cancel)
        max_bytes = self._download_limit(max_bytes)
        self._check_download_size(row.get("filesize"), max_bytes)
        destination = Path(destination).expanduser().absolute()
        if managed_root is not None:
            self._check_managed_target(destination, managed_root)
        destination = destination.resolve()
        self._check_analysis_source(row, destination)
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".elablink-", suffix=".part", dir=destination.parent)
        temporary = Path(name)
        response = None
        complete = False
        filename = str(row.get("real_name") or row.get("name"))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                response = self._client.uploads.read_upload(
                    self.entity_type, self.entity_id, int(row["id"]), format="binary",
                    _preload_content=False,
                )
                headers = response.headers
                encoded = str(headers.get("Content-Encoding") or "identity").lower() != "identity"
                try:
                    length = int(headers["Content-Length"]) if not encoded else None
                    if length is not None and length < 0:
                        length = None
                except (KeyError, ValueError, TypeError):
                    length = None
                self._check_download_size(length, max_bytes)
                total = length if length is not None else _size(row.get("filesize"))
                digest = hashlib.sha256()
                done = 0
                if progress:
                    progress(0, total, filename)
                while True:
                    _cancelled(cancel)
                    amount = _CHUNK_SIZE if max_bytes is None else min(_CHUNK_SIZE, max_bytes - done + 1)
                    block = response.read(amount, decode_content=True)
                    if not block:
                        break
                    self._check_download_size(done + len(block), max_bytes)
                    stream.write(block)
                    digest.update(block)
                    done += len(block)
                    if progress:
                        progress(done, total, filename)
                if length is not None and done != length:
                    raise OSError(f"Incomplete download of {filename!r}; expected {length} bytes, received {done}")
                expected = self._expected_hash(row)
                if expected and digest.hexdigest() != expected:
                    raise OSError(f"The downloaded contents of {filename!r} do not match the server hash; refresh and retry")
                _cancelled(cancel)
                complete = True
            if managed_root is not None:
                self._check_managed_target(destination, managed_root)
            self._check_analysis_source(row, destination)
            if overwrite:
                os.replace(temporary, destination)
            elif os.name == "nt":
                os.rename(temporary, destination)
            else:
                # Linking within one directory commits without overwriting a file
                # another application may have created during the transfer.
                os.link(temporary, destination)
                temporary.unlink()
            return destination
        finally:
            if response is not None:
                if not complete:
                    response.close()
                response.release_conn()
            temporary.unlink(missing_ok=True)

    def download(self, rows, directory=None, *, overwrite=False, progress=None, cancel=None,
                 max_bytes=None, refresh=False, with_metadata=False):
        """Prepare local copies, preserving working files until an explicit refresh.

        A working folder has stable attachment paths and retains local edits.
        ``refresh=True`` replaces a working copy only after the browser confirms it.
        ``max_bytes`` bounds each transfer; manual downloads have no size limit.
        ``with_metadata=True`` pairs each path with its refreshed attachment row.
        """
        _cancelled(cancel)
        max_bytes = self._download_limit(max_bytes)
        rows = self._selected(rows)
        working = directory is None and self.folder is not None
        if directory is None:
            targets = [(self._working_target(row) if working else self._cache_target(row)) for row in rows]
        else:
            directory = Path(directory).expanduser().resolve()
            names = [_filename(row.get("real_name") or row.get("name")) for row in rows]
            if len({name.casefold() for name in names}) != len(names):
                raise FileExistsError("Several selected attachments have the same filename; save them separately")
            targets = [directory / name for name in names]
            if not overwrite:
                for target in targets:
                    if target.exists():
                        raise FileExistsError(target)
        for row, target in zip(rows, targets):
            self._check_download_size(row.get("filesize"), max_bytes)
            self._check_analysis_source(row, target)
        result = []
        for row, target in zip(rows, targets):
            _cancelled(cancel)
            if working and target.exists() and not target.is_file():
                raise IsADirectoryError(target)
            reuse = directory is None and not refresh and (
                target.is_file() if working else self._cached(target, row)
            )
            if reuse:
                self._check_download_size(target.stat().st_size, max_bytes)
                if progress:
                    size = target.stat().st_size
                    progress(size, size, target.name)
                result.append(target)
            else:
                managed_root = self.folder if working else self.cache_directory if directory is None else None
                replace = bool(refresh) if working else overwrite or directory is None
                try:
                    path = self._download(row, target, overwrite=replace, progress=progress, cancel=cancel,
                                          max_bytes=max_bytes, managed_root=managed_root)
                except FileExistsError:
                    if not working or refresh:
                        raise
                    # Another worker or editor may have created the working copy.
                    self._check_managed_target(target, self.folder)
                    if not target.is_file():
                        raise
                    self._check_download_size(target.stat().st_size, max_bytes)
                    path = target
                result.append(path)
            self._remember_analysis_source(row, result[-1])
        if with_metadata:
            return [{"row": row, "path": path} for row, path in zip(rows, result)]
        return result

    def save_files(self, destinations, *, overwrite=(), progress=None, cancel=None):
        """Save upload IDs to fixed destinations, replacing only approved paths.

        Server names may change after the dialog closes. They never change the
        paths selected by the user, or extend permission to overwrite a file.
        """
        _cancelled(cancel)
        rows = self._selected({"id": identifier} for identifier in destinations)
        targets = {int(identifier): absolute_storage_path(path)
                   for identifier, path in destinations.items()}
        approved = {absolute_storage_path(path) for path in overwrite}
        if not approved.issubset(targets.values()):
            raise ValueError("Overwrite approval must refer to a selected destination")
        if len({str(path.resolve()).casefold() for path in targets.values()}) != len(targets):
            raise FileExistsError("Several selected attachments have the same destination; save them separately")
        for row in rows:
            target = targets[int(row["id"])]
            _filename(target.name)
            self._check_managed_target(target, Path(target.anchor))
            if target.exists():
                if not target.is_file():
                    raise IsADirectoryError(target)
                if target not in approved:
                    raise FileExistsError(target)
            self._check_analysis_source(row, target)
        paths = []
        for row in rows:
            target = targets[int(row["id"])]
            path = self._download(row, target, overwrite=target in approved,
                                  progress=progress, cancel=cancel, managed_root=Path(target.anchor))
            self._remember_analysis_source(row, path)
            paths.append(path)
        return paths

    def save_file(self, row, destination, *, overwrite=False, progress=None, cancel=None, max_bytes=None):
        """Download one selected attachment to an explicit Save As destination."""
        _cancelled(cancel)
        row = self._selected([row])[0]
        destination = Path(destination).expanduser().resolve()
        _filename(destination.name)
        path = self._download(row, destination, overwrite=overwrite,
                              progress=progress, cancel=cancel, max_bytes=max_bytes)
        self._remember_analysis_source(row, path)
        return path
