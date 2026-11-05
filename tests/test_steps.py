"""Offline tests for dictionary-based step and choice access."""

from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from elablink import Experiment, Resource, Step


class StepTests(unittest.TestCase):
    def entity(self, rows=(), resource=False):
        rows = deepcopy(list(rows))
        api = Mock()
        api.category_dict = {"Calibration": 3}
        api.resource_category_dict = {"Lasers": 5}
        api.status_dict = {"Completed": 7}
        api.resource_status_dict = {"Available": 8}
        model = SimpleNamespace(to_dict=lambda: {"steps": deepcopy(rows)})
        api.experiments.get_experiment.return_value = model
        api.items.get_item.return_value = model
        api.steps.read_steps.side_effect = lambda *args: deepcopy(rows)

        def create(entity_type, entity_id, *, body):
            step_id = max((row["id"] for row in rows), default=0) + 1
            rows.append({"id": step_id, "body": body["body"], "finished": 0})
            return None, 201, {"Location": f"/{entity_type}/{entity_id}/steps/{step_id}"}

        def toggle(entity_type, entity_id, step_id, *, body):
            self.assertEqual(body, {"action": "finish"})
            row = next(row for row in rows if row["id"] == step_id)
            row["finished"] = 0 if row["finished"] else 1

        api.steps.post_step_with_http_info.side_effect = create
        api.steps.patch_step.side_effect = toggle
        entity = (Resource if resource else Experiment)(api, 17)
        entity._cache = model.to_dict()
        return entity, api, rows

    def test_steps_are_dictionary_of_server_backed_objects(self):
        entity, api, _ = self.entity([
            {"id": 4, "body": "Align the beam", "finished": 0},
            {"id": 5, "body": "Measure power", "finished": 1},
        ])
        steps = entity.steps
        self.assertIsInstance(steps, dict)
        self.assertEqual(list(steps), ["Align the beam", "Measure power"])
        self.assertIsInstance(steps["Align the beam"], Step)
        self.assertEqual(steps["Align the beam"].ID, 4)
        self.assertFalse(steps["Align the beam"].status)
        self.assertTrue(steps["Measure power"].status)
        api.steps.read_steps.assert_not_called()

    def test_checkbox_assignment_does_not_toggle_an_already_checked_step(self):
        entity, api, rows = self.entity([{"id": 4, "body": "Align", "finished": 0}])
        step = entity.steps["Align"]
        step.status = True
        step.status = True
        self.assertTrue(step.status)
        self.assertEqual(rows[0]["finished"], 1)
        api.steps.patch_step.assert_called_once_with(
            "experiments", 17, 4, body={"action": "finish"}
        )
        step.status = False
        self.assertFalse(step.status)
        self.assertEqual(rows[0]["finished"], 0)

    def test_retained_step_targets_its_id_after_remote_rename(self):
        entity, _, rows = self.entity([{"id": 4, "body": "Align", "finished": 0}])
        step = entity.steps["Align"]
        rows[0]["body"] = "Renamed"
        rows.append({"id": 5, "body": "Align", "finished": 0})
        step.status = True
        self.assertEqual([row["finished"] for row in rows], [1, 0])

    def test_deleted_step_is_not_recreated(self):
        entity, api, rows = self.entity([{"id": 4, "body": "Align", "finished": 0}])
        step = entity.steps["Align"]
        rows.clear()
        with self.assertRaises(KeyError):
            step.status = True
        self.assertFalse(step.status)
        api.steps.patch_step.assert_not_called()
        api.steps.post_step_with_http_info.assert_not_called()

    def test_missing_checkbox_name_does_not_create_a_step(self):
        entity, api, _ = self.entity()
        with self.assertRaises(KeyError):
            entity.check_step("Missing")
        api.steps.post_step_with_http_info.assert_not_called()

    def test_repeated_labels_require_explicit_disambiguation(self):
        entity, api, rows = self.entity([
            {"id": 4, "body": "Align", "finished": 0},
            {"id": 5, "body": "Align", "finished": 0},
        ])
        with self.assertRaises(ValueError):
            _ = entity.steps
        self.assertEqual(list(entity.steps_by_id), [4, 5])
        with self.assertRaises(ValueError):
            entity.check_step("Align")
        api.steps.patch_step.assert_not_called()
        entity.check_step("Align", occurrence=1)
        self.assertEqual([row["finished"] for row in rows], [0, 1])
        entity.check_step(4)
        self.assertEqual([row["finished"] for row in rows], [1, 1])

    def test_add_checked_step_targets_new_id_even_with_duplicate_text(self):
        entity, _, rows = self.entity([{"id": 4, "body": "Align", "finished": 0}])
        step = entity.add_step("Align", checked=True)
        self.assertEqual(step.ID, 5)
        self.assertTrue(step.status)
        self.assertEqual([row["finished"] for row in rows], [0, 1])

    def test_add_unchecked_step_returns_an_editable_object(self):
        entity, api, rows = self.entity()
        step = entity.add_step("Align")
        self.assertFalse(step.status)
        api.steps.patch_step.assert_not_called()
        step.status = True
        self.assertEqual(rows[0]["finished"], 1)

    def test_resource_steps_use_items_endpoint(self):
        resource, api, _ = self.entity(resource=True)
        resource.add_step("Check optics", checked=True)
        api.steps.post_step_with_http_info.assert_called_once_with(
            "items", 17, body={"body": "Check optics"}
        )
        api.steps.patch_step.assert_called_once_with(
            "items", 17, 1, body={"action": "finish"}
        )

    def test_missing_embedded_steps_are_loaded_and_cached(self):
        entity, api, _ = self.entity([{"id": 4, "body": "Align", "finished": 0}])
        entity._cache = {}
        self.assertIn("Align", entity.steps)
        self.assertIn("Align", entity.steps)
        api.steps.read_steps.assert_called_once_with("experiments", 17)

    def test_step_listing_does_not_hide_server_errors(self):
        entity, api, _ = self.entity()
        entity._cache = {}
        api.steps.read_steps.side_effect = RuntimeError("Offline")
        with self.assertRaisesRegex(RuntimeError, "Offline"):
            _ = entity.steps

    def test_boolean_validation_prevents_unintended_writes(self):
        entity, api, _ = self.entity([{"id": 4, "body": "Align", "finished": 0}])
        with self.assertRaises(TypeError):
            entity.steps["Align"].status = "false"
        with self.assertRaises(TypeError):
            entity.check_step("Align", checked="false")
        with self.assertRaises(TypeError):
            entity.add_step("Align", checked=1)
        api.steps.patch_step.assert_not_called()
        api.steps.post_step_with_http_info.assert_not_called()

    def test_invalid_occurrence_and_missing_occurrence_are_rejected(self):
        entity, _, _ = self.entity([{"id": 4, "body": "Align", "finished": 0}])
        for value in (-1, True, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                entity.check_step("Align", occurrence=value)
        with self.assertRaises(IndexError):
            entity.check_step("Align", occurrence=1)

    def test_category_and_status_choices_can_be_assigned(self):
        entity, api, _ = self.entity()
        self.assertEqual(entity.categories, {"Calibration": 3})
        self.assertEqual(entity.statuses, {"Completed": 7})
        entity.category = entity.categories["Calibration"]
        entity.status = entity.statuses["Completed"]
        self.assertEqual(api.experiments.patch_experiment.call_args_list[0].kwargs,
                         {"id": 17, "body": {"category": 3}})
        self.assertEqual(api.experiments.patch_experiment.call_args_list[1].kwargs,
                         {"id": 17, "body": {"status": 7}})

    def test_step_future_failure_does_not_claim_checkbox_changed(self):
        future = Future()
        step = Step(4, "Align", False, lambda *args: future)
        step.status = True
        self.assertFalse(step.status)
        future.set_exception(RuntimeError("Failed"))
        self.assertFalse(step.status)

    def test_step_future_success_confirms_checkbox_change(self):
        future = Future()
        step = Step(4, "Align", False, lambda *args: future)
        step.status = True
        future.set_result(None)
        self.assertTrue(step.status)

    def test_failed_later_edit_keeps_the_last_successful_checkbox_state(self):
        first, second = Future(), Future()
        futures = iter((first, second))
        step = Step(4, "Align", False, lambda *args: next(futures))
        step.status = True
        step.status = False
        first.set_result(None)
        second.set_exception(RuntimeError("Failed"))
        self.assertTrue(step.status)

    def test_cancelled_edit_does_not_claim_checkbox_changed(self):
        future = Future()
        step = Step(4, "Align", False, lambda *args: future)
        step.status = True
        future.cancel()
        self.assertFalse(step.status)


if __name__ == "__main__":
    unittest.main()
