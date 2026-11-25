"""A browser H5 working copy keeps its original experiment during later analysis."""

from contextlib import suppress
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from elablink._attachment_store import AttachmentStore
from tests.test_acquisition_analysis_manager import HAS_LABMATE_HOOK, FileFigure, MultiExperimentClient
from tests.test_attachment_store import Response


@unittest.skipUnless(HAS_LABMATE_HOOK, "Requires current Labmate")
class BrowserAnalysisSyncTests(unittest.TestCase):
    def test_browser_copy_reanalysis_updates_original_experiment_and_checks_server(self):
        import h5py
        from elablink import AcquisitionAnalysisManager

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            client = MultiExperimentClient()
            client.api_client = SimpleNamespace(call_api=Mock())
            managers = []

            def manager(data_folder):
                result = AcquisitionAnalysisManager(
                    str(root / data_folder), shell=None, use_magic=False,
                    save_files=True, client_factory=lambda: client,
                )
                managers.append(result)
                return result

            try:
                acquisition = manager("original-acquisition")
                acquisition.acquisition_cell("Laser calibration", cell="acquire signal")
                acquisition.save_acquisition(x=[0, 1, 2], y=[0, 1, 0])
                original_id = acquisition.flush(timeout=5)["id"]
                acquisition.close(timeout=5)
                remote = client.experiments["Laser calibration"]
                filename = next(name for name in remote.files if name.endswith(".h5"))
                contents = remote.files[filename]
                row = {"id": 52, "real_name": filename, "filesize": len(contents),
                       "hash": hashlib.sha256(contents).hexdigest(), "hash_algorithm": "sha256"}
                remote.title = "Renamed calibration record"
                client.experiments[remote.title] = client.experiments.pop("Laser calibration")
                remote.refresh = lambda: None
                remote.list_files = lambda: [dict(row)]
                client.uploads = SimpleNamespace(
                    read_upload=lambda *args, **kwargs: Response(remote.files[filename]),
                )
                store = AttachmentStore(
                    None, "experiments", original_id, root / "browser-cache",
                    lambda: client, folder=root / "unrelated-working-folder",
                )
                working = store.download([row])[0]
                self.assertTrue(working.is_relative_to(root / "unrelated-working-folder"))
                self.assertEqual(working.read_bytes(), contents)
                client.create_titles.clear()
                client.load_requests.clear()

                analysis = manager("different-analysis-session")
                analysis.analysis_cell(filepath=working, cell="none")
                analysis.data["fit_frequency"] = 3.25
                analysis.data.save()
                analysis.save_fig(fig=FileFigure(), name="browser-fit.png", cell="fit signal")
                result = analysis.flush(timeout=5)
                self.assertEqual(result["id"], original_id)
                self.assertEqual(client.create_titles, [])
                self.assertTrue(all(identifier == original_id for identifier, _ in client.load_requests))
                self.assertTrue(any(name.endswith("_FIG_browser-fit.png") for name in remote.files))
                with h5py.File(io.BytesIO(remote.files[filename]), "r") as saved:
                    self.assertAlmostEqual(float(saved["fit_frequency"][()]), 3.25)
                self.assertFalse(any("elablink.json" in name for name in remote.files))
                analysis.close(timeout=5)

                before = dict(remote.files)
                client.configuration.host = "https://another.invalid/api/v2"
                wrong_server = manager("different-server-session")
                with self.assertRaisesRegex((ValueError, RuntimeError), "different.*server"):
                    wrong_server.analysis_cell(filepath=working, cell="none")
                    wrong_server.save_fig(fig=FileFigure(), name="wrong-server.png", cell="fit signal")
                    wrong_server.flush(timeout=5)
                self.assertEqual(remote.files, before)
                self.assertEqual(client.create_titles, [])
            finally:
                for instance in managers:
                    with suppress(RuntimeError, TimeoutError):
                        instance.close(timeout=5)


if __name__ == "__main__":
    unittest.main()
