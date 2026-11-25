"""Keep an original acquisition's remote destination across analysis projects."""

from contextlib import suppress
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from elablink import AsyncBridge
from elablink.remote_analysis import RemoteSource, read_source, remember_source, write_source_manifest
from tests.fakes import OfflineClient
from tests.test_acquisition_analysis_manager import HAS_LABMATE_HOOK, FileFigure, MultiExperimentClient


class OriginalSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.original = self.root / "acquisition" / "Sinusoid" / "run.h5"
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b"saved measurement")
        self.source = RemoteSource(1, "Original experiment", "https://offline.invalid/api/v2")
        self.catalog = self.root / "acquisition" / ".elabftw"
        self.analysis_catalog = self.root / "analysis" / ".elabftw"
        self.bridges = []
        self.releases = []
        self.addCleanup(self.close_bridges)

    def close_bridges(self):
        for release in self.releases:
            release.set()
        for bridge in self.bridges:
            with suppress(RuntimeError):
                bridge.close(timeout=5)

    def bridge(self, factory, **kwargs):
        bridge = AsyncBridge("Original experiment", spool_directory=self.root / "spool",
                             analysis_directory=self.analysis_catalog, client_factory=factory, **kwargs)
        self.bridges.append(bridge)
        return bridge

    def blocked_bridge(self, client, **kwargs):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def factory():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release worker")
            return client

        return self.bridge(factory, **kwargs), entered, release

    def test_old_catalog_is_found_by_exact_original_path_from_another_project(self):
        remember_source(self.original, self.source, self.catalog)
        self.assertEqual(read_source(self.original, self.analysis_catalog), self.source)
        neighbor = self.original.parent / "other" / self.original.name
        neighbor.parent.mkdir()
        neighbor.write_bytes(b"another run with the same filename")
        self.assertIsNone(read_source(neighbor, self.analysis_catalog))

    def test_conflicting_matching_catalogs_are_rejected_and_sidecar_has_priority(self):
        remember_source(self.original, self.source, self.catalog)
        other = RemoteSource(2, "Different experiment", self.source.server)
        remember_source(self.original, other, self.analysis_catalog)
        with self.assertRaisesRegex(ValueError, "Conflicting eLabFTW origins"):
            read_source(self.original, self.analysis_catalog)
        write_source_manifest(self.original, self.source)
        self.assertEqual(read_source(self.original, self.analysis_catalog), self.source)

    def test_fixed_experiment_conflict_is_rejected_before_queuing_upload(self):
        write_source_manifest(self.original, self.source)
        client = OfflineClient()
        bridge = self.bridge(lambda: client, experiment_id=2)
        with self.assertRaisesRegex(ValueError, "another experiment_id"):
            bridge.enqueue(SimpleNamespace(filepath=self.original))
        self.assertEqual(bridge.status(), [])
        self.assertEqual(client.created, 0)
        self.assertEqual(client.experiment.files, {})

    def test_origin_added_after_capture_is_checked_before_file_upload(self):
        client = OfflineClient()

        bridge, entered, release = self.blocked_bridge(client, experiment_id=1)
        bridge.enqueue(SimpleNamespace(filepath=self.original))
        self.assertTrue(entered.wait(3))
        write_source_manifest(self.original, RemoteSource(2, "Other", self.source.server))
        release.set()
        with self.assertRaisesRegex(RuntimeError, "different eLabFTW experiment"):
            bridge.flush(timeout=5)
        self.assertEqual(client.experiment.files, {})
        self.assertTrue(list(bridge.spool_directory.rglob("*.h5")))

    def test_late_origin_does_not_create_a_record_from_the_queued_title(self):
        client = MultiExperimentClient()
        other = client.create_experiment(title="Different experiment")
        client.create_titles.clear()

        bridge, entered, release = self.blocked_bridge(client)
        bridge.enqueue(SimpleNamespace(filepath=self.original))
        self.assertTrue(entered.wait(3))
        write_source_manifest(self.original, RemoteSource(other.ID, "Different experiment", self.source.server))
        release.set()

        with self.assertRaisesRegex(RuntimeError, "origin changed"):
            bridge.flush(timeout=5)
        self.assertEqual(client.create_titles, [])
        self.assertEqual(other.files, {})
        self.assertTrue(list(bridge.spool_directory.rglob("*.h5")))

    def test_queued_saves_accept_the_origin_recorded_by_the_first_save(self):
        client = OfflineClient()

        bridge, entered, release = self.blocked_bridge(client)
        data = SimpleNamespace(filepath=self.original)
        bridge.enqueue(data)
        self.assertTrue(entered.wait(3))
        self.original.write_bytes(b"updated measurement")
        bridge.enqueue(data)
        release.set()

        self.assertEqual(bridge.flush(timeout=5)["id"], self.source.experiment_id)
        self.assertEqual(client.created, 1)
        self.assertEqual(client.experiment.files, {"run.h5": b"updated measurement"})

    def test_removed_original_does_not_prevent_uploading_the_captured_checkpoint(self):
        client = OfflineClient()

        bridge, entered, release = self.blocked_bridge(client)
        bridge.enqueue(SimpleNamespace(filepath=self.original))
        self.assertTrue(entered.wait(3))
        self.original.unlink()
        release.set()
        self.assertEqual(bridge.flush(timeout=5)["id"], 1)
        self.assertEqual(client.experiment.files, {"run.h5": b"saved measurement"})
        self.assertEqual(read_source(self.original, self.analysis_catalog), self.source)
        self.assertEqual(list(bridge.spool_directory.iterdir()), [])

    def test_original_removed_during_sidecar_write_keeps_the_checkpoint_successful(self):
        client = OfflineClient()
        bridge = self.bridge(lambda: client)

        def remove_before_write(filepath, source, **kwargs):
            filepath.unlink()
            return write_source_manifest(filepath, source, **kwargs)

        with patch("elablink.async_bridge.write_source_manifest", side_effect=remove_before_write):
            bridge.enqueue(SimpleNamespace(filepath=self.original))
            bridge.add_step("Uploaded")
            self.assertEqual(bridge.flush(timeout=5)["id"], 1)

        self.assertEqual(client.experiment.files, {"run.h5": b"saved measurement"})
        self.assertEqual(client.experiment.steps, ["Uploaded"])
        self.assertFalse(self.original.with_suffix(".h5.elablink.json").exists())
        self.assertEqual(read_source(self.original, self.analysis_catalog), self.source)
        self.assertEqual(list(bridge.spool_directory.iterdir()), [])
        with self.assertRaises(FileNotFoundError):
            write_source_manifest(self.original, self.source)

    def test_metadata_write_failure_is_reported_and_snapshot_is_retained(self):
        client = OfflineClient()
        bridge = self.bridge(lambda: client)
        with patch("elablink.async_bridge.write_source_manifest", side_effect=OSError("Cannot save origin")):
            bridge.enqueue(SimpleNamespace(filepath=self.original))
            with self.assertRaisesRegex(RuntimeError, "Cannot save origin"):
                bridge.flush(timeout=5)
        self.assertEqual(bridge.status()[0]["state"], "failed")
        self.assertTrue(list(bridge.spool_directory.rglob("*.h5")))


