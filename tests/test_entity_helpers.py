"""Offline tests for reusable entity helpers, independent of any migration."""

from datetime import date
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from elabmate import ElabExperiment, ElabResource


class EntityHelperTests(unittest.TestCase):
    def entity(self, resource=False):
        api = Mock()
        api.configuration.host = "https://lab.example/api/v2"
        entity = (ElabResource if resource else ElabExperiment)(api, 17)
        data = {"body": "", "steps": [], "metadata": "{}"}
        entity._cache = data
        if resource:
            api.items.get_item.return_value.to_dict.return_value = data
        else:
            api.experiments.get_experiment.return_value.to_dict.return_value = data
        return entity, api, data

    def test_date_and_custom_fields_keep_unrelated_metadata(self):
        entity, api, data = self.entity()
        data["metadata"] = json.dumps({"elabftw": {"display_main_text": True}})
        entity.started_on = date(2020, 4, 3)
        api.experiments.patch_experiment.assert_called_with(
            17, body={"date": "2020-04-03"}
        )
        entity.set_extra_fields({"Author": "Ada"})
        metadata = json.loads(
            api.experiments.patch_experiment.call_args.kwargs["body"]["metadata"]
        )
        self.assertTrue(metadata["elabftw"]["display_main_text"])
        self.assertEqual(metadata["extra_fields"]["Author"]["value"], "Ada")

    def test_inline_url_retains_server_subpath_and_escapes_html(self):
        entity, api, _ = self.entity()
        api.configuration.host = "https://lab.example/notebook/api/v2"
        api.uploads.read_uploads.return_value = [
            {"id": 3, "real_name": "photo.png", "long_name": "ab/cd.png", "storage": 1}
        ]
        html = entity.inline_image_html("photo.png", alt='A "setup"')
        self.assertIn(
            "https://lab.example/notebook/app/download.php?f=ab%2Fcd.png", html
        )
        self.assertIn("&amp;storage=1", html)
        self.assertIn("A &quot;setup&quot;", html)

    def test_existing_link_skips_create_and_resource_uses_items_endpoint(self):
        entity, api, _ = self.entity(resource=True)
        api.experiment_links.read_entity_experiments_links.return_value = [
            SimpleNamespace(entityid=9)
        ]
        self.assertFalse(entity.link_experiment(9))
        api.experiment_links.post_entity_experiments_links.assert_not_called()
        api.item_links.read_entity_items_links.return_value = []
        self.assertTrue(entity.link_resource(7))
        api.item_links.post_entity_items_links.assert_called_once_with("items", 17, 7)

    def test_completion_toggle_is_only_sent_when_state_differs(self):
        entity, api, data = self.entity()
        data["steps"] = [{"id": 4, "body": "Measure", "finished": 1}]
        api.steps.read_steps.return_value = data["steps"]
        self.assertFalse(entity.ensure_step("Measure", finished=True))
        api.steps.patch_step.assert_not_called()
        entity.ensure_step("Measure", finished=False)
        api.steps.patch_step.assert_called_once_with(
            "experiments", 17, 4, body={"action": "finish"}
        )

    def test_repeated_labels_target_the_requested_occurrence(self):
        entity, api, data = self.entity()
        data["steps"] = [
            {"id": 4, "body": "Measure", "finished": 1},
            {"id": 5, "body": "Measure", "finished": 0},
        ]
        api.steps.read_steps.return_value = data["steps"]
        self.assertFalse(entity.ensure_step("Measure", occurrence=1, finished=True))
        api.steps.patch_step.assert_called_once_with(
            "experiments", 17, 5, body={"action": "finish"}
        )
        api.steps.post_step.assert_not_called()


if __name__ == "__main__":
    unittest.main()
