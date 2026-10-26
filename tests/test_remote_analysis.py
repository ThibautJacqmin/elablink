"""Offline checks of analysis reopened from an eLabFTW attachment."""

import io
import json
import tempfile
import threading
import unittest
from contextlib import suppress
from pathlib import Path

from tests.test_acquisition_analysis_manager import HAS_LABMATE_HOOK, FileFigure
from tests.test_async_experiment import EditableClient, EditableExperiment


class DownloadableExperiment(EditableExperiment):
    """Expose the attachment methods used by the synchronous experiment API."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.downloads = []
        self.file_rows = None
        self.download_error = None
        self.download_started = threading.Event()
        self.download_release = None

    def list_files(self):
        self.thread_ids.append(threading.get_ident())
        if self.file_rows is not None:
            return self.file_rows
        return [
            {"id": index + 1, "real_name": name, "name": name}
            for index, name in enumerate(self.files)
        ]

    def get_file(self, filename):
        return next(
            (
                row
                for row in self.list_files()
                if filename in (row.get("real_name"), row.get("name"))
            ),
            None,
        )

    def download_file(self, file_id, destination):
        self.thread_ids.append(threading.get_ident())
        self.download_started.set()
        if self.download_release is not None:
            if not self.download_release.wait(timeout=5):
                raise TimeoutError("Test download was not released")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.downloads.append((file_id, destination))
        if self.download_error is not None:
            destination.write_bytes(b"partial download")
            raise self.download_error
        destination.write_bytes(list(self.files.values())[int(file_id) - 1])
        return destination


class DownloadableClient(EditableClient):
    def create_experiment(self, *, title):
        previous = super().create_experiment(title=title)
        experiment = DownloadableExperiment()
        experiment.ID = previous.ID
        experiment.title = title
        self.experiments[title] = experiment
        return experiment


@unittest.skipUnless(HAS_LABMATE_HOOK, "Requires current Labmate")
class RemoteAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.managers = []
        self.releases = []
        self.addCleanup(self.close_managers)
        self.client = DownloadableClient()
        self.remote = self.seed("Remote A", revision=1)
        self.other = self.seed("Remote B", revision=2)
        self.client.create_titles.clear()
        self.client.thread_ids.clear()

    def close_managers(self):
        for release in self.releases:
            release.set()
        for manager in self.managers:
            with suppress(RuntimeError, TimeoutError):
                manager.close(timeout=5)

    def seed(self, title, *, revision):
        import h5py

        content = io.BytesIO()
        with h5py.File(content, "w") as saved:
            saved["x"] = [0, 1, 2]
            saved["y"] = [0, revision, 0]
            saved["revision"] = revision
        remote = self.client.create_experiment(title=title)
        remote.files["run.h5"] = content.getvalue()
        return remote

    def manager(self, **kwargs):
        from elablink import AcquisitionAnalysisManager

        manager = AcquisitionAnalysisManager(
            str(self.root / "data"),
            shell=None,
            use_magic=False,
            save_files=True,
            client_factory=lambda: self.client,
            **kwargs,
        )
        self.managers.append(manager)
        return manager

    def open_remote(self, manager, remote=None, **kwargs):
        return manager.analysis_cell(
            experiment_id=(remote or self.remote).ID,
            filename="run.h5",
            cell="none",
            **kwargs,
        )

    def acquire(self, manager, name="local run"):
        manager.acquisition_cell(name, cell="acquisition code")
        manager.save_acquisition(x=[0, 1], y=[0, 1])

    @staticmethod
    def filepath(manager):
        path = Path(str(manager.data.filepath))
        return path if str(path).endswith(".h5") else Path(str(path) + ".h5")

    def test_opens_a_remote_hdf5_with_the_normal_analysis_interface(self):
        manager = self.manager()
        self.assertIs(self.open_remote(manager), manager)
        self.assertEqual(list(manager.data.x), [0, 1, 2])
        self.assertEqual(int(manager.data.revision), 1)
        self.assertIsNone(manager.current_acquisition)
        path = self.filepath(manager)
        self.assertEqual(path.name, "run.h5")
        self.assertTrue(path.is_relative_to(self.root / "data" / ".elabftw"))
        self.assertEqual(self.client.create_titles, [])
        self.assertEqual(self.client.load_requests, [(self.remote.ID, None)])
        self.assertTrue(self.remote.downloads)
        self.assertTrue(all(t != threading.get_ident() for t in self.client.thread_ids))
        self.assertTrue(all(t != threading.get_ident() for t in self.remote.thread_ids))

    def test_remote_handle_is_available_before_saving_any_figure(self):
        manager = self.manager()
        self.open_remote(manager)
        manager.experiment.add_step("Reanalyse the old acquisition")
        result = manager.flush(timeout=5)
        self.assertEqual(result["id"], self.remote.ID)
        self.assertEqual(self.remote.steps, ["Reanalyse the old acquisition"])
        self.assertEqual(self.client.create_titles, [])

    def test_analysis_figures_upload_to_the_original_experiment(self):
        manager = self.manager()
        self.open_remote(manager)
        manager.save_fig(fig=FileFigure(), name="fit.png", cell="fit code")
        result = manager.flush(timeout=5)
        self.assertEqual(result["id"], self.remote.ID)
        self.assertTrue(any("fit.png" in name for name in self.remote.files))
        self.assertFalse(any("fit.png" in name for name in self.other.files))
        self.assertEqual(self.client.create_titles, [])

    def test_remote_handles_remain_bound_while_switching_remote_and_local_data(self):
        manager = self.manager()
        self.open_remote(manager)
        first_path = self.filepath(manager)
        first = manager.experiment
        self.open_remote(manager, self.other)
        second = manager.experiment
        self.acquire(manager)
        first.add_step("Only remote A")
        second.add_comment("Only remote B")
        manager.experiment.add_tag("Only local")
        manager.analysis_cell(filepath=first_path, cell="none")
        manager.save_fig(fig=FileFigure(), name="reopened.png", cell="fit code")
        manager.flush(timeout=5)
        self.assertEqual(self.remote.steps, ["Only remote A"])
        self.assertEqual(self.other.comments, ["Only remote B"])
        self.assertEqual(self.client.experiments["local run"].tags, ["Only local"])
        self.assertTrue(any("reopened.png" in name for name in self.remote.files))
        self.assertEqual(self.client.create_titles, ["local run"])

    def test_explicit_remote_id_overrides_fixed_title_only_for_that_analysis(self):
        manager = self.manager(experiment_title="Shared local")
        self.open_remote(manager)
        manager.experiment.add_step("Remote analysis")
        self.acquire(manager)
        manager.experiment.add_comment("Local acquisition")
        manager.flush(timeout=5)
        self.assertEqual(self.remote.steps, ["Remote analysis"])
        self.assertEqual(self.client.create_titles, ["Shared local"])
        self.assertEqual(
            self.client.experiments["Shared local"].comments, ["Local acquisition"]
        )

    def test_remote_reopening_reuses_the_local_file_without_overwriting_changes(self):
        import h5py

        manager = self.manager()
        self.open_remote(manager)
        first = self.filepath(manager)
        with h5py.File(first, "a") as saved:
            saved["local_note"] = "Keep my analysis"
        original = first.read_bytes()
        self.open_remote(manager)
        second = self.filepath(manager)
        self.assertEqual(first, second)
        self.assertEqual(first.read_bytes(), original)
        self.assertIn("local_note", manager.data)
        self.assertEqual(len(self.remote.downloads), 1)

    def test_a_new_manager_reuses_the_associated_local_file_for_remote_open(self):
        first = self.manager()
        self.open_remote(first)
        path = self.filepath(first)
        first.close(timeout=5)
        reopened = self.manager()
        self.open_remote(reopened)
        self.assertEqual(self.filepath(reopened), path)
        self.assertEqual(len(self.remote.downloads), 1)
        reopened.experiment.add_comment("Reopened through the experiment ID")
        reopened.flush(timeout=5)
        self.assertEqual(self.remote.comments, ["Reopened through the experiment ID"])
        self.assertEqual(self.client.create_titles, [])

    def test_missing_associated_local_file_is_downloaded_into_a_new_working_copy(self):
        manager = self.manager()
        self.open_remote(manager)
        original = self.filepath(manager)
        original.unlink()
        self.open_remote(manager)
        downloaded = self.filepath(manager)
        self.assertNotEqual(downloaded, original)
        self.assertTrue(downloaded.is_file())
        self.assertEqual(int(manager.data.revision), 1)
        self.assertEqual(len(self.remote.downloads), 2)

    def test_remote_open_reuses_the_original_uploaded_acquisition_file(self):
        manager = self.manager()
        self.acquire(manager, "Previously acquired")
        original = Path(str(manager.current_filepath) + ".h5").resolve()
        result = manager.flush(timeout=5)
        experiment = self.client.experiments["Previously acquired"]
        before = original.read_bytes()
        manager.analysis_cell(
            experiment_id=result["id"], filename=original.name, cell="none"
        )
        self.assertEqual(self.filepath(manager), original)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(experiment.downloads, [])
        manager.experiment.add_comment("Analysis on the original local data")
        manager.flush(timeout=5)
        self.assertEqual(experiment.comments, ["Analysis on the original local data"])

    def test_a_new_manager_finds_the_original_acquisition_from_the_local_index(self):
        first = self.manager()
        self.acquire(first, "Previously acquired")
        original = Path(str(first.current_filepath) + ".h5").resolve()
        result = first.flush(timeout=5)
        first.close(timeout=5)
        experiment = self.client.experiments["Previously acquired"]
        reopened = self.manager()
        reopened.analysis_cell(
            experiment_id=result["id"], filename=original.name, cell="none"
        )
        self.assertEqual(self.filepath(reopened), original)
        self.assertEqual(experiment.downloads, [])
        reopened.experiment.add_step("Reanalysis after restarting the notebook")
        reopened.flush(timeout=5)
        self.assertEqual(experiment.steps, ["Reanalysis after restarting the notebook"])

    def test_a_new_manager_reopens_downloaded_data_without_another_download(self):
        first = self.manager()
        self.open_remote(first)
        path = self.filepath(first)
        first.close(timeout=5)
        reopened = self.manager()
        reopened.analysis_cell(filepath=path, cell="none")
        reopened.experiment.add_comment("Later analysis")
        reopened.save_fig(fig=FileFigure(), name="later.png", cell="later code")
        result = reopened.flush(timeout=5)
        self.assertEqual(result["id"], self.remote.ID)
        self.assertEqual(self.remote.comments, ["Later analysis"])
        self.assertTrue(any("later.png" in name for name in self.remote.files))
        self.assertEqual(len(self.remote.downloads), 1)
        self.assertEqual(self.client.create_titles, [])

    def test_working_copy_has_local_source_metadata_not_uploaded_to_elabftw(self):
        manager = self.manager()
        self.open_remote(manager)
        manifest = self.filepath(manager).parent / ".elablink.json"
        source = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(source["version"], 1)
        self.assertEqual(source["experiment_id"], self.remote.ID)
        self.assertEqual(source["filename"], "run.h5")
        self.assertEqual(source["title"], "Remote A")
        self.assertIn("offline.invalid", source["server"])
        manager.save_fig(fig=FileFigure(), name="fit.png", cell="fit code")
        manager.flush(timeout=5)
        self.assertNotIn(".elablink.json", self.remote.files)

    def test_local_analysis_filepath_keeps_its_normal_behavior(self):
        manager = self.manager()
        self.acquire(manager)
        local_path = str(manager.current_filepath)
        returned = manager.analysis_cell(filepath=local_path, cell="none")
        self.assertIs(returned, manager)
        self.assertEqual(list(manager.data.y), [0, 1])
        self.assertEqual(self.remote.downloads, [])

    def test_missing_attachment_preserves_the_active_analysis(self):
        manager = self.manager()
        self.open_remote(manager)
        previous = manager.current_analysis
        with self.assertRaises((FileNotFoundError, ValueError)):
            manager.analysis_cell(
                experiment_id=self.other.ID, filename="absent.h5", cell="none"
            )
        self.assertIs(manager.current_analysis, previous)
        self.assertEqual(self.other.downloads, [])
        self.assertEqual(self.client.create_titles, [])

    def test_duplicate_attachment_names_are_not_silently_chosen(self):
        manager = self.manager()
        self.remote.file_rows = [
            {"id": 1, "real_name": "run.h5", "name": "run.h5"},
            {"id": 2, "real_name": "run.h5", "name": "run.h5"},
        ]
        with self.assertRaises(ValueError):
            self.open_remote(manager)
        self.assertIsNone(manager.current_analysis)
        self.assertEqual(self.remote.downloads, [])

    def test_attachment_name_is_used_when_real_name_is_absent(self):
        manager = self.manager()
        self.remote.file_rows = [{"id": 1, "name": "run.h5"}]
        self.open_remote(manager)
        self.assertEqual(int(manager.data.revision), 1)
        self.assertEqual(len(self.remote.downloads), 1)

    def test_download_failure_does_not_activate_or_leave_partial_files(self):
        manager = self.manager()
        self.open_remote(manager)
        previous = manager.current_analysis
        before = {p for p in self.root.rglob("*") if p.is_file()}
        self.other.download_error = ConnectionError("Download interrupted")
        with self.assertRaises(ConnectionError):
            self.open_remote(manager, self.other)
        self.assertIs(manager.current_analysis, previous)
        after = {p for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(self.client.create_titles, [])

    def test_invalid_hdf5_is_rejected_before_activating_a_new_analysis(self):
        manager = self.manager()
        self.open_remote(manager)
        previous = manager.current_analysis
        before = {p for p in self.root.rglob("*") if p.is_file()}
        self.other.files["run.h5"] = b"not an HDF5 file"
        with self.assertRaises((ValueError, OSError)):
            self.open_remote(manager, self.other)
        self.assertIs(manager.current_analysis, previous)
        after = {p for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_download_timeout_does_not_switch_analysis_when_worker_finishes_later(self):
        manager = self.manager()
        self.open_remote(manager)
        previous = manager.current_analysis
        release = threading.Event()
        self.releases.append(release)
        self.other.download_release = release
        with self.assertRaises(TimeoutError):
            self.open_remote(manager, self.other, download_timeout=0.01)
        self.assertTrue(self.other.download_started.wait(timeout=3))
        self.assertIs(manager.current_analysis, previous)
        release.set()
        manager.flush(timeout=5)
        self.assertIs(manager.current_analysis, previous)
        manager.experiment.add_comment("Still editing A")
        manager.flush(timeout=5)
        self.assertEqual(self.remote.comments, ["Still editing A"])
        self.assertEqual(self.other.comments, [])

    def test_remote_filename_cannot_contain_paths_or_other_extensions(self):
        manager = self.manager()
        for filename in (
            "../run.h5",
            "folder/run.h5",
            r"folder\run.h5",
            r"C:\data\run.h5",
            "/run.h5",
            "run.png",
            "run.h5?x=1",
            "",
        ):
            with self.subTest(filename=filename):
                with self.assertRaises((TypeError, ValueError)):
                    manager.analysis_cell(
                        experiment_id=self.remote.ID, filename=filename, cell="none"
                    )
        self.assertEqual(self.client.load_requests, [])

    def test_remote_id_requires_a_positive_integer_and_filename(self):
        manager = self.manager()
        for experiment_id in (0, -1, True, "1", 1.5):
            with self.subTest(experiment_id=experiment_id):
                with self.assertRaises((TypeError, ValueError)):
                    manager.analysis_cell(
                        experiment_id=experiment_id, filename="run.h5", cell="none"
                    )
        with self.assertRaises((TypeError, ValueError)):
            manager.analysis_cell(experiment_id=self.remote.ID, cell="none")
        self.assertEqual(self.client.load_requests, [])

    def test_remote_arguments_cannot_be_combined_with_a_local_filepath(self):
        manager = self.manager()
        with self.assertRaises((TypeError, ValueError)):
            manager.analysis_cell(
                experiment_id=self.remote.ID,
                filename="run.h5",
                filepath=self.root / "local.h5",
                cell="none",
            )
        self.assertEqual(self.client.load_requests, [])

    def test_fixed_experiment_id_conflict_is_rejected_before_network_access(self):
        manager = self.manager(experiment_id=self.remote.ID)
        with self.assertRaises(ValueError):
            self.open_remote(manager, self.other)
        self.assertEqual(self.client.load_requests, [])
        self.assertEqual(self.client.create_titles, [])

    def test_same_fixed_experiment_id_can_be_opened_remotely(self):
        manager = self.manager(experiment_id=self.remote.ID)
        self.open_remote(manager)
        manager.experiment.add_comment("Same explicit record")
        manager.flush(timeout=5)
        self.assertEqual(self.remote.comments, ["Same explicit record"])
        self.assertEqual(self.client.create_titles, [])

    def test_local_reopening_cannot_upload_to_another_server_with_the_same_id(self):
        first = self.manager()
        self.open_remote(first)
        path = self.filepath(first)
        first.close(timeout=5)
        loaded = len(self.client.load_requests)
        self.client.configuration.host = "https://another.invalid/api/v2"
        reopened = self.manager()
        reopened.analysis_cell(filepath=path, cell="none")
        reopened.save_fig(fig=FileFigure(), name="wrong-server.png", cell="fit code")
        with self.assertRaisesRegex(RuntimeError, "failed"):
            reopened.flush(timeout=5)
        self.assertEqual(len(self.client.load_requests), loaded)
        self.assertFalse(any("wrong-server.png" in name for name in self.remote.files))


if __name__ == "__main__":
    unittest.main()
