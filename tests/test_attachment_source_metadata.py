"""Portable browser-download associations for later local Labmate analysis."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from elablink._attachment_store import AttachmentStore
from elablink.remote_analysis import RemoteSource, read_source, write_source_manifest
from tests.test_attachment_store import FakeClient, Response


class AttachmentSourceMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.client = FakeClient()
        self.cache = self.root / "cache"
        self.working = self.root / "My local analysis"
        self.store = self.new_store()
        self.source = RemoteSource(17, "Offline experiment", self.client.configuration.host)

    def new_store(self, kind="experiments", identifier=17, *, folder=None):
        return AttachmentStore(None, kind, identifier, self.cache, lambda: self.client, folder=folder)

    @staticmethod
    def manifest(filepath):
        return filepath.with_name(filepath.name + ".elablink.json")

    def record(self, filepath, source=None):
        source = source or self.source
        return {"version": 1, "filename": filepath.name, "experiment_id": source.experiment_id,
                "title": source.title, "server": source.server}

    def test_cached_h5_download_and_reuse_remember_origin_without_credentials(self):
        row = self.client.add("run.h5", b"synthetic data")
        filepath = self.store.download([row])[0]
        self.assertEqual(read_source(filepath), self.source)
        metadata = json.loads(self.manifest(filepath).read_text(encoding="utf-8"))
        self.assertEqual(metadata, self.record(filepath))
        self.assertNotIn("API_KEY", metadata)
        self.manifest(filepath).unlink()
        self.assertEqual(self.store.download([row]), [filepath])
        self.assertEqual(len(self.client.reads), 1)
        self.assertEqual(read_source(filepath), self.source)

    def test_working_copy_keeps_local_edits_and_can_refresh_same_origin_title(self):
        row = self.client.add("run.h5", b"remote original")
        store = self.new_store(folder=self.working)
        filepath = store.download([row])[0]
        filepath.write_bytes(b"precious local analysis edits")
        store._entity.title = "Renamed experiment"
        self.assertEqual(store.download([row]), [filepath])
        self.assertEqual(filepath.read_bytes(), b"precious local analysis edits")
        self.assertEqual(len(self.client.reads), 1)
        self.assertEqual(read_source(filepath), RemoteSource(17, "Renamed experiment", self.source.server))

    def test_save_as_can_associate_two_experiments_in_one_folder(self):
        row = self.client.add("run.h5", b"remote data")
        first = self.store.save_file(row, self.root / "analysis-a.h5")
        other = self.new_store(identifier=22)
        second = other.save_file(row, self.root / "analysis-b.h5")
        self.assertEqual(read_source(first).experiment_id, 17)
        self.assertEqual(read_source(second).experiment_id, 22)
        self.assertEqual(json.loads(self.manifest(second).read_text())["filename"], "analysis-b.h5")
        self.assertFalse((self.root / ".elablink.json").exists())

    def test_sidecar_is_portable_with_its_file_and_independent_of_manager_catalog(self):
        row = self.client.add("run.h5", b"remote data")
        original = self.store.save_file(row, self.root / "first" / "run.h5")
        moved = self.root / "unrelated project title"
        shutil.copytree(original.parent, moved)
        copied = moved / original.name
        self.assertEqual(read_source(copied), self.source)
        self.assertEqual(read_source(copied, self.root / "fresh-manager-data" / ".elabftw"), self.source)

    def test_invalid_or_conflicting_metadata_prevents_overwrite_before_transfer(self):
        row = self.client.add("run.h5", b"server version")
        for existing in (
            b"{malformed JSON}",
            json.dumps(self.record(self.root / "run.h5", RemoteSource(99, "Another", self.source.server))).encode(),
            json.dumps(self.record(self.root / "run.h5", RemoteSource(17, "Other server", "https://other.invalid/api/v2"))).encode(),
        ):
            with self.subTest(existing=existing):
                filepath = self.root / "run.h5"
                filepath.write_bytes(b"keep local edits")
                manifest = self.manifest(filepath)
                manifest.write_bytes(existing)
                calls = len(self.client.reads)
                with self.assertRaises(ValueError):
                    self.store.save_file(row, filepath, overwrite=True)
                self.assertEqual(filepath.read_bytes(), b"keep local edits")
                self.assertEqual(manifest.read_bytes(), existing)
                self.assertEqual(len(self.client.reads), calls)

    def test_conflict_appearing_during_download_is_checked_before_publication(self):
        row = self.client.add("run.h5", b"new server version")
        filepath = self.root / "run.h5"
        filepath.write_bytes(b"existing local data")

        def appeared():
            manifest = self.manifest(filepath)
            if not manifest.exists():
                manifest.write_text(json.dumps(self.record(filepath, RemoteSource(99, "Another", self.source.server))))

        self.client.response_factory = lambda content: Response(content, on_read=appeared)
        with self.assertRaisesRegex(ValueError, "different eLabFTW experiment or server"):
            self.store.save_file(row, filepath, overwrite=True)
        self.assertEqual(filepath.read_bytes(), b"existing local data")
        self.assertEqual(read_source(filepath).experiment_id, 99)
        self.assertEqual(list(self.root.glob(".elablink-*.part")), [])

    def test_failed_download_never_creates_an_association(self):
        row = self.client.add("run.h5", b"server data")
        self.client.response_factory = lambda content: Response(content[:2], length=len(content))
        filepath = self.root / "run.h5"
        with self.assertRaises(OSError):
            self.store.save_file(row, filepath)
        self.assertFalse(filepath.exists())
        self.assertFalse(self.manifest(filepath).exists())

    def test_resource_h5_and_other_formats_never_gain_experiment_metadata(self):
        resource_row = self.client.add("resource.h5", b"resource data")
        resource = self.new_store(kind="items")
        filepath = resource.download([resource_row])[0]
        self.assertIsNone(read_source(filepath))
        self.assertFalse(self.manifest(filepath).exists())
        plain = self.client.add("notes.txt", b"notes")
        filepath = self.store.download([plain])[0]
        self.assertFalse(self.manifest(filepath).exists())
        filepath = resource.save_file(resource_row, self.root / "resource.h5")
        self.assertIsNone(read_source(filepath))

    def test_resource_download_cannot_leave_a_stale_experiment_association(self):
        row = self.client.add("run.h5", b"experiment data")
        filepath = self.store.save_file(row, self.root / "run.h5")
        old_data, old_meta = filepath.read_bytes(), self.manifest(filepath).read_bytes()
        resource = self.new_store(kind="items")
        with self.assertRaisesRegex(ValueError, "associated with an eLabFTW experiment"):
            resource.save_file(row, filepath, overwrite=True)
        self.assertEqual(filepath.read_bytes(), old_data)
        self.assertEqual(self.manifest(filepath).read_bytes(), old_meta)

    def test_per_file_sidecar_takes_precedence_and_preserves_existing_folder_manifest(self):
        filepath = self.root / "run.h5"
        filepath.write_bytes(b"local analysis")
        legacy = self.root / ".elablink.json"
        legacy.write_text(json.dumps(self.record(filepath)))
        self.assertEqual(read_source(filepath), self.source)
        source = RemoteSource(17, "Current title", self.source.server)
        write_source_manifest(filepath, source)
        original_legacy = legacy.read_bytes()
        legacy.write_text(json.dumps(self.record(self.root / "another.h5", RemoteSource(88, "Another", self.source.server))))
        self.assertEqual(read_source(filepath), source)
        other = self.store.save_file(self.client.add("second.h5", b"second"), self.root / "second.h5")
        self.assertEqual(read_source(other), self.source)
        self.assertNotEqual(legacy.read_bytes(), original_legacy)
        self.assertEqual(json.loads(legacy.read_text())["experiment_id"], 88)

    def test_atomic_sidecar_update_preserves_previous_metadata_on_failure(self):
        filepath = self.root / "run.h5"
        filepath.write_bytes(b"local analysis")
        manifest = write_source_manifest(filepath, self.source)
        old = manifest.read_bytes()
        source = RemoteSource(17, "New title", self.source.server)
        with patch("elablink.remote_analysis.Path.replace", side_effect=OSError("Synthetic disk error")):
            with self.assertRaises(OSError):
                write_source_manifest(filepath, source)
        self.assertEqual(manifest.read_bytes(), old)
        self.assertEqual(list(self.root.glob(".elablink-source-*.tmp")), [])

    def test_remote_attachments_cannot_overwrite_reserved_source_metadata_paths(self):
        row = self.client.add("notes.json", b"server JSON")
        for name in (".elablink.json", "run.h5.elablink.json", "run.H5.ELABLINK.JSON"):
            with self.subTest(name=name):
                destination = self.root / name
                destination.write_bytes(b"preserve local association")
                calls = len(self.client.reads)
                with self.assertRaisesRegex(ValueError, "reserved for eLabLink source metadata"):
                    self.store.save_file(row, destination, overwrite=True)
                self.assertEqual(destination.read_bytes(), b"preserve local association")
                self.assertEqual(len(self.client.reads), calls)

    def test_generated_sidecar_is_not_captured_as_an_acquisition_attachment(self):
        from elablink import AsyncBridge
        from tests.fakes import OfflineClient

        row = self.client.add("run.h5", b"saved analysis data")
        filepath = self.store.save_file(row, self.root / "run.h5")
        client = OfflineClient()
        client.configuration.host = self.source.server
        client.experiment.ID = self.source.experiment_id
        adapter = AsyncBridge("Original", client_factory=lambda: client, spool_directory=self.root / "spool")
        try:
            adapter.enqueue(type("Analysis", (), {"filepath": filepath})())
            adapter.flush(timeout=3)
        finally:
            adapter.close(timeout=3)
        self.assertEqual(set(client.experiment.files), {"run.h5"})

    def test_fresh_bridge_routes_local_browser_copy_to_original_id_not_folder_title(self):
        from elablink import AsyncBridge
        from tests.fakes import OfflineClient

        row = self.client.add("run.h5", b"saved analysis data")
        filepath = self.store.save_file(row, self.root / "unrelated folder" / "run.h5")
        client = OfflineClient()
        client.configuration.host = self.source.server
        client.experiment.ID = 17
        adapter = AsyncBridge(
            "Unrelated acquisition title", client_factory=lambda: client,
            spool_directory=self.root / "fresh-manager-data" / ".upload_queue",
        )
        try:
            adapter.bind_analysis(filepath)
            adapter.enqueue(type("Analysis", (), {"filepath": filepath})())
            result = adapter.flush(timeout=3)
        finally:
            adapter.close(timeout=3)
        self.assertEqual(result["id"], 17)
        self.assertEqual(client.created, 0)
        self.assertEqual(set(client.experiment.files), {"run.h5"})

    def test_sidecar_changed_after_browser_copy_is_queued_stops_the_upload(self):
        import threading
        from contextlib import suppress
        from elablink import AsyncBridge
        from tests.fakes import OfflineClient

        filepath = self.store.save_file(self.client.add("run.h5", b"measurement"), self.root / "run.h5")
        entered, release = threading.Event(), threading.Event()
        client = OfflineClient()

        def connect():
            entered.set()
            if not release.wait(4):
                raise TimeoutError("Client initialization did not resume")
            return client

        bridge = AsyncBridge(experiment_id=17, client_factory=connect, spool_directory=self.root / "spool")
        client.configuration.host = self.source.server
        try:
            future = bridge.enqueue(type("Analysis", (), {"filepath": filepath})())
            self.assertTrue(entered.wait(3))
            record = self.record(filepath, RemoteSource(99, "Other", self.source.server))
            self.manifest(filepath).write_text(json.dumps(record), encoding="utf-8")
            release.set()
            with self.assertRaisesRegex(ValueError, "different eLabFTW experiment"):
                future.result(timeout=4)
            self.assertEqual(client.experiment.files, {})
        finally:
            release.set()
            with suppress(RuntimeError):
                bridge.close(timeout=4)

    def test_existing_fixed_experiment_and_server_guards_apply_to_browser_copies(self):
        from contextlib import suppress
        from elablink import AsyncBridge
        from tests.fakes import OfflineClient

        row = self.client.add("run.h5", b"saved analysis data")
        filepath = self.store.save_file(row, self.root / "run.h5")
        client = OfflineClient()
        fixed = AsyncBridge(client_factory=lambda: client, experiment_id=99, spool_directory=self.root / "fixed")
        try:
            with self.assertRaisesRegex(ValueError, "another experiment_id"):
                fixed.bind_analysis(filepath)
        finally:
            fixed.close(timeout=3)
        wrong_server = AsyncBridge(client_factory=lambda: client, spool_directory=self.root / "wrong-server")
        try:
            wrong_server.bind_analysis(filepath)
            future = wrong_server.enqueue(type("Analysis", (), {"filepath": filepath})())
            with self.assertRaisesRegex(ValueError, "different eLabFTW server"):
                future.result(timeout=3)
        finally:
            with suppress(RuntimeError):
                wrong_server.close(timeout=3)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.experiment.files, {})


if __name__ == "__main__":
    unittest.main()
