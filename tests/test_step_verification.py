"""Checkbox updates must confirm the server state, including concurrent edits."""

from concurrent.futures import Future
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from elablink import AsyncBridge, AsyncExperiment, Experiment, Resource


class StepVerificationTests(unittest.TestCase):
    def entity(self, *, resource=False, initial=False, before_toggle=None, embedded=True):
        rows = [{"id": 4, "body": "Align", "finished": int(initial)}]
        api = Mock()
        api.configuration.host = "https://offline.invalid/api/v2"
        model = SimpleNamespace(to_dict=lambda: {"steps": deepcopy(rows)} if embedded else {})
        api.experiments.get_experiment.return_value = model
        api.items.get_item.return_value = model
        api.steps.read_steps.side_effect = lambda *args: deepcopy(rows)

        def toggle(entity_type, entity_id, step_id, *, body):
            self.assertEqual(body, {"action": "finish"})
            if before_toggle is not None:
                before_toggle(rows)
            row = next(row for row in rows if row["id"] == step_id)
            row["finished"] = 0 if row["finished"] else 1

        api.steps.patch_step.side_effect = toggle
        entity = (Resource if resource else Experiment)(api, 17)
        return entity, api, rows

    def test_gui_edit_between_read_and_toggle_reports_conflict_without_retry(self):
        for resource in (False, True):
            for initial in (False, True):
                with self.subTest(resource=resource, initial=initial):
                    def gui_edit(rows):
                        rows[0]["finished"] = int(not initial)

                    entity, api, rows = self.entity(resource=resource, initial=initial, before_toggle=gui_edit)
                    step = entity.steps["Align"]
                    state = "checked" if initial else "unchecked"
                    with self.assertRaisesRegex(RuntimeError, f"step 4: it is currently {state}"):
                        step.status = not initial
                    self.assertEqual(step.status, initial)
                    self.assertEqual(entity.steps_by_id[4].status, initial)
                    self.assertEqual(rows[0]["finished"], int(initial))
                    api.steps.patch_step.assert_called_once()

    def test_direct_check_returns_confirmed_state_and_current_label(self):
        def rename(rows):
            rows[0]["body"] = "Renamed in GUI"
            rows.append({"id": 5, "body": "Align", "finished": 0})

        entity, api, rows = self.entity(before_toggle=rename)
        confirmed = entity.check_step("Align")
        self.assertEqual((confirmed.ID, confirmed.text, confirmed.status), (4, "Renamed in GUI", True))
        self.assertEqual(rows[1]["finished"], 0)
        api.steps.read_steps.assert_called_once()
        api.experiments.get_experiment.assert_called_once()

    def test_missing_embedded_steps_are_verified_through_steps_endpoint(self):
        entity, api, _ = self.entity(embedded=False)
        self.assertTrue(entity.check_step(4).status)
        self.assertEqual(api.steps.read_steps.call_count, 2)
        api.steps.patch_step.assert_called_once()

    def test_step_deleted_after_toggle_reports_missing_step(self):
        entity, api, rows = self.entity()
        step = entity.steps["Align"]
        api.steps.patch_step.side_effect = lambda *args, **kwargs: rows.clear()
        with self.assertRaisesRegex(KeyError, "Step 4 disappeared"):
            step.status = True
        self.assertFalse(step.status)
        self.assertEqual(entity.steps_by_id, {})
        api.steps.patch_step.assert_called_once()

    def test_failed_readback_does_not_confirm_update_or_retry_toggle(self):
        entity, api, rows = self.entity()
        step = entity.steps["Align"]
        api.experiments.get_experiment.side_effect = TimeoutError("Readback failed")
        with self.assertRaisesRegex(TimeoutError, "Readback failed"):
            step.status = True
        self.assertFalse(step.status)
        self.assertEqual(rows[0]["finished"], 1)
        api.steps.patch_step.assert_called_once()

    def async_handle(self, entity, api):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        bridge = AsyncBridge(spool_directory=Path(temporary.name) / "spool", client_factory=lambda: api)

        def close():
            with suppress(RuntimeError):
                bridge.close(timeout=5)

        self.addCleanup(close)
        bridge._client = api
        bridge._experiments[entity.ID] = entity
        prerequisite = Future()
        prerequisite.set_result(None)
        return bridge, AsyncExperiment(bridge, entity.ID, "Experiment", prerequisite)

    def test_async_checkbox_conflict_reaches_flush_and_preserves_confirmed_value(self):
        def gui_edit(rows):
            rows[0]["finished"] = 1

        entity, api, _ = self.entity(before_toggle=gui_edit)
        bridge, handle = self.async_handle(entity, api)
        step = handle.steps["Align"]
        step.status = True
        with self.assertRaisesRegex(RuntimeError, "currently unchecked"):
            bridge.flush(timeout=5)
        self.assertFalse(step.status)
        self.assertFalse(handle.steps["Align"].status)
        api.steps.patch_step.assert_called_once()

    def test_async_direct_check_reports_conflict_in_future(self):
        def gui_edit(rows):
            rows[0]["finished"] = 1

        entity, api, _ = self.entity(before_toggle=gui_edit)
        bridge, handle = self.async_handle(entity, api)
        future = handle.check_step(4)
        with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
            future.result(timeout=5)
        with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
            bridge.flush(timeout=5)
        api.steps.patch_step.assert_called_once()