@unittest.skipUnless(HAS_LABMATE_HOOK, "Requires Labmate with figure_saved(AnalysisData)")
class OriginalAcquisitionReanalysisTests(unittest.TestCase):
    def test_new_project_keeps_origin_for_new_and_preexisting_acquisitions(self):
        from elablink import AcquisitionAnalysisManager

        for catalog_only in (False, True):
            with self.subTest(catalog_only=catalog_only), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                client = MultiExperimentClient()
                options = {"shell": None, "use_magic": False, "save_files": True,
                           "client_factory": lambda: client}
                with AcquisitionAnalysisManager(str(root / "acquisition"),
                                                experiment_title="Actual destination", **options) as first:
                    first.acquisition_cell("Sinusoid", cell="none")
                    first.save_acquisition(x=[1, 2, 3])
                    original = Path(str(first.current_filepath) + ".h5")
                    acquired = first.flush(timeout=5)
                manifest = original.with_name(original.name + ".elablink.json")
                self.assertTrue(manifest.is_file())
                self.assertEqual(json.loads(manifest.read_text())["experiment_id"], acquired["id"])
                if catalog_only:
                    manifest.unlink()  # Existing acquisitions from before sidecars were introduced.
                with AcquisitionAnalysisManager(str(root / "analysis"), **options) as second:
                    second.analysis_cell(filepath=original, cell="none")
                    second.save_fig(fig=FileFigure(), name="reanalysis.png", cell="none")
                    result = second.flush(timeout=5)
                self.assertEqual(result["id"], acquired["id"])
                self.assertEqual(client.create_titles, ["Actual destination"])
                self.assertTrue(manifest.is_file())
                remote = client.experiments["Actual destination"]
                self.assertTrue(any(name.endswith("reanalysis.png") for name in remote.files))
                self.assertFalse(any(name.endswith(".elablink.json") for name in remote.files))
