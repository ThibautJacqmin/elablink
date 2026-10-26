"""Offline checks of notebook-safe, experiment-bound background edits."""

import copy
import tempfile
import threading
import unittest
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from elablink import AsyncBridge, AsyncExperiment
from tests.fakes import OfflineExperiment
from tests.test_acquisition_analysis_manager import (
    HAS_LABMATE_HOOK,
    FileFigure,
    MultiExperimentClient,
)


class EditableExperiment(OfflineExperiment):
    def __init__(self):
        super().__init__()
        self.comments = []
        self.tags = []
        self.extra_fields = {}
        self.operations = []
        self._main_text = ""

    def _record(self, kind, value):
        self.thread_ids.append(threading.get_ident())
        self.operations.append((kind, copy.deepcopy(value)))

    def add_step(self, text):
        self._record("step", text)
        self.steps.append(text)

    def add_comment(self, text):
        self._record("comment", text)
        self.comments.append(text)

    def add_tag(self, tag):
        self._record("tag", tag)
        self.tags.append(tag)

    @property
    def main_text(self):
        return self._main_text

    @main_text.setter
    def main_text(self, value):
        self._record("body", value)
        self._main_text = value

    @property
    def body(self):
        return self.main_text

    @body.setter
    def body(self, value):
        self.main_text = value

    def set_extra_fields(self, fields, *, merge=True):
        self._record("extra fields", (fields, merge))
        if not merge:
            self.extra_fields = {}
        self.extra_fields.update(copy.deepcopy(fields))


class EditableClient(MultiExperimentClient):
    def create_experiment(self, *, title):
        experiment = super().create_experiment(title=title)
        editable = EditableExperiment()
        editable.ID = experiment.ID
        self.experiments[title] = editable
        return editable


class ExperimentTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.resources = []
        self.releases = []
        self.addCleanup(self.close_resources)

    def close_resources(self):
        for release in self.releases:
            release.set()
        for resource in self.resources:
            with suppress(RuntimeError, TimeoutError):
                resource.close(timeout=5)

    def adapter(self, client):
        bridge = AsyncBridge(
            spool_directory=self.root / "spool",
            client_factory=lambda: client,
        )
        self.resources.append(bridge)
        return bridge

    def data(self, title="A", name="run"):
        filepath = self.root / title / (name + ".h5")
        filepath.parent.mkdir(exist_ok=True)
        filepath.write_bytes(b"saved data")
        return SimpleNamespace(filepath=filepath, experiment_name=title)


