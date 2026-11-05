"""Offline checks for existing category, status and team tag selections."""

from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock

from elablink import Client, Experiment, InvalidCategory, InvalidStatus, InvalidTag, Resource


class ChoiceValidationTests(unittest.TestCase):
    @staticmethod
    def tag_response(rows):
        return SimpleNamespace(data=json.dumps(rows).encode(), release_conn=Mock())

    def client(self):
        client = Client.__new__(Client)
        client._get_team_id = Mock(return_value=4)
        client.categories = Mock()
        client.categories.read_team_experiments_categories.return_value = [
            SimpleNamespace(title="Calibration", id=3),
            SimpleNamespace(title="Measurement", id=7),
        ]
        client.statuses = Mock()
        client.statuses.read_team_experiments_status.return_value = [
            SimpleNamespace(title="In progress", id=2),
            SimpleNamespace(title="Completed", id=6),
        ]
        client.resource_categories = Mock()
        client.resource_categories.read_items_types.return_value = [
            SimpleNamespace(title="Lasers", id=8)
        ]
        client.resource_statuses = Mock()
        client.resource_statuses.read_team_items_status.return_value = [
            SimpleNamespace(title="Available", id=9)
        ]
        client.team_tags = Mock()
        client.team_tags.read_team_tags.return_value = self.tag_response([
            {"tag": "optics", "id": 11},
            {"tag": "calibration", "id": 12},
        ])
        client.tags = Mock()
        client.experiments = Mock()
        client.experiments.get_experiment.return_value.to_dict.return_value = {}
        client.items = Mock()
        client.items.get_item.return_value.to_dict.return_value = {}
        return client

    def test_missing_category_or_status_lists_current_names_and_ids(self):
        for entity_type, field, error, expected in (
            (Experiment, "category", InvalidCategory, "'Calibration' (ID 3)"),
            (Experiment, "status", InvalidStatus, "'Completed' (ID 6)"),
            (Resource, "category", InvalidCategory, "'Lasers' (ID 8)"),
            (Resource, "status", InvalidStatus, "'Available' (ID 9)"),
        ):
            for value in ("Missing", 999, "999", True):
                with self.subTest(entity_type=entity_type, field=field, value=value):
                    client = self.client()
                    entity = entity_type(client, 17)
                    with self.assertRaises(error) as raised:
                        setattr(entity, field, value)
                    self.assertIn(expected, str(raised.exception))
                    self.assertIn("not found", str(raised.exception))
                    client.experiments.patch_experiment.assert_not_called()
                    client.items.patch_item.assert_not_called()

    def test_known_resource_numeric_category_and_status_are_accepted(self):
        client = self.client()
        resource = Resource(client, 17)
        resource.category = 8
        resource.status = "9"
        self.assertEqual(
            client.items.patch_item.call_args_list,
            [unittest.mock.call(id=17, body={"category": 8}),
             unittest.mock.call(id=17, body={"status": 9})],
        )

    def test_tag_catalog_is_fresh_and_separate_from_attached_tags(self):
        client = self.client()
        experiment = Experiment(client, 17)
        experiment._cache = {"tags": "attached"}
        self.assertEqual(experiment.tags, ["attached"])
        self.assertEqual(experiment.available_tags, {"optics": 11, "calibration": 12})
        client.team_tags.read_team_tags.return_value = self.tag_response([
            {"tag": "new tag", "id": 20}
        ])
        self.assertEqual(experiment.available_tags, {"new tag": 20})
        self.assertEqual(client.team_tags.read_team_tags.call_args_list,
                         [unittest.mock.call(4, _preload_content=False),
                          unittest.mock.call(4, _preload_content=False)])
        client.team_tags.read_team_tags.return_value.release_conn.assert_called_once()

    def test_invalid_tag_response_releases_http_connection(self):
        client = self.client()
        response = client.team_tags.read_team_tags.return_value
        response.data = b"not JSON"
        with self.assertRaises(json.JSONDecodeError):
            client.tag_dict
        response.release_conn.assert_called_once()

    def test_existing_team_tag_can_be_attached_to_an_untagged_entity(self):
        for entity_type, route in ((Experiment, "experiments"), (Resource, "items")):
            with self.subTest(entity_type=entity_type):
                client = self.client()
                entity = entity_type(client, 17)
                entity._cache = {"tags": ""}
                entity.add_tag("calibration")
                client.tags.post_tag.assert_called_once_with(
                    route, 17, body={"tag": "calibration"}
                )

    def test_missing_tag_never_posts_and_lists_team_catalog(self):
        client = self.client()
        experiment = Experiment(client, 17)
        with self.assertRaises(InvalidTag) as raised:
            experiment.add_tag("Missing")
        self.assertIn("'optics' (ID 11)", str(raised.exception))
        self.assertIn("'calibration' (ID 12)", str(raised.exception))
        client.tags.post_tag.assert_not_called()

    def test_catalog_failure_is_not_presented_as_an_empty_list(self):
        client = self.client()
        client.team_tags.read_team_tags.side_effect = OSError("server unavailable")
        with self.assertRaisesRegex(OSError, "server unavailable"):
            Experiment(client, 17).add_tag("optics")
        client.tags.post_tag.assert_not_called()

    def test_empty_choices_are_explicit_in_error(self):
        client = self.client()
        client.team_tags.read_team_tags.return_value = self.tag_response([])
        with self.assertRaisesRegex(InvalidTag, "Available tags: none"):
            Experiment(client, 17).add_tag("optics")

    def test_invalid_resource_category_is_rejected_before_creation(self):
        for value in ("Missing", 999, "999"):
            with self.subTest(value=value):
                client = self.client()
                with self.assertRaisesRegex(InvalidCategory, "Lasers"):
                    client.create_resource("Laser", category=value)
                client.items.post_item_with_http_info.assert_not_called()

    def test_invalid_experiment_category_is_rejected_before_creation(self):
        client = self.client()
        client.enforce_unique_titles = False
        with self.assertRaisesRegex(InvalidCategory, "Calibration"):
            client.create_experiment("Run", category="Missing")
        client.experiments.post_experiment_with_http_info.assert_not_called()
