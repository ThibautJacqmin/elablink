"""Run the three examples without credentials or server access."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import warnings

from elablink import Client, Experiment, Resource
from tests.fakes import OfflineClient


ROOT = Path(__file__).resolve().parents[1]


def load_example(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "examples" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExperimentResourceExampleTests(unittest.TestCase):
    def test_import_does_not_create_a_client(self):
        with patch("elablink.Client") as client:
            load_example("experiments_and_resources")
        client.assert_not_called()

    def test_creates_and_links_entities_with_explicit_resource_category(self):
        example = load_example("experiments_and_resources")
        client = Mock(spec=Client)
        experiment = Mock(spec=Experiment)
        experiment.ID = 11
        sample = Mock(spec=Resource)
        sample.ID = 21
        client.create_experiment.return_value = experiment
        client.create_resource.return_value = sample
        with patch.object(example, "Client", return_value=client) as constructor:
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
        client.create_resource.assert_called_once_with("Sample A", category=7)
        self.assertEqual(result, (experiment, sample))
        self.assertIn("Characterisation", experiment.main_text)
        experiment.set_extra_fields.assert_called_once_with({"Temperature (K)": 4.2})
        experiment.add_step.assert_called_once_with("Cool the sample")
        experiment.add_comment.assert_called_once_with("The signal is stable.")
        experiment.add_tag.assert_called_once_with("calibration")
        sample.set_extra_fields.assert_called_once_with({"Serial number": "SAMPLE-001"})
        sample.add_tag.assert_called_once_with("sample")
        experiment.link_resource.assert_called_once_with(21)
        sample.link_experiment.assert_called_once_with(11)

    def test_missing_category_never_creates_a_client(self):
        example = load_example("experiments_and_resources")
        with patch.object(example, "Client") as client:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    example.main([])
        client.assert_not_called()

    def test_notebook_contains_no_saved_outputs(self):
        notebook = json.loads(
            (ROOT / "examples" / "labmate_notebook.ipynb").read_text(encoding="utf-8")
        )
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])


try:
    import h5py
    import matplotlib

    matplotlib.use("Agg")
    import numpy as np
    from IPython.core.interactiveshell import InteractiveShell
    from IPython.core.profiledir import ProfileDir
    from traitlets.config import Config
    from labmate.acquisition import LifecycleHooks
    import elablink
    from elablink import AcquisitionAnalysisManager
    from tests.test_async_experiment import EditableClient

    HAS_ACQUISITION = hasattr(LifecycleHooks, "add_analysis_figure_saved")
except ImportError:
    HAS_ACQUISITION = False


@unittest.skipUnless(
    HAS_ACQUISITION, "Requires plotting dependencies, IPython and the Labmate hook"
)
class AcquisitionExampleTests(unittest.TestCase):
    def test_import_does_not_create_a_manager(self):
        with patch("elablink.AcquisitionAnalysisManager") as manager:
            load_example("labmate_acquisition")
        manager.assert_not_called()

    def run_script(self, directory, client):
        example = load_example("labmate_acquisition")
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

    def check_uploaded_signal(self, remote):
        h5 = next(
            content for name, content in remote.files.items() if name.endswith(".h5")
        )
        png = next(
            content for name, content in remote.files.items() if name.endswith(".png")
        )
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        with h5py.File(io.BytesIO(h5), "r") as saved:
            t, y = saved["t"][()], saved["y"][()]
            self.assertEqual(len(t), 500)
            np.testing.assert_allclose(y, np.sin(2 * np.pi * 5 * t))
            acquisition_source = saved["acquisition_cell"]["1"][()].decode()
            analysis_source = saved["analysis_cells"]["sinusoid.png"][()].decode()
        self.assertEqual(remote.steps, ["Sinusoid acquired and plotted."])
        self.assertEqual(remote.comments, ["The signal is stable."])
        self.assertEqual(remote.extra_fields, {"Frequency (Hz)": 5})
        return acquisition_source, analysis_source

    def test_script_uploads_signal_source_figure_and_notes_then_drains_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = EditableClient()
            example, manager, result = self.run_script(Path(temporary) / "data", client)
            self.assertEqual(client.create_titles, ["Sinusoid"])
            self.assertEqual(result["id"], 1)
            self.assertEqual(result["title"], "Sinusoid")
            remote = client.experiments["Sinusoid"]
            acquisition_source, analysis_source = self.check_uploaded_signal(remote)
            source = Path(example.__file__).read_text(encoding="utf-8")
            self.assertEqual(acquisition_source, source)
            self.assertEqual(analysis_source, source)
            self.assertTrue(all(job["state"] == "done" for job in manager.upload_status()))
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
            with self.assertLogs("elablink.async_bridge", level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    self.run_script(data, client)
            self.assertTrue(list((data / "Sinusoid").glob("*.h5")))
            self.assertTrue(list((data / ".upload_queue").rglob("*.h5")))

    def test_notebook_cells_run_with_real_ipython_history_without_server_access(self):
        notebook = json.loads(
            (ROOT / "examples" / "labmate_notebook.ipynb").read_text(encoding="utf-8")
        )
        cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
        client = EditableClient()

        class NotebookShell(InteractiveShell):
            # Jupyter's parent message contains the current cell's code.
            def get_parent(self):
                return {"content": {"code": self.history_manager.input_hist_raw[-1]}}

        def manager(data_directory):
            return AcquisitionAnalysisManager(
                data_directory, client_factory=lambda: client
            )

        previous_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            os.chdir(temporary)
            shell = NotebookShell.instance(
                ipython_dir=str(Path(temporary) / "ipython"),
                profile_dir=ProfileDir.create_profile_dir(
                    str(Path(temporary) / "ipython-profile")
                ),
                config=Config({"HistoryManager": {"hist_file": ":memory:"}}),
            )
            try:
                with (
                    patch.object(
                        elablink, "AcquisitionAnalysisManager", side_effect=manager
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                    warnings.catch_warnings(),
                ):
                    warnings.filterwarnings(
                        "ignore", message="FigureCanvasAgg is non-interactive"
                    )
                    for cell in cells:
                        result = shell.run_cell("".join(cell["source"]), store_history=True)
                        if result.error_before_exec:
                            raise result.error_before_exec
                        if result.error_in_exec:
                            raise result.error_in_exec
                aqm = shell.user_ns["aqm"]
                self.assertIs(aqm.shell, shell)
                acquisition_source, analysis_source = self.check_uploaded_signal(
                    client.experiments["Sinusoid"]
                )
                self.assertIn("np.sin", acquisition_source)
                self.assertIn("plt.plot", analysis_source)
                self.assertTrue(
                    all(job["state"] == "done" for job in aqm.upload_status())
                )
            finally:
                if "aqm" in shell.user_ns:
                    shell.user_ns["aqm"].close(timeout=5)
                shell.history_manager.end_session()
                NotebookShell.clear_instance()
                os.chdir(previous_directory)


if __name__ == "__main__":
    unittest.main()
