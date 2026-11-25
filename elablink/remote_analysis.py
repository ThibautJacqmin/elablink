"""Download Labmate analysis files and preserve their eLabFTW destination.

@author Thibaut Jacqmin
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp
from threading import RLock
from typing import Any


_MANIFEST = ".elablink.json"
_CATALOG = "index.json"
_CATALOG_LOCK = RLock()


@dataclass(frozen=True)
class RemoteSource:
    """The original experiment for a downloaded local working copy."""

    experiment_id: int
    title: str
    server: str


def validate_request(experiment_id: int, filename: str) -> None:
    """Require an experiment ID and one unambiguous Labmate attachment name."""
    if (
        not isinstance(experiment_id, int)
        or isinstance(experiment_id, bool)
        or experiment_id <= 0
    ):
        raise ValueError("experiment_id must be a positive integer")
    if (
        not isinstance(filename, str)
        or not filename
        or filename in {".", ".."}
        or any(character in filename for character in ("/", "\\", ":", "\0"))
        or Path(filename).is_absolute()
        or not filename.endswith(".h5")
        or filename[:-3] in {"", ".", ".."}
    ):
        raise ValueError("filename must be a single attachment name ending in '.h5'")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"The eLabLink source {field} must be nonempty text")
    return value


def _local_file(filepath: Path) -> Path:
    filepath = Path(filepath).expanduser().resolve()
    if not str(filepath).endswith(".h5"):
        filepath = Path(str(filepath) + ".h5")
    return filepath


def _source_record(source: RemoteSource, filename: str) -> dict[str, Any]:
    validate_request(source.experiment_id, filename)
    server = _required_text(source.server, "server").rstrip("/")
    return {
        "experiment_id": source.experiment_id,
        "title": _required_text(source.title, "title"),
        "server": _required_text(server, "server"),
        "filename": filename,
    }


def _record_source(record: dict[str, Any]) -> RemoteSource:
    validate_request(record.get("experiment_id"), record.get("filename"))
    title = _required_text(record.get("title"), "title")
    server = _required_text(record.get("server"), "server").rstrip("/")
    return RemoteSource(
        record["experiment_id"], title, _required_text(server, "server")
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid eLabLink source metadata in {path}") from exc
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
    ):
        raise ValueError(f"Unsupported eLabLink source metadata in {path}")
    return data


def _file_manifest(filepath: Path) -> Path:
    return filepath.with_name(filepath.name + ".elablink.json")


def _manifest_source(manifest: Path, filepath: Path) -> RemoteSource:
    if manifest.is_symlink() or getattr(manifest, "is_junction", lambda: False)():
        raise ValueError("eLabLink source metadata must not use symbolic links or junctions")
    if not manifest.is_file():
        raise ValueError(f"The eLabLink source metadata is not a file: {manifest}")
    data = _read_json(manifest)
    if data.get("filename") != filepath.name:
        raise ValueError(f"The eLabLink source filename does not match {filepath.name!r}")
    return _record_source(data)


def check_source_manifest(filepath: Path, source: RemoteSource) -> None:
    """Refuse to replace a different or malformed local experiment association."""
    filepath = _local_file(filepath)
    _source_record(source, filepath.name)
    with _CATALOG_LOCK:
        manifest = _file_manifest(filepath)
        existing = None
        if manifest.exists() or manifest.is_symlink():
            existing = _manifest_source(manifest, filepath)
        else:
            legacy = filepath.parent / _MANIFEST
            if legacy.exists() or legacy.is_symlink():
                data = _read_json(legacy)
                # A legacy folder manifest may describe another file in this folder.
                if data.get("filename") == filepath.name:
                    existing = _manifest_source(legacy, filepath)
        if existing is not None and (
            existing.experiment_id != source.experiment_id
            or existing.server.rstrip("/") != source.server.rstrip("/")
        ):
            raise ValueError(
                "This local file belongs to a different eLabFTW experiment or server. "
                "Choose another destination filename."
            )


def write_source_manifest(filepath: Path, source: RemoteSource, *, missing_ok: bool = False) -> Path | None:
    """Write portable routing beside an H5 file. Optionally skip a missing original."""
    filepath = _local_file(filepath)
    if not filepath.is_file():
        if missing_ok:
            return None
        raise FileNotFoundError(filepath)
    manifest = _file_manifest(filepath)
    with _CATALOG_LOCK:
        check_source_manifest(filepath, source)
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=".elablink-source-", suffix=".tmp",
                dir=filepath.parent, delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, **_source_record(source, filepath.name)}, stream, indent=2)
                stream.write("\n")
            check_source_manifest(filepath, source)
            if manifest.exists():
                temporary.replace(manifest)
            else:
                try:
                    if os.name == "nt":
                        temporary.rename(manifest)
                    else:
                        os.link(temporary, manifest)
                except FileExistsError:
                    check_source_manifest(filepath, source)
                    temporary.replace(manifest)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return manifest


def _catalog_file(record: dict[str, Any], directory: Path) -> Path:
    stored = _required_text(record.get("path"), "path")
    if "\0" in stored:
        raise ValueError("The eLabLink catalog contains an invalid path")
    filepath = Path(stored)
    if not filepath.is_absolute():
        if filepath.anchor or ".." in filepath.parts:
            raise ValueError("The eLabLink catalog contains an invalid relative path")
        filepath = directory.parent / filepath
    filepath = filepath.expanduser().resolve()
    if filepath.name != record.get("filename"):
        raise ValueError("The eLabLink catalog path does not match its filename")
    return filepath


def _read_catalog(directory: Path) -> list[dict[str, Any]]:
    with _CATALOG_LOCK:
        return _read_catalog_contents(directory)


def _read_catalog_contents(directory: Path) -> list[dict[str, Any]]:
    catalog = directory / _CATALOG
    if not catalog.exists():
        return []
    data = _read_json(catalog)
    records = data.get("files")
    if not isinstance(records, list):
        raise ValueError(f"Invalid eLabLink file catalog in {catalog}")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"Invalid eLabLink file catalog in {catalog}")
        _record_source(record)
        _catalog_file(record, directory)
    return records


def remember_source(filepath: Path, source: RemoteSource, directory: Path) -> None:
    """Remember a successful upload/download without changing the data file.

    Paths inside the data directory are relative so moving a project preserves
    its associations. A file outside it is recorded by its absolute local path.
    """
    with _CATALOG_LOCK:
        _remember_source(filepath, source, directory)


def _remember_source(filepath: Path, source: RemoteSource, directory: Path) -> None:
    filepath = _local_file(filepath)
    directory = Path(directory).expanduser().resolve()
    records = _read_catalog(directory)
    record = _source_record(source, filepath.name)
    try:
        record["path"] = filepath.relative_to(directory.parent).as_posix()
    except ValueError:
        record["path"] = str(filepath)
    records = [item for item in records if _catalog_file(item, directory) != filepath]
    records.append(record)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="index-",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump({"version": 1, "files": records}, stream, indent=2)
            stream.write("\n")
        temporary.replace(directory / _CATALOG)
    finally:
        if temporary is not None and temporary.exists():
            if temporary.parent != directory or not temporary.name.startswith("index-"):
                raise ValueError("Refusing to remove files outside the catalog folder")
            temporary.unlink()


def _validate_hdf5(filepath: Path) -> None:
    # Labmate is optional for the direct eLabFTW API, so import lazily.
    import h5py

    with h5py.File(filepath, "r"):
        pass


def download_analysis(
    client: Any,
    experiment_id: int,
    filename: str,
    directory: Path,
) -> tuple[Path, RemoteSource, Any]:
    """Reuse an associated local file, downloading only when it is missing.

    Existing local analyses take precedence over the remote attachment. They are
    validated but never overwritten. A missing local file gets a fresh download
    directory. The catalog and manifests contain routing, never credentials.
    """
    validate_request(experiment_id, filename)
    experiment = client.load_experiment(ID=experiment_id)
    title = getattr(experiment, "title", None)
    if not isinstance(title, str) or not title.strip():
        title = f"Experiment {experiment_id}"
    server = _required_text(client.configuration.host, "server").rstrip("/")
    _required_text(server, "server")
    source = RemoteSource(experiment_id, title, server)
    directory = Path(directory).expanduser().resolve()
    records = _read_catalog(directory)
    for record in reversed(records):
        if (
            record["experiment_id"] == experiment_id
            and record["server"].rstrip("/") == server
            and record["filename"] == filename
        ):
            filepath = _catalog_file(record, directory)
            if filepath.is_file():
                _validate_hdf5(filepath)
                return filepath, source, experiment

    matches = [
        upload
        for upload in experiment.list_files()
        if (upload.get("real_name") or upload.get("name")) == filename
    ]
    if not matches:
        raise FileNotFoundError(
            f"Experiment {experiment_id} has no attachment named {filename!r}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Experiment {experiment_id} has several attachments named {filename!r}"
        )
    file_id = matches[0].get("id")
    if isinstance(file_id, bool):
        raise ValueError("The eLabFTW attachment has no valid upload ID")
    try:
        file_id = int(file_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("The eLabFTW attachment has no valid upload ID") from exc
    if file_id <= 0:
        raise ValueError("The eLabFTW attachment has no valid upload ID")

    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"experiment-{experiment_id}-"
    working = Path(mkdtemp(prefix=prefix, dir=directory)).resolve()
    try:
        temporary = working / ".download"
        experiment.download_file(file_id, temporary)

        _validate_hdf5(temporary)
        filepath = working / filename
        if filepath.exists():
            raise FileExistsError(filepath)
        temporary.rename(filepath)
        with (working / _MANIFEST).open("x", encoding="utf-8") as stream:
            json.dump(
                {"version": 1, **_source_record(source, filename)},
                stream,
                indent=2,
            )
            stream.write("\n")
        remember_source(filepath, source, directory)
        return filepath, source, experiment
    except Exception:
        if working.parent != directory or not working.name.startswith(prefix):
            raise ValueError("Refusing to remove files outside the download folder")
        shutil.rmtree(working)
        raise


def read_source(filepath: Path, directory: Path | None = None) -> RemoteSource | None:
    """Read local routing without contacting eLabFTW or changing the HDF5 file."""
    filepath = _local_file(filepath)
    for manifest in (_file_manifest(filepath), filepath.parent / _MANIFEST):
        if manifest.exists() or manifest.is_symlink():
            return _manifest_source(manifest, filepath)
    # Older acquisitions have only a catalog in their original data directory.
    # Look up their exact path there, even when another project opens the file.
    directories = [Path(directory)] if directory is not None else []
    directories.extend(parent / ".elabftw" for parent in filepath.parents)
    visited = set()
    source = None
    for candidate in directories:
        candidate = candidate.expanduser().resolve()
        if candidate in visited:
            continue
        visited.add(candidate)
        for record in reversed(_read_catalog(candidate)):
            if _catalog_file(record, candidate) != filepath:
                continue
            found = _record_source(record)
            if source is not None and (
                source.experiment_id != found.experiment_id
                or source.server.rstrip("/") != found.server.rstrip("/")
            ):
                raise ValueError("Conflicting eLabFTW origins for this local file; keep one verified association")
            source = source or found
    return source
