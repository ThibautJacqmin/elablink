"""Offline tests for reusable entity helpers, independent of any migration."""

from datetime import date
import json
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, create_autospec

from elablink import Client, Experiment, Resource


class EntityHelperTests(unittest.TestCase):
    def test_replacement_explicitly_selects_replace_action(self):
        client = Client.__new__(Client)
        client.api_client = Mock()
        client.replace_upload("experiments", 17, 3, "signal.h5", "New analysis")
        kwargs = client.api_client.call_api.call_args.kwargs
        self.assertEqual(client.api_client.call_api.call_args.args,
                         ("/{entity_type}/{id}/uploads/{subid}", "POST"))
        self.assertEqual(kwargs["path_params"],
                         {"entity_type": "experiments", "id": 17, "subid": 3})
        self.assertIn(("action", "replace"), kwargs["post_params"])
        self.assertEqual(kwargs["files"], {"file": "signal.h5"})

    def entity(self, resource=False):
        api = Mock()
        api.configuration.host = "https://lab.example/api/v2"
        entity = (Resource if resource else Experiment)(api, 17)
        data = {"body": "", "steps": [], "metadata": "{}"}
        entity._cache = data
        if resource:
            api.items.get_item.return_value.to_dict.return_value = data
        else:
            api.experiments.get_experiment.return_value.to_dict.return_value = data
        return entity, api, data

    def test_changed_attachment_routes_to_replacement_helper(self):
        entity, api, _ = self.entity()
        api.uploads.read_uploads.return_value = [{"id": 3, "real_name": "signal.h5"}]
        # Use the real helper signature so keyword mistakes fail before live tests.
        api.replace_upload = create_autospec(Client.__new__(Client).replace_upload)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signal.h5"
            path.write_bytes(b"new analysis")
            entity.upload_file(path)
            api.replace_upload.assert_called_once_with(
                "experiments", 17, 3, file_path=str(path), comment="Uploaded via API"
            )
        api.uploads.post_upload.assert_not_called()

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
        self.assertTrue(entity.check_step("Measure").status)
        api.steps.patch_step.assert_not_called()
        entity.check_step("Measure", checked=False)
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
        self.assertTrue(entity.check_step("Measure", occurrence=1).status)
        api.steps.patch_step.assert_called_once_with(
            "experiments", 17, 5, body={"action": "finish"}
        )
        api.steps.post_step.assert_not_called()


if __name__ == "__main__":
    unittest.main()