class AsyncExperimentTests(ExperimentTestCase):
    def test_no_handle_is_available_before_a_saved_checkpoint(self):
        bridge = self.adapter(EditableClient())
        with self.assertRaisesRegex(RuntimeError, "[Ss]ave"):
            _ = bridge.experiment

    def test_edits_queue_during_creation_and_copy_mutable_fields(self):
        client = EditableClient()
        started = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        create = client.create_experiment

        def blocked_create(*, title):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test worker was not released")
            return create(title=title)

        client.create_experiment = blocked_create
        bridge = self.adapter(client)
        bridge.enqueue(self.data())
        self.assertTrue(started.wait(timeout=3))
        experiment = bridge.experiment
        self.assertIsInstance(experiment, AsyncExperiment)
        fields = {"temperature": {"value": 4, "unit": "K"}}
        futures = [
            experiment.add_step("Cool sample"),
            experiment.add_comment("Stable"),
            experiment.add_tag("calibration"),
            experiment.set_extra_fields(fields, merge=False),
        ]
        experiment.main_text = "First description"
        experiment.body = "Final description"
        fields["temperature"]["value"] = 300
        self.assertTrue(all(isinstance(future, Future) for future in futures))
        self.assertTrue(all(not future.done() for future in futures))
        self.assertFalse(release.is_set())
        release.set()
        result = bridge.flush(timeout=5)

        remote = client.experiments["A"]
        self.assertEqual(result["title"], "A")
        self.assertEqual(remote.steps, ["Cool sample"])
        self.assertEqual(remote.comments, ["Stable"])
        self.assertEqual(remote.tags, ["calibration"])
        self.assertEqual(remote.extra_fields["temperature"]["value"], 4)
        self.assertEqual(remote.main_text, "Final description")
        self.assertEqual(
            [kind for kind, value in remote.operations],
            ["step", "comment", "tag", "extra fields", "body", "body"],
        )
        self.assertTrue(all(t != threading.get_ident() for t in remote.thread_ids))
        self.assertTrue(all(job["state"] == "done" for job in bridge.status()))

    def test_saved_handle_stays_bound_after_another_experiment_is_submitted(self):
        client = EditableClient()
        bridge = self.adapter(client)
        bridge.enqueue(self.data("A"))
        first = bridge.experiment
        bridge.enqueue(self.data("B"))
        second = bridge.experiment
        first.add_step("Only A")
        second.add_comment("Only B")
        bridge.add_step("Legacy latest-experiment helper")
        bridge.flush(timeout=5)
        self.assertEqual(client.experiments["A"].steps, ["Only A"])
        self.assertEqual(client.experiments["A"].comments, [])
        self.assertEqual(client.experiments["B"].comments, ["Only B"])
        self.assertEqual(
            client.experiments["B"].steps, ["Legacy latest-experiment helper"]
        )

    def test_handle_keeps_the_checkpoint_prerequisite_captured_at_access(self):
        client = EditableClient()
        bridge = self.adapter(client)
        data = self.data()
        bridge.enqueue(data)
        bridge.flush(timeout=5)
        earlier = bridge.experiment
        remote = client.experiments["A"]
        remote.upload_file = Mock(side_effect=ConnectionError("upload failed"))
        bridge.enqueue(data)
        later = bridge.experiment
        blocked = later.add_step("Needs failed checkpoint")
        allowed = earlier.add_step("Uses earlier successful checkpoint")
        with self.assertRaises(ConnectionError):
            blocked.result(timeout=5)
        allowed.result(timeout=5)
        with self.assertRaisesRegex(RuntimeError, "failed"):
            bridge.flush(timeout=5)
        self.assertEqual(remote.steps, ["Uses earlier successful checkpoint"])

    def test_failed_upload_blocks_dependent_edits_and_close_reports_failures(self):
        client = EditableClient()
        remote = client.create_experiment(title="A")
        remote.upload_file = Mock(side_effect=ConnectionError("upload failed"))
        bridge = self.adapter(client)
        bridge.enqueue(self.data())
        future = bridge.experiment.add_comment("Must not appear")
        with self.assertRaises(ConnectionError):
            future.result(timeout=5)
        with self.assertRaisesRegex(RuntimeError, "failed"):
            bridge.close(timeout=5)
        self.assertEqual(remote.comments, [])
        self.assertTrue(list(bridge.spool_directory.rglob("*.h5")))

    def test_edit_errors_are_reported_by_flush_and_close(self):
        client = EditableClient()
        bridge = self.adapter(client)
        bridge.enqueue(self.data())
        bridge.flush(timeout=5)
        client.experiments["A"].add_tag = Mock(
            side_effect=ConnectionError("edit failed")
        )
        future = bridge.experiment.add_tag("calibration")
        with self.assertRaises(ConnectionError):
            future.result(timeout=5)
        for wait in (bridge.flush, bridge.close):
            with self.assertRaisesRegex(RuntimeError, "failed") as raised:
                wait(timeout=5)
            self.assertIsInstance(raised.exception.__cause__, ConnectionError)
        self.assertEqual(bridge.status()[-1]["state"], "failed")

    def test_cancelled_checkpoint_blocks_its_dependent_edit(self):
        client = EditableClient()
        started = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        create = client.create_experiment

        def blocked_create(*, title):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test worker was not released")
            return create(title=title)

        client.create_experiment = blocked_create
        bridge = self.adapter(client)
        bridge.enqueue(self.data("A"))
        self.assertTrue(started.wait(timeout=3))
        checkpoint = bridge.enqueue(self.data("B"))
        self.assertTrue(checkpoint.cancel())
        edit = bridge.experiment.add_step("Must not appear")
        release.set()
        with self.assertRaises(CancelledError):
            edit.result(timeout=5)
        with self.assertRaisesRegex(RuntimeError, "failed"):
            bridge.flush(timeout=5)
        self.assertNotIn("B", client.experiments)

    def test_reads_and_unsupported_operations_never_expose_the_remote_object(self):
        bridge = self.adapter(EditableClient())
        bridge.enqueue(self.data())
        experiment = bridge.experiment
        for attribute in ("main_text", "body"):
            with self.assertRaisesRegex(
                AttributeError, "[Rr]ead|[Ww]rite|[Ss]ynchronous"
            ):
                getattr(experiment, attribute)
        for attribute in ("api", "upload_file", "delete", "unknown_method"):
            with self.assertRaises(AttributeError):
                getattr(experiment, attribute)

    def test_handle_edits_obey_owner_thread_and_closed_bridge_checks(self):
        bridge = self.adapter(EditableClient())
        bridge.enqueue(self.data())
        experiment = bridge.experiment
        with ThreadPoolExecutor(max_workers=1) as worker:
            for operation in (
                lambda: experiment.add_step("wrong thread"),
                lambda: setattr(experiment, "main_text", "wrong thread"),
            ):
                with self.assertRaisesRegex(RuntimeError, "creating notebook thread"):
                    worker.submit(operation).result(timeout=3)
        bridge.close(timeout=5)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            experiment.add_step("Too late")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            experiment.body = "Too late"


