"""Offline checks of the public asynchronous bridge and Labmate integration."""

import io
import tempfile
import threading
import unittest
from contextlib import suppress
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from elabmate import AsyncElabBridge
from elabmate.async_bridge import _set_request_timeouts

from tests.fakes import OfflineClient


try:
    import h5py

    from labmate.acquisition import LifecycleHooks
    from labmate.acquisition_notebook import AcquisitionAnalysisManager

    HAS_LABMATE_HOOK = hasattr(LifecycleHooks, "add_analysis_figure_saved")
except ImportError:
    HAS_LABMATE_HOOK = False


class FileFigure:
    """Exercise figure callbacks without needing a graphical plotting backend."""

    def savefig(self, filename, **kwargs):
        Path(filename).write_bytes(b"figure data")


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.adapters = []
        self.addCleanup(self.close_adapters)

    def close_adapters(self):
        for adapter in self.adapters:
            # Failure assertions below verify surfaced errors and retained files.
            with suppress(RuntimeError):
                adapter.close(timeout=3)

    def adapter(self, client, **kwargs):
        adapter = AsyncElabBridge(
            "offline example",
            config_path=self.root / "not-read.conf",
            spool_directory=self.root / "spool",
            client_factory=lambda: client,
            **kwargs,
        )
        self.adapters.append(adapter)
        return adapter


class AsyncBridgeTests(BridgeTestCase):
    def data(self):
        path = self.root / "run.h5"
        path.write_bytes(b"saved data")
        return SimpleNamespace(filepath=path)

    def test_configuration_file_is_required_without_a_custom_factory(self):
        with self.assertRaises(FileNotFoundError):
            AsyncElabBridge(
                "Sinusoid",
                config_path=self.root / "missing.conf",
                spool_directory=self.root / "spool",
            )
        self.assertFalse((self.root / "spool").exists())

    def test_default_client_is_created_lazily_on_the_upload_worker(self):
        client = OfflineClient()
        config = self.root / "example.conf"
        config.touch()
        caller = threading.get_ident()
        threads = []

        def make_client(config_path):
            self.assertEqual(Path(config_path), config.resolve())
            threads.append(threading.get_ident())
            return client

        with (
            patch(
                "elabmate.async_bridge.ElabClient", side_effect=make_client
            ) as factory,
            patch("elabmate.async_bridge._set_request_timeouts") as set_timeouts,
        ):
            adapter = AsyncElabBridge(
                "Sinusoid", config_path=config, spool_directory=self.root / "spool"
            )
            self.adapters.append(adapter)
            factory.assert_not_called()
            adapter.enqueue(self.data())
            adapter.flush(timeout=3)
            set_timeouts.assert_called_once_with(client, (5, 30))
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], caller)

    def test_attach_validates_both_hooks_before_registering(self):
        adapter = self.adapter(OfflineClient())
        old_hooks = SimpleNamespace(add_acquisition_saved=Mock())
        with self.assertRaisesRegex(TypeError, "analysis_figure_saved"):
            adapter.attach(old_hooks)
        old_hooks.add_acquisition_saved.assert_not_called()
        hooks = SimpleNamespace(
            add_acquisition_saved=Mock(), add_analysis_figure_saved=Mock()
        )
        adapter.attach(hooks)
        with self.assertRaisesRegex(RuntimeError, "already attached"):
            adapter.attach(hooks)
        hooks.add_acquisition_saved.assert_called_once()
        hooks.add_analysis_figure_saved.assert_called_once()

    def test_close_drains_work_and_rejects_further_submissions(self):
        client = OfflineClient()
        adapter = self.adapter(client)
        data = self.data()
        with adapter:
            adapter.enqueue(data)
        self.assertEqual(client.experiment.files, {"run.h5": b"saved data"})
        with self.assertRaisesRegex(RuntimeError, "closed"):
            adapter.enqueue(data)
        adapter.close()

    def test_context_does_not_mask_the_original_exception_on_upload_failure(self):
        client = OfflineClient()
        client.experiment.upload_file = Mock(
            side_effect=ConnectionError("server unavailable")
        )
        adapter = self.adapter(client)
        with self.assertLogs("elabmate.async_bridge", level="ERROR"):
            with self.assertRaisesRegex(ValueError, "analysis failed"):
                with adapter:
                    adapter.enqueue(self.data())
                    raise ValueError("analysis failed")
        self.assertEqual(adapter.status()[0]["state"], "failed")

    def test_submit_and_flush_must_use_the_creating_thread(self):
        adapter = self.adapter(OfflineClient())
        data = self.data()
        with ThreadPoolExecutor(max_workers=1) as worker:
            for method in (lambda: adapter.enqueue(data), adapter.flush, adapter.close):
                with self.assertRaisesRegex(RuntimeError, "creating notebook thread"):
                    worker.submit(method).result(timeout=3)
        self.assertEqual(adapter.flush(), {})

    def test_cancelled_pending_upload_is_reported_and_its_snapshot_is_retained(self):
        client = OfflineClient()
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        original_upload = client.experiment.upload_file

        def blocked_upload(filename):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test worker was not released")
            original_upload(filename)

        client.experiment.upload_file = blocked_upload
        adapter = self.adapter(client)
        data = self.data()
        adapter.enqueue(data)
        self.assertTrue(started.wait(timeout=3))
        cancelled = adapter.enqueue(data)
        self.assertTrue(cancelled.cancel())
        self.assertEqual(adapter.status()[1]["state"], "failed")
        release.set()
        with self.assertRaises(RuntimeError) as raised:
            adapter.flush(timeout=3)
        self.assertIsInstance(raised.exception.__cause__, CancelledError)
        self.assertEqual(len(list(adapter.spool_directory.rglob("*.h5"))), 1)


