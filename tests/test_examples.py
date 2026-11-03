"""Run the three examples without credentials or server access."""

import contextlib
import ast
import io
import json
import os
import runpy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch
import warnings

from elablink import Experiment, Resource, Step
from tests.fakes import OfflineClient


ROOT = Path(__file__).resolve().parents[1]


def run_example(name):
    return runpy.run_path(str(ROOT / "examples" / f"{name}.py"))


class ExperimentResourceExampleTests(unittest.TestCase):
    def test_examples_are_linear_scripts_without_command_line_setup(self):
        for name in ("experiments_and_resources", "labmate_acquisition"):
            source = (ROOT / "examples" / f"{name}.py").read_text(encoding="utf-8")
            tree = ast.parse(source)
            self.assertFalse(
                any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for node in ast.walk(tree)
                )
            )
            self.assertNotIn("argparse", source)
            self.assertNotIn('if __name__', source)
            self.assertNotIn("config_path", source)

    def test_notebook_code_stays_simple(self):
        notebook = json.loads(
            (ROOT / "examples" / "labmate_notebook.ipynb").read_text(encoding="utf-8")
        )
        for cell in notebook["cells"]:
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            tree = ast.parse(source)
            self.assertFalse(
                any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for node in ast.walk(tree)
                )
            )
            self.assertNotIn("argparse", source)
            self.assertNotIn('if __name__', source)
            self.assertNotIn("config_path", source)

    def test_creates_and_links_entities_with_visible_resource_category(self):
        experiment = Mock(spec=Experiment)
        experiment.ID = 11
        experiment.categories = {"Calibration": 2}
        experiment.statuses = {"Completed": 4}
        experiment.available_tags = {"calibration": 5, "sample": 6}
        update_step = Mock()
        step = Step(1, "Cool the sample", False, update_step)
        experiment.steps = {step.text: step}
        sample = Mock(spec=Resource)
        sample.ID = 21
        sample.categories = {"Samples": 3}
        sample.statuses = {"Available": 7}
        with (
            patch("elablink.Experiment", return_value=experiment) as create_experiment,
            patch("elablink.Resource", return_value=sample) as create_resource,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = run_example("experiments_and_resources")

        create_experiment.assert_called_once_with(title="First measurement")
        create_resource.assert_called_once_with(title="Sample A", category=3)
        self.assertIs(result["experiment"], experiment)
        self.assertIs(result["sample"], sample)
        self.assertIn("Characterisation", experiment.main_text)
        experiment.set_extra_fields.assert_called_once_with({"Temperature (K)": 4.2})
        self.assertEqual(experiment.category, "Calibration")
        self.assertEqual(experiment.status, "Completed")
        self.assertEqual(
            experiment.add_step.call_args_list,
            [call("Cool the sample"), call("Record the signal", checked=True)],
        )
        self.assertTrue(step.status)
        update_step.assert_called_once_with(1, True)
        experiment.add_comment.assert_called_once_with("The signal is stable.")
        experiment.add_tag.assert_called_once_with("calibration")
        sample.set_extra_fields.assert_called_once_with({"Serial number": "SAMPLE-001"})
        self.assertEqual(sample.category, "Samples")
        self.assertEqual(sample.status, "Available")
        sample.add_tag.assert_called_once_with("sample")
        sample.add_comment.assert_called_once_with("Ready for use.")
        experiment.link_resource.assert_called_once_with(21)
        sample.link_experiment.assert_called_once_with(11)

    def test_duplicate_experiment_title_does_not_create_an_unused_resource(self):
        from elablink import DuplicateTitle

        with (
            patch("elablink.Experiment", side_effect=DuplicateTitle("First measurement")),
            patch("elablink.Resource") as create_resource,
        ):
            with self.assertRaises(DuplicateTitle):
                run_example("experiments_and_resources")
        create_resource.assert_not_called()


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
    from tests.test_async_experiment import EditableClient, EditableExperiment

    HAS_ACQUISITION = hasattr(LifecycleHooks, "add_figure_saved")
except ImportError:
    HAS_ACQUISITION = False


if HAS_ACQUISITION:
    class ExampleExperiment(EditableExperiment):
        """Supply the metadata and checkbox operations used by the examples."""

        def __init__(self):
            super().__init__()
            self.categories = {"Calibration": 2}
            self.statuses = {"Completed": 4}
            self.category = None
            self.status = None
            self.checked_steps = {}

        def refresh(self):
            self._record("refresh", None)

        def add_step(self, text, *, checked=False):
            super().add_step(text)
            self.checked_steps[len(self.steps)] = checked

        def _set_step_status(self, ID, checked):  # noqa: N803 - Step API
            self._record("step status", (ID, checked))
            self.checked_steps[ID] = checked

        @property
        def steps_by_id(self):
            return {
                ID: Step(ID, text, self.checked_steps[ID], self._set_step_status)
                for ID, text in enumerate(self.steps, start=1)
            }

    class ExampleClient(EditableClient):
        def create_experiment(self, *, title):
            previous = super().create_experiment(title=title)
            experiment = ExampleExperiment()
            experiment.ID = previous.ID
            self.experiments[title] = experiment
            return experiment


@unittest.skipUnless(
    HAS_ACQUISITION, "Requires plotting dependencies, IPython and the Labmate hook"
)
class AcquisitionExampleTests(unittest.TestCase):
    def run_script(self, directory, client):
        managers = []

        def manager(data_directory, **kwargs):
            self.assertEqual(data_directory, "data")
            self.assertEqual(kwargs, {"shell": None, "use_magic": False})
            instance = AcquisitionAnalysisManager(
                directory, client_factory=lambda: client, **kwargs
            )
            managers.append(instance)
            return instance

        with (
            patch("elablink.AcquisitionAnalysisManager", side_effect=manager),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            namespace = run_example("labmate_acquisition")
        return namespace, managers[0], namespace["info"]

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
        self.assertEqual(remote.checked_steps, {1: True})
        self.assertEqual(remote.comments, ["The signal is stable."])
        self.assertEqual(remote.extra_fields, {"Frequency (Hz)": 5})
        self.assertEqual(remote.category, "Calibration")
        self.assertEqual(remote.status, "Completed")
        return acquisition_source, analysis_source

    def test_script_uploads_signal_source_figure_and_notes_then_drains_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = ExampleClient()
            example, manager, result = self.run_script(Path(temporary) / "data", client)
            self.assertEqual(client.create_titles, ["Sinusoid"])
            self.assertEqual(result["id"], 1)
            self.assertEqual(result["title"], "Sinusoid")
            remote = client.experiments["Sinusoid"]
            acquisition_source, analysis_source = self.check_uploaded_signal(remote)
            source = Path(example["__file__"]).read_text(encoding="utf-8")
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
                with self.assertRaisesRegex(ConnectionError, "Offline"):
                    self.run_script(data, client)
            self.assertTrue(list((data / "Sinusoid").glob("*.h5")))
            self.assertTrue(list((data / ".upload_queue").rglob("*.h5")))

    def test_notebook_cells_run_with_real_ipython_history_without_server_access(self):
        notebook = json.loads(
            (ROOT / "examples" / "labmate_notebook.ipynb").read_text(encoding="utf-8")
        )
        cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
        client = ExampleClient()

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