@unittest.skipUnless(HAS_LABMATE_HOOK, "Requires Labmate with analysis_figure_saved")
class ManagerExperimentTests(ExperimentTestCase):
    def manager(self, client, **kwargs):
        from elablink import AcquisitionAnalysisManager

        manager = AcquisitionAnalysisManager(
            str(self.root / "data"),
            shell=None,
            use_magic=False,
            save_files=True,
            client_factory=lambda: client,
            **kwargs,
        )
        self.resources.append(manager)
        return manager

    def acquire(self, manager, name):
        manager.acquisition_cell(name, cell="acquisition code")
        manager.save_acquisition(x=[0, 1], y=[0, 1])

    def test_manager_requires_a_saved_checkpoint_not_just_an_acquisition_cell(self):
        manager = self.manager(EditableClient())
        with self.assertRaisesRegex(RuntimeError, "[Ss]ave"):
            _ = manager.experiment
        manager.acquisition_cell("A", cell="acquisition code")
        with self.assertRaisesRegex(RuntimeError, "[Ss]ave"):
            _ = manager.experiment

    def test_reopening_old_analysis_selects_its_handle_without_resaving_a_figure(self):
        client = EditableClient()
        manager = self.manager(client)
        self.acquire(manager, "A")
        first_path = str(manager.current_filepath)
        self.acquire(manager, "B")
        manager.analysis_cell(filepath=first_path, cell="old analysis")
        manager.experiment.add_comment("Analysis of A")
        manager.flush(timeout=5)
        self.assertEqual(client.experiments["A"].comments, ["Analysis of A"])
        self.assertEqual(client.experiments["B"].comments, [])

    def test_unsaved_new_acquisition_does_not_fall_back_to_the_previous_experiment(
        self,
    ):
        manager = self.manager(EditableClient())
        self.acquire(manager, "A")
        manager.acquisition_cell("B", cell="new acquisition")
        with self.assertRaisesRegex(RuntimeError, "[Ss]ave"):
            _ = manager.experiment

    def test_indexed_old_analysis_keeps_its_successful_upload_association(self):
        client = EditableClient()
        original = self.manager(client)
        self.acquire(original, "A")
        filepath = str(original.current_filepath)
        original.close(timeout=5)
        reopened = self.manager(client)
        self.acquire(reopened, "B")
        reopened.analysis_cell(filepath=filepath, cell="old analysis")
        reopened.experiment.add_comment("Known original upload")
        reopened.save_fig(fig=FileFigure(), name="fit.png", cell="fit code")
        reopened.experiment.add_step("A figure was saved")
        reopened.flush(timeout=5)
        self.assertEqual(client.experiments["A"].steps, ["A figure was saved"])
        self.assertEqual(client.experiments["A"].comments, ["Known original upload"])
        self.assertEqual(client.experiments["B"].steps, [])

    def test_unindexed_local_analysis_still_requires_a_saved_checkpoint(self):
        client = EditableClient()
        manager = self.manager(client)
        self.acquire(manager, "A")
        original = Path(str(manager.current_filepath) + ".h5")
        copied = self.root / "Unassociated" / "run.h5"
        copied.parent.mkdir()
        copied.write_bytes(original.read_bytes())
        manager.close(timeout=5)
        reopened = self.manager(client)
        self.acquire(reopened, "B")
        reopened.analysis_cell(filepath=copied, cell="local analysis")
        with self.assertRaisesRegex(RuntimeError, "[Ss]ave"):
            _ = reopened.experiment

    def test_fixed_title_keeps_both_acquisition_handles_on_the_selected_experiment(
        self,
    ):
        client = EditableClient()
        manager = self.manager(client, experiment_title="Shared")
        self.acquire(manager, "A")
        first = manager.experiment
        self.acquire(manager, "B")
        first.add_step("From A")
        manager.experiment.add_comment("From B")
        manager.close(timeout=5)
        self.assertEqual(client.create_titles, ["Shared"])
        self.assertEqual(client.experiments["Shared"].steps, ["From A"])
        self.assertEqual(client.experiments["Shared"].comments, ["From B"])

    def test_new_unsaved_run_of_the_same_name_can_edit_its_existing_experiment(self):
        client = EditableClient()
        manager = self.manager(client)
        self.acquire(manager, "A")
        earlier = manager.experiment
        manager.acquisition_cell("A", cell="next acquisition")
        self.assertIs(manager.experiment, earlier)
        manager.experiment.add_comment("Same experiment")
        manager.close(timeout=5)
        self.assertEqual(client.create_titles, ["A"])
        self.assertEqual(client.experiments["A"].comments, ["Same experiment"])

    def test_explicit_experiment_id_keeps_handles_bound_to_the_existing_record(self):
        client = EditableClient()
        remote = client.create_experiment(title="Existing")
        client.create_titles.clear()
        manager = self.manager(client, experiment_id=remote.ID)
        self.acquire(manager, "A")
        first = manager.experiment
        self.acquire(manager, "B")
        first.add_step("From A")
        manager.experiment.add_comment("From B")
        result = manager.flush(timeout=5)
        self.assertEqual(result["id"], remote.ID)
        self.assertEqual(client.create_titles, [])
        self.assertEqual(client.load_requests, [(remote.ID, None)])
        self.assertEqual(remote.steps, ["From A"])
        self.assertEqual(remote.comments, ["From B"])


if __name__ == "__main__":
    unittest.main()