@unittest.skipUnless(HAS_LABMATE_HOOK, "Requires Labmate with analysis_figure_saved")
class AsyncBridgeIntegrationTests(BridgeTestCase):
    def manager(self):
        return AcquisitionAnalysisManager(
            str(self.root / "data"), shell=None, use_magic=False, save_files=True
        )

    def test_blocked_upload_does_not_block_local_saves_and_preserves_revisions(self):
        client = OfflineClient()
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        revisions = []
        original_upload = client.experiment.upload_file

        def blocked_upload(filename):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test worker was not released")
            if Path(filename).suffix == ".h5":
                with h5py.File(filename, "r") as saved:
                    revisions.append(int(saved["revision"][()]))
            original_upload(filename)

        client.experiment.upload_file = blocked_upload
        manager = self.manager()
        adapter = self.adapter(client)
        adapter.attach(manager.hooks)
        manager.acquisition_cell("calibration", cell="none")
        manager.save_acquisition(revision=1, x=[1, 2, 3])
        self.assertTrue(started.wait(timeout=3))
        manager.save_acquisition(revision=2, x=[4, 5, 6])
        manager.analysis_cell(cell="none")
        manager.save_fig(fig=FileFigure(), name="fit.png", cell="fit code")
        adapter.add_step("Fit complete")
        self.assertFalse(release.is_set())
        self.assertEqual(adapter.status()[0]["state"], "pending")
        release.set()
        adapter.flush(timeout=5)

        self.assertEqual(revisions, [1, 2, 2])
        self.assertEqual(client.created, 1)
        self.assertEqual(client.experiment.steps, ["Fit complete"])
        self.assertTrue(
            all(i != threading.get_ident() for i in client.experiment.thread_ids)
        )
        self.assertEqual(list(adapter.spool_directory.iterdir()), [])

    def test_reopened_analysis_uploads_figure_and_code_to_same_experiment(self):
        client = OfflineClient()
        adapter = self.adapter(client)
        manager = self.manager()
        adapter.attach(manager.hooks)
        manager.acquisition_cell("calibration", cell="acquisition code")
        manager.save_acquisition(x=[1, 2, 3])
        filepath = str(manager.current_filepath)
        manager.analysis_cell(filepath=filepath, cell="none")
        self.assertIsNone(manager.current_acquisition)
        manager.save_fig(
            fig=FileFigure(), name="reopened.png", cell="reopened analysis code"
        )
        adapter.flush(timeout=5)

        self.assertEqual(client.created, 1)
        self.assertEqual(len(adapter.status()), 2)
        files = client.experiment.files
        self.assertTrue(any(name.endswith("_FIG_reopened.png") for name in files))
        self.assertTrue(any(name.endswith("_CELL.py") for name in files))
        h5_name = next(name for name in files if name.endswith(".h5"))
        with h5py.File(io.BytesIO(files[h5_name]), "r") as saved:
            code = saved["analysis_cells"]["reopened.png"][()]
            self.assertEqual(code.decode(), "reopened analysis code")

    def test_public_bridge_attaches_directly_to_manager(self):
        client = OfflineClient()
        adapter = self.adapter(client)
        manager = self.manager()
        adapter.attach(manager)
        manager.acquisition_cell("Sinusoid", cell="none")
        manager.save_acquisition(t=[0, 0.25, 0.5], y=[0, 1, 0])
        manager.analysis_cell(cell="none")
        manager.save_fig(fig=FileFigure(), name="sinusoid.png", cell="plot code")
        result = adapter.flush(timeout=5)
        self.assertEqual(result["title"], "offline example")
        self.assertTrue(
            any(name.endswith("_FIG_sinusoid.png") for name in client.experiment.files)
        )

    def test_existing_experiment_id_is_used_without_creating_another(self):
        client = OfflineClient()
        adapter = self.adapter(client, experiment_id=1)
        manager = self.manager()
        adapter.attach(manager.hooks)
        manager.acquisition_cell("calibration", cell="none")
        manager.save_acquisition(x=[1, 2, 3])
        result = adapter.flush(timeout=5)
        self.assertEqual(result["id"], 1)
        self.assertEqual(client.created, 0)

    def test_repeated_close_after_timeout_waits_for_the_existing_worker(self):
        client = OfflineClient()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocked_upload(filename):
            if not release.wait(timeout=5):
                raise TimeoutError("Test worker was not released")

        client.experiment.upload_file = blocked_upload
        adapter = self.adapter(client)
        manager = self.manager()
        adapter.attach(manager.hooks)
        manager.acquisition_cell("calibration", cell="none")
        manager.save_acquisition(x=[1, 2, 3])
        with self.assertRaises(TimeoutError):
            adapter.close(timeout=0.01)
        with self.assertRaises(TimeoutError):
            adapter.close(timeout=0.01)
        release.set()
        adapter.close(timeout=3)

    def test_upload_failure_retains_snapshot_and_blocks_dependent_note(self):
        client = OfflineClient()
        client.experiment.upload_file = Mock(
            side_effect=ConnectionError("server unavailable")
        )
        adapter = self.adapter(client)
        manager = self.manager()
        adapter.attach(manager.hooks)
        manager.acquisition_cell("calibration", cell="none")
        manager.save_acquisition(x=[1, 2, 3])
        note = adapter.add_step("Must not appear")
        with self.assertRaises(ConnectionError):
            note.result(timeout=3)
        with self.assertRaisesRegex(RuntimeError, "failed"):
            adapter.flush(timeout=3)
        self.assertEqual(client.experiment.steps, [])
        self.assertEqual(adapter.status()[0]["state"], "failed")
        self.assertTrue(list(adapter.spool_directory.rglob("*.h5")))
        self.assertTrue(Path(str(manager.current_filepath) + ".h5").is_file())

    def test_capture_does_not_include_neighboring_acquisition_files(self):
        client = OfflineClient()
        adapter = self.adapter(client)
        data = self.root / "data"
        data.mkdir()
        for name in ("run.h5", "run_CELL.py", "run__1.h5", "run__1_CELL.py"):
            (data / name).write_bytes(b"data")
        adapter.enqueue(SimpleNamespace(filepath=str(data / "run")))
        adapter.flush(timeout=5)
        self.assertEqual(set(client.experiment.files), {"run.h5", "run_CELL.py"})


class RequestTimeoutTests(unittest.TestCase):
    def test_generated_none_timeout_is_replaced_but_explicit_timeout_is_kept(self):
        original = Mock()
        client = SimpleNamespace(api_client=SimpleNamespace(call_api=original))
        _set_request_timeouts(client, (5, 30))
        client.api_client.call_api("/experiments", "GET", _request_timeout=None)
        original.assert_called_with("/experiments", "GET", _request_timeout=(5, 30))
        client.api_client.call_api("/experiments", "GET", _request_timeout=(2, 10))
        original.assert_called_with("/experiments", "GET", _request_timeout=(2, 10))


if __name__ == "__main__":
    unittest.main()
