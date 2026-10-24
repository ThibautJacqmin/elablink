"""Offline checks of the drop-in Labmate manager and automatic uploads."""

import io
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

from elabmate.exceptions import DuplicateTitle
from tests.fakes import OfflineExperiment


try:
    import h5py

    from labmate.acquisition import LifecycleHooks
    from labmate.acquisition_notebook import (
        AcquisitionAnalysisManager as LabmateAcquisitionAnalysisManager,
    )

    HAS_LABMATE_HOOK = hasattr(LifecycleHooks, "add_analysis_figure_saved")
except ImportError:
    HAS_LABMATE_HOOK = False


class FileFigure:
    """Save a small attachment without requiring a graphical backend."""

    def savefig(self, filename, **kwargs):
        Path(filename).write_bytes(b"figure data")


class MultiExperimentClient:
    """Keep separate remote experiments and mimic unique-title enforcement."""

    def __init__(self):
        self.configuration = SimpleNamespace(host="https://offline.invalid/api/v2")
        self.experiments = {}
        self.create_titles = []
        self.load_requests = []
        self.thread_ids = []

    def create_experiment(self, *, title):
        self.thread_ids.append(threading.get_ident())
        self.create_titles.append(title)
        if title in self.experiments:
            raise DuplicateTitle(title)
        experiment = OfflineExperiment()
        experiment.ID = len(self.experiments) + 1
        self.experiments[title] = experiment
        return experiment

    def load_experiment(self, *, ID=None, title=None):  # noqa: N803 - ElabClient API
        self.thread_ids.append(threading.get_ident())
        self.load_requests.append((ID, title))
        if title is not None:
            return self.experiments[title]
        return next(value for value in self.experiments.values() if value.ID == ID)


