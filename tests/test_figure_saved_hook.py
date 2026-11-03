"""Offline regressions for Labmate's AnalysisData-based figure_saved hook."""

from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from tests.test_async_bridge import (
    BridgeTestCase,
    FileFigure,
    HAS_LABMATE_HOOK,
    OfflineClient,
)


@unittest.skipUnless(HAS_LABMATE_HOOK, "Requires Labmate with figure_saved")
class FigureSavedHookTests(BridgeTestCase):
    def manager_with_analysis(self, reopened=False):
        from labmate.acquisition_notebook import AcquisitionAnalysisManager

        manager = AcquisitionAnalysisManager(
            str(self.root / "data"), shell=None, use_magic=False, save_files=True
        )
        manager.acquisition_cell("calibration", cell="acquisition code")
        manager.save_acquisition(x=[1, 2, 3])
        if reopened:
            manager.analysis_cell(filepath=str(manager.current_filepath), cell="none")
            self.assertIsNone(manager.current_acquisition)
        else:
            manager.analysis_cell(cell="none")
        return manager

    def assert_figure_checkpoint(self, reopened):
        import h5py

        from labmate.acquisition import AnalysisData

        manager = self.manager_with_analysis(reopened)
        client = OfflineClient()
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        caller = threading.get_ident()
        upload_threads = []
        original_upload = client.experiment.upload_file

        def blocked_upload(filename):
            upload_threads.append(threading.get_ident())
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("Test did not release the upload worker")
            original_upload(filename)

        client.experiment.upload_file = blocked_upload
        seen = []

        def after_persist(analysis, **kwargs):
            self.assertIs(analysis, manager.data)
            self.assertIsInstance(analysis, AnalysisData)
            prefix = Path(str(analysis.filepath))
            self.assertTrue(list(prefix.parent.glob(prefix.name + "*FIG_fit.png")))
            with h5py.File(str(prefix) + ".h5", "r") as saved:
                code = saved["analysis_cells"]["fit.png"][()].decode()
            self.assertEqual(code, "analysis code")
            seen.append(analysis)

        manager.hooks.add_figure_saved(after_persist)
        adapter = self.adapter(client)
        adapter.attach(manager)
        manager.save_fig(fig=FileFigure(), name="fit.png", cell="analysis code")

        self.assertEqual(seen, [manager.data])
        self.assertTrue(started.wait(timeout=2))
        self.assertFalse(release.is_set())
        self.assertEqual(len(adapter.status()), 1)
        self.assertEqual(adapter.status()[0]["state"], "pending")
        release.set()
        adapter.flush(timeout=3)
        self.assertTrue(upload_threads)
        self.assertTrue(all(thread != caller for thread in upload_threads))
        self.assertEqual(len(adapter.status()), 1)
        self.assertTrue(
            any(name.endswith("_FIG_fit.png") for name in client.experiment.files)
        )

    def test_current_analysis_dispatches_one_persisted_nonblocking_checkpoint(self):
        self.assert_figure_checkpoint(reopened=False)

    def test_reopened_analysis_dispatches_one_persisted_nonblocking_checkpoint(self):
        self.assert_figure_checkpoint(reopened=True)

    def test_save_fig_only_does_not_dispatch_a_checkpoint(self):
        manager = self.manager_with_analysis()
        client = OfflineClient()
        adapter = self.adapter(client)
        adapter.attach(manager)
        seen = []
        manager.hooks.add_figure_saved(lambda analysis, **kwargs: seen.append(analysis))
        manager.save_fig_only(fig=FileFigure(), name="only.png")
        prefix = Path(str(manager.data.filepath))
        self.assertTrue(list(prefix.parent.glob(prefix.name + "*FIG_only.png")))
        self.assertEqual(seen, [])
        self.assertEqual(adapter.status(), [])
        self.assertIsNone(adapter.latest_snapshot)
        self.assertEqual(client.created, 0)

    def test_failed_analysis_cell_save_does_not_enqueue_partial_checkpoint(self):
        manager = self.manager_with_analysis()
        client = OfflineClient()
        adapter = self.adapter(client)
        adapter.attach(manager)
        with patch.object(
            manager, "save_analysis_cell", side_effect=RuntimeError("Local save failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "Local save failed"):
                manager.save_fig(fig=FileFigure(), name="partial.png", cell="analysis code")
        self.assertEqual(adapter.status(), [])
        self.assertEqual(client.created, 0)


if __name__ == "__main__":
    unittest.main()

