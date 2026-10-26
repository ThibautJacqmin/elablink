"""Offline checks of local-file associations for remote Labmate analysis."""

import io
import json
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from elablink.remote_analysis import (
    RemoteSource,
    download_analysis,
    read_source,
    remember_source,
)


try:
    import h5py
except ImportError:
    h5py = None


class DownloadExperiment:
    """Serve one in-memory attachment without a real API client."""

    ID = 123
    title = "Original experiment"

    def __init__(self, payload):
        self.payload = payload
        self.list_calls = 0
        self.download_calls = 0

    def list_files(self):
        self.list_calls += 1
        return [{"id": 42, "real_name": "run.h5"}]

    def download_file(self, file_id, destination):
        if file_id != 42:
            raise AssertionError("The wrong upload was requested")
        self.download_calls += 1
        Path(destination).write_bytes(self.payload)


class DownloadClient:
    def __init__(self, payload, server="https://first.invalid/api/v2"):
        self.configuration = SimpleNamespace(host=server)
        self.experiment = DownloadExperiment(payload)

    def load_experiment(self, *, ID):  # noqa: N803 - Client API
        if ID != self.experiment.ID:
            raise AssertionError("The wrong experiment was requested")
        return self.experiment


@unittest.skipUnless(h5py is not None, "Requires h5py")
class RemoteAnalysisMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "data"
        self.data.mkdir()
        self.catalog = self.data / ".elabftw"
        self.payload = self.hdf5_bytes(1)
        self.client = DownloadClient(self.payload)
        self.source = RemoteSource(
            123, "Original experiment", "https://first.invalid/api/v2"
        )

    @staticmethod
    def hdf5_bytes(revision):
        content = io.BytesIO()
        with h5py.File(content, "w") as saved:
            saved["revision"] = revision
        return content.getvalue()

    def download(self, client=None):
        return download_analysis(client or self.client, 123, "run.h5", self.catalog)

    def test_concurrent_catalog_updates_preserve_all_twenty_associations(self):
        barrier = threading.Barrier(20)

        def associate(index):
            filepath = self.data / f"run-{index}.h5"
            filepath.write_bytes(self.payload)
            source = RemoteSource(index + 1, f"Run {index}", self.source.server)
            barrier.wait(timeout=10)
            remember_source(filepath, source, self.catalog)
            self.assertEqual(read_source(filepath, self.catalog), source)
            return filepath, source

        with ThreadPoolExecutor(max_workers=20) as workers:
            futures = [workers.submit(associate, index) for index in range(20)]
            associations = [future.result(timeout=15) for future in futures]

        records = json.loads((self.catalog / "index.json").read_text())["files"]
        self.assertEqual(len(records), 20)
        for filepath, source in associations:
            self.assertEqual(read_source(filepath, self.catalog), source)
        self.assertEqual(list(self.catalog.glob("index-*.tmp")), [])

    def test_relative_associations_survive_moving_the_complete_data_directory(self):
        original = self.data / "acquisitions" / "run.h5"
        original.parent.mkdir()
        original.write_bytes(self.payload)
        remember_source(original, self.source, self.catalog)
        records = json.loads((self.catalog / "index.json").read_text())["files"]
        self.assertEqual(records[0]["path"], "acquisitions/run.h5")

        moved = self.root / "moved-data"
        self.assertTrue(self.data.resolve().is_relative_to(self.root))
        self.assertTrue(moved.resolve().is_relative_to(self.root))
        shutil.move(str(self.data), str(moved))
        new_file = moved / "acquisitions" / "run.h5"
        new_catalog = moved / ".elabftw"
        self.assertEqual(read_source(new_file, new_catalog), self.source)
        selected, source, _ = download_analysis(
            self.client, 123, "run.h5", new_catalog
        )
        self.assertEqual(selected, new_file)
        self.assertEqual(source, self.source)
        self.assertEqual(self.client.experiment.download_calls, 0)
        self.assertEqual(self.client.experiment.list_calls, 0)

    def test_malformed_catalog_is_reported_and_never_silently_replaced(self):
        original = self.data / "run.h5"
        original.write_bytes(self.payload)
        self.catalog.mkdir()
        index = self.catalog / "index.json"
        invalid = b"{ this is not valid JSON"
        index.write_bytes(invalid)

        with self.assertRaises(ValueError):
            remember_source(original, self.source, self.catalog)
        with self.assertRaises(ValueError):
            read_source(original, self.catalog)
        with self.assertRaises(ValueError):
            self.download()

        self.assertEqual(index.read_bytes(), invalid)
        self.assertEqual(original.read_bytes(), self.payload)
        self.assertEqual(self.client.experiment.download_calls, 0)
        self.assertEqual(self.client.experiment.list_calls, 0)
        self.assertEqual(list(self.catalog.glob("experiment-*")), [])

    def test_corrupt_existing_local_file_is_not_overwritten_or_downloaded_again(self):
        filepath, _, _ = self.download()
        local_content = b"local modifications which are not valid HDF5"
        filepath.write_bytes(local_content)

        with self.assertRaises(OSError):
            self.download()

        self.assertEqual(filepath.read_bytes(), local_content)
        self.assertEqual(self.client.experiment.download_calls, 1)
        self.assertEqual(self.client.experiment.list_calls, 1)
        self.assertEqual(len(list(self.catalog.glob("experiment-*"))), 1)

    def test_same_experiment_id_and_filename_on_other_server_do_not_reuse_data(self):
        first, first_source, _ = self.download()
        second_client = DownloadClient(
            self.hdf5_bytes(2), server="https://second.invalid/api/v2/"
        )
        second, second_source, _ = self.download(second_client)

        self.assertNotEqual(first, second)
        self.assertNotEqual(first_source.server, second_source.server)
        self.assertEqual(second_source.server, "https://second.invalid/api/v2")
        self.assertEqual(second_client.experiment.download_calls, 1)
        with h5py.File(first, "r") as saved:
            self.assertEqual(int(saved["revision"][()]), 1)
        with h5py.File(second, "r") as saved:
            self.assertEqual(int(saved["revision"][()]), 2)
        first_again, _, _ = self.download()
        self.assertEqual(first_again, first)
        self.assertEqual(self.client.experiment.download_calls, 1)

    def test_missing_complete_working_directory_is_recreated_by_new_download(self):
        first, _, _ = self.download()
        working = first.parent.resolve()
        self.assertEqual(working.parent, self.catalog.resolve())
        self.assertTrue(working.name.startswith("experiment-123-"))
        shutil.rmtree(working)

        second, source, _ = self.download()

        self.assertNotEqual(first, second)
        self.assertFalse(first.exists())
        self.assertTrue(second.is_file())
        self.assertEqual(second.read_bytes(), self.payload)
        self.assertEqual(read_source(second), source)
        self.assertEqual(self.client.experiment.download_calls, 2)
        self.assertTrue((second.parent / ".elablink.json").is_file())