class OptionalLabmateTests(unittest.TestCase):
    def test_regular_elabmate_import_still_works_when_labmate_is_not_installed(self):
        script = textwrap.dedent(
            """
            import sys

            class BlockLabmate:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] == 'labmate':
                        raise ModuleNotFoundError(
                            "No module named 'labmate'", name='labmate'
                        )

            sys.meta_path.insert(0, BlockLabmate())
            import elabmate
            assert elabmate.ElabClient is not None
            assert elabmate.AsyncElabBridge is not None
            try:
                from elabmate import AcquisitionAnalysisManager
            except ModuleNotFoundError as error:
                assert error.name == 'labmate', error
            else:
                raise AssertionError('The manager unexpectedly imported without Labmate')
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(HAS_LABMATE_HOOK, "Requires Labmate with analysis_figure_saved")
class AcquisitionAnalysisManagerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.managers = []
        self.releases = []
        self.addCleanup(self.close_managers)

    def close_managers(self):
        for release in self.releases:
            release.set()
        for manager in self.managers:
            # Explicit tests below verify failures; cleanup must still drain workers.
            with suppress(RuntimeError):
                manager.close(timeout=5)

    def manager(self, client, **kwargs):
        from elabmate import AcquisitionAnalysisManager

        options = {
            "shell": None,
            "use_magic": False,
            "save_files": True,
            "config_path": self.root / "not-read.conf",
            "client_factory": lambda: client,
        }
        options.update(kwargs)
        manager = AcquisitionAnalysisManager(str(self.root / "data"), **options)
        self.managers.append(manager)
        return manager

    def acquire(self, manager, name="Sinusoid", **data):
        manager.acquisition_cell(name, cell="acquisition code")
        manager.save_acquisition(**(data or {"x": [0, 1, 2], "y": [0, 1, 0]}))

    def test_public_manager_is_a_true_subclass_and_saves_without_manual_attach(self):
        client = MultiExperimentClient()
        manager = self.manager(client)
        self.assertIsInstance(manager, LabmateAcquisitionAnalysisManager)
        self.assertEqual(manager.upload_status(), [])
        self.acquire(manager)
        manager.analysis_cell(cell="none")
        manager.save_fig(fig=FileFigure(), name="sinusoid.png", cell="plot code")
        result = manager.flush(timeout=5)

        self.assertEqual(result["title"], "Sinusoid")
        self.assertEqual(client.create_titles, ["Sinusoid"])
        files = client.experiments["Sinusoid"].files
        self.assertTrue(any(name.endswith(".h5") for name in files))
        self.assertTrue(any(name.endswith("_FIG_sinusoid.png") for name in files))
        h5_name = next(name for name in files if name.endswith(".h5"))
        with h5py.File(io.BytesIO(files[h5_name]), "r") as saved:
            self.assertEqual(
                saved["analysis_cells"]["sinusoid.png"][()].decode(), "plot code"
            )
        self.assertEqual(
            [job["state"] for job in manager.upload_status()], ["done", "done"]
        )
        spool = self.root / "data" / ".upload_queue"
        self.assertTrue(spool.is_dir())
        self.assertEqual(list(spool.iterdir()), [])

    def test_blocked_server_does_not_block_local_saves_and_keeps_stable_revisions(self):
        client = MultiExperimentClient()
        experiment = client.create_experiment(title="Sinusoid")
        started = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        revisions = []
        upload = experiment.upload_file

        def blocked_upload(filename):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test worker was not released")
            if Path(filename).suffix == ".h5":
                with h5py.File(filename, "r") as saved:
                    revisions.append(int(saved["revision"][()]))
            upload(filename)

        experiment.upload_file = blocked_upload
        manager = self.manager(client)
        self.acquire(manager, revision=1)
        self.assertTrue(started.wait(timeout=3))
        manager.save_acquisition(revision=2)
        manager.analysis_cell(cell="none")
        manager.save_fig(fig=FileFigure(), name="fit.png", cell="fit code")
        self.assertFalse(release.is_set())
        self.assertEqual(manager.upload_status()[0]["state"], "pending")
        with h5py.File(str(manager.current_filepath) + ".h5", "r") as saved:
            self.assertEqual(int(saved["revision"][()]), 2)
        release.set()
        manager.flush(timeout=5)

        self.assertEqual(revisions, [1, 2, 2])
        self.assertTrue(
            all(thread != threading.get_ident() for thread in experiment.thread_ids)
        )
        self.assertTrue(
            all(thread != threading.get_ident() for thread in client.thread_ids[1:])
        )

    def test_acquisition_names_route_to_separate_cached_experiments(self):
        client = MultiExperimentClient()
        manager = self.manager(client)
        for index, title in enumerate(("calibration", "sweep", "calibration")):
            self.acquire(manager, name=title, revision=index)
        result = manager.flush(timeout=5)

        self.assertEqual(client.create_titles, ["calibration", "sweep"])
        self.assertEqual(result["title"], "calibration")
        self.assertEqual(len(client.experiments), 2)
        for title, experiment in client.experiments.items():
            for name, content in experiment.files.items():
                if name.endswith(".h5"):
                    with h5py.File(io.BytesIO(content), "r") as saved:
                        revision = int(saved["revision"][()])
                    self.assertIn(revision, [0, 2] if title == "calibration" else [1])

    def test_reopened_analysis_routes_back_to_its_original_acquisition(self):
        client = MultiExperimentClient()
        manager = self.manager(client)
        self.acquire(manager, name="calibration")
        old_filepath = str(manager.current_filepath)
        self.acquire(manager, name="sweep")
        manager.analysis_cell(filepath=old_filepath, cell="none")
        self.assertIsNone(manager.current_acquisition)
        manager.save_fig(fig=FileFigure(), name="old-fit.png", cell="old fit code")
        result = manager.flush(timeout=5)

        self.assertEqual(result["title"], "calibration")
        self.assertEqual(client.create_titles, ["calibration", "sweep"])
        self.assertTrue(
            any(
                name.endswith("_FIG_old-fit.png")
                for name in client.experiments["calibration"].files
            )
        )
        self.assertFalse(
            any(
                name.endswith("_FIG_old-fit.png")
                for name in client.experiments["sweep"].files
            )
        )

    def test_fresh_manager_resolves_existing_title_for_old_data(self):
        client = MultiExperimentClient()
        original = self.manager(client)
        self.acquire(original, name="calibration")
        filepath = str(original.current_filepath)
        original.close(timeout=5)
        experiment = client.experiments["calibration"]

        reopened = self.manager(client)
        reopened.analysis_cell(filepath=filepath, cell="none")
        reopened.save_fig(fig=FileFigure(), name="reopened.png", cell="new code")
        result = reopened.flush(timeout=5)

        self.assertEqual(result["id"], experiment.ID)
        self.assertEqual(len(client.experiments), 1)
        self.assertIn((None, "calibration"), client.load_requests)
        self.assertTrue(
            any(name.endswith("_FIG_reopened.png") for name in experiment.files)
        )

    def test_explicit_experiment_title_overrides_acquisition_names(self):
        client = MultiExperimentClient()
        manager = self.manager(client, experiment_title="Shared experiment")
        self.acquire(manager, name="calibration")
        self.acquire(manager, name="sweep")
        result = manager.flush(timeout=5)
        self.assertEqual(client.create_titles, ["Shared experiment"])
        self.assertEqual(result["title"], "Shared experiment")

    def test_existing_experiment_id_never_creates_an_experiment(self):
        client = MultiExperimentClient()
        experiment = client.create_experiment(title="Previously created")
        client.create_titles.clear()
        manager = self.manager(client, experiment_id=experiment.ID)
        self.acquire(manager, name="calibration")
        self.acquire(manager, name="sweep")
        result = manager.flush(timeout=5)

        self.assertEqual(result["id"], experiment.ID)
        self.assertEqual(client.create_titles, [])
        self.assertEqual(client.load_requests, [(experiment.ID, None)])
        self.assertTrue(experiment.files)

    def test_shared_hooks_and_labmate_options_are_preserved(self):
        client = MultiExperimentClient()
        hooks = LifecycleHooks()
        saved = []
        figures = []
        hooks.add_acquisition_saved(
            lambda acquisition, **kwargs: saved.append(acquisition)
        )
        hooks.add_analysis_figure_saved(
            lambda analysis, **kwargs: figures.append(analysis)
        )
        manager = self.manager(
            client,
            hooks=hooks,
            save_on_edit=False,
            save_on_edit_analysis=False,
            save_fig_inside_h5=False,
        )
        self.acquire(manager)
        manager.analysis_cell(cell="none")
        manager.save_fig(fig=FileFigure(), name="fit.png", cell="fit code")
        manager.close(timeout=5)

        self.assertIs(manager.hooks, hooks)
        self.assertIsNone(manager.shell)
        self.assertFalse(manager._save_on_edit)
        self.assertFalse(manager._save_on_edit_analysis)
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(figures), 1)
        self.assertEqual(len(manager.upload_status()), 2)

    def test_close_waits_for_uploads_and_context_manager_uses_same_instance(self):
        client = MultiExperimentClient()
        manager = self.manager(client)
        with manager as entered:
            self.assertIs(entered, manager)
            self.acquire(manager)
        self.assertTrue(client.experiments["Sinusoid"].files)
        self.assertTrue(all(job["state"] == "done" for job in manager.upload_status()))
        manager.close(timeout=5)

    def test_repeated_close_after_timeout_still_waits_for_pending_upload(self):
        client = MultiExperimentClient()
        experiment = client.create_experiment(title="Sinusoid")
        started = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        upload = experiment.upload_file

        def blocked_upload(filename):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test worker was not released")
            upload(filename)

        experiment.upload_file = blocked_upload
        manager = self.manager(client)
        self.acquire(manager)
        self.assertTrue(started.wait(timeout=3))
        with self.assertRaises(TimeoutError):
            manager.close(timeout=0.01)
        with self.assertRaises(TimeoutError):
            manager.close(timeout=0.01)
        release.set()
        manager.close(timeout=5)
        self.assertEqual(manager.upload_status()[0]["state"], "done")

    def test_close_surfaces_upload_failure_and_keeps_local_data_and_snapshot(self):
        client = MultiExperimentClient()
        experiment = client.create_experiment(title="Sinusoid")

        def failed_upload(filename):
            raise ConnectionError("server unavailable")

        experiment.upload_file = failed_upload
        manager = self.manager(client)
        self.acquire(manager)
        with self.assertRaisesRegex(RuntimeError, "failed") as raised:
            manager.close(timeout=5)
        self.assertIsInstance(raised.exception.__cause__, ConnectionError)
        self.assertEqual(manager.upload_status()[0]["state"], "failed")
        self.assertTrue(Path(str(manager.current_filepath) + ".h5").is_file())
        self.assertTrue(list((self.root / "data" / ".upload_queue").rglob("*.h5")))

    def test_context_cleanup_does_not_mask_an_exception_in_the_notebook(self):
        client = MultiExperimentClient()
        experiment = client.create_experiment(title="Sinusoid")

        def failed_upload(filename):
            raise ConnectionError("server unavailable")

        experiment.upload_file = failed_upload
        manager = self.manager(client)
        with self.assertLogs("elabmate.async_bridge", level="ERROR"):
            with self.assertRaisesRegex(ValueError, "analysis failed"):
                with manager:
                    self.acquire(manager)
                    raise ValueError("analysis failed")
        self.assertEqual(manager.upload_status()[0]["state"], "failed")


if __name__ == "__main__":
    unittest.main()
