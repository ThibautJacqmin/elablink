"""Exercise the example entry points without credentials or server access."""

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from elabmate import ElabClient, ElabExperiment, ElabResource
from tests.fakes import OfflineClient


ROOT = Path(__file__).resolve().parents[1]


def load_example(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "examples" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ElabftwExampleTests(unittest.TestCase):
    def test_import_does_not_create_a_client(self):
        with patch("elabmate.ElabClient") as client:
            load_example("elabftw_only")
        client.assert_not_called()

    def test_creates_and_links_entities_with_explicit_resource_category(self):
        example = load_example("elabftw_only")
        client = Mock(spec=ElabClient)
        experiment = Mock(spec=ElabExperiment)
        experiment.ID = 11
        sample = Mock(spec=ElabResource)
        sample.ID = 21
        client.create_experiment.return_value = experiment
        client.create_resource.return_value = sample
        with patch.object(example, "ElabClient", return_value=client) as constructor:
            with contextlib.redirect_stdout(io.StringIO()):
                result = example.main(
                    [
                        "--config",
                        "not-read.conf",
                        "--title",
                        "Test",
                        "--resource-category",
                        "7",
                    ]
                )

        constructor.assert_called_once_with("not-read.conf")
        client.create_experiment.assert_called_once_with("Test")
        client.create_resource.assert_called_once_with("Sample for Test", category=7)
        self.assertEqual(result, (experiment, sample))
        self.assertIn("Characterisation", experiment.main_text)
        experiment.set_extra_fields.assert_called_once_with(
            {"Temperature (K)": 4.2, "Operator": "Thibaut"}
        )
        experiment.add_step.assert_called_once_with("Cool the sample")
        experiment.add_comment.assert_called_once_with("The signal is stable.")
        experiment.add_tag.assert_called_once_with("calibration")
        sample.set_extra_fields.assert_called_once_with({"Serial number": "SAMPLE-001"})
        sample.add_tag.assert_called_once_with("sample")
        experiment.link_resource.assert_called_once_with(21)
        sample.link_experiment.assert_called_once_with(11)

    def test_missing_category_never_creates_a_client(self):
        example = load_example("elabftw_only")
        with patch.object(example, "ElabClient") as client:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    example.main([])
        client.assert_not_called()


try:
    import h5py
    import matplotlib

    matplotlib.use("Agg")
    import numpy as np
    from labmate.acquisition import LifecycleHooks
    from elabmate import AcquisitionAnalysisManager

    HAS_ACQUISITION = hasattr(LifecycleHooks, "add_analysis_figure_saved")
except ImportError:
    HAS_ACQUISITION = False


@unittest.skipUnless(
    HAS_ACQUISITION, "Requires plotting dependencies and the Labmate hook"
)
class AcquisitionScriptTests(unittest.TestCase):
    def test_import_does_not_create_a_manager(self):
        with patch("elabmate.AcquisitionAnalysisManager") as manager:
            load_example("acquisition_script")
        manager.assert_not_called()

    def run_script(self, directory, client):
        example = load_example("acquisition_script")
        managers = []

        def manager(*args, **kwargs):
            instance = AcquisitionAnalysisManager(
                *args, client_factory=lambda: client, **kwargs
            )
            managers.append(instance)
            return instance

        with patch.object(example, "AcquisitionAnalysisManager", side_effect=manager):
            with contextlib.redirect_stdout(io.StringIO()):
                result = example.main(
                    ["--config", "not-read.conf", "--data", str(directory)]
                )
        return example, managers[0], result

    def test_sinusoid_source_and_real_png_are_uploaded_and_worker_is_drained(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = OfflineClient()
            example, manager, result = self.run_script(Path(temporary) / "data", client)
            self.assertEqual(client.created, 1)
            self.assertEqual(result["id"], 1)
            self.assertEqual(result["title"], "Sinusoid")
            self.assertEqual(
                client.experiment.steps, ["Sinusoid acquired and plotted."]
            )
            self.assertTrue(
                all(job["state"] == "done" for job in manager.upload_status())
            )
            files = client.experiment.files
            h5 = next(
                content for name, content in files.items() if name.endswith(".h5")
            )
            png = next(
                content for name, content in files.items() if name.endswith(".png")
            )
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            source = Path(example.__file__).read_text(encoding="utf-8")
            with h5py.File(io.BytesIO(h5), "r") as saved:
                t, y = saved["t"][()], saved["y"][()]
                self.assertEqual(len(t), 500)
                np.testing.assert_allclose(y, np.sin(2 * np.pi * 5 * t))
                self.assertEqual(saved["acquisition_cell"]["1"][()].decode(), source)
                self.assertEqual(
                    saved["analysis_cells"]["sinusoid.png"][()].decode(), source
                )
            self.assertEqual(
                list((Path(temporary) / "data" / ".upload_queue").iterdir()), []
            )
            with self.assertRaisesRegex(RuntimeError, "closed"):
                manager.experiment.add_step("Too late")

    def test_upload_failure_surfaces_and_preserves_local_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = OfflineClient()
            client.experiment.upload_file = Mock(side_effect=ConnectionError("Offline"))
            data = Path(temporary) / "data"
            with self.assertLogs("elabmate.async_bridge", level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    self.run_script(data, client)
            self.assertTrue(list((data / "Sinusoid").glob("*.h5")))
            self.assertTrue(list((data / ".upload_queue").rglob("*.h5")))


if __name__ == "__main__":
    unittest.main()
