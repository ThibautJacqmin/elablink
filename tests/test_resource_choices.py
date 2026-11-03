"""Offline tests for resource category choices and name-based selection."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from elablink import Client, InvalidCategory, Resource


class ResourceChoiceTests(unittest.TestCase):
    def client(self):
        client = Client.__new__(Client)
        client.resource_categories = Mock()
        client.resource_categories.read_items_types.return_value = [
            SimpleNamespace(title="Lasers", id=3),
            SimpleNamespace(title="Optical tables", id=7),
        ]
        client.items = Mock()
        client.items.post_item_with_http_info.return_value = (
            None, None, {"Location": "/api/v2/items/17"}
        )
        client.items.get_item.return_value.to_dict.return_value = {"id": 17}
        return client

    def test_client_initializes_resource_category_api(self):
        api_client = Mock()

        def configure(client):
            client.api_client = api_client

        with (
            patch("elablink.client.resolve_config_path", return_value=Path("unused.conf")),
            patch.object(Client, "_read_configuration_file", return_value={}),
            patch.object(Client, "_api_configuration", new=configure),
            patch("elablink.client.ep.ItemsTypesApi") as category_api,
        ):
            client = Client()

        category_api.assert_called_once_with(api_client)
        self.assertIs(client.resource_categories, category_api.return_value)

    def test_accessible_category_choices_do_not_use_team_category_endpoint(self):
        client = self.client()
        client._get_team_id = Mock(side_effect=AssertionError("No team lookup needed"))
        self.assertEqual(
            client.resource_category_dict, {"Lasers": 3, "Optical tables": 7}
        )
        client.resource_categories.read_items_types.assert_called_once_with()
        client._get_team_id.assert_not_called()

    def test_no_accessible_categories_returns_empty_mapping(self):
        client = self.client()
        for response in ([], None):
            with self.subTest(response=response):
                client.resource_categories.read_items_types.return_value = response
                self.assertEqual(client.resource_category_dict, {})

    def test_resource_uses_resource_category_choices(self):
        client = self.client()
        resource = Resource(client, 17)
        self.assertEqual(resource._category_values(), {"Lasers": 3, "Optical tables": 7})

    def test_resource_category_accepts_case_insensitive_name(self):
        client = self.client()
        resource = Resource(client, 17)
        resource.category = "optical TABLES"
        client.items.patch_item.assert_called_once_with(17, body={"category": 7})

    def test_numeric_category_assignment_does_not_require_choice_lookup(self):
        client = self.client()
        resource = Resource(client, 17)
        resource.category = 99
        client.items.patch_item.assert_called_once_with(17, body={"category": 99})
        client.resource_categories.read_items_types.assert_not_called()

    def test_unknown_category_name_does_not_modify_resource(self):
        client = self.client()
        resource = Resource(client, 17)
        with self.assertRaises(InvalidCategory):
            resource.category = "Missing category"
        client.items.patch_item.assert_not_called()

    def test_create_resource_resolves_name_before_posting(self):
        client = self.client()
        resource = client.create_resource("Blue laser", category="LASERS")
        self.assertIsInstance(resource, Resource)
        self.assertEqual(resource.ID, 17)
        client.items.post_item_with_http_info.assert_called_once_with(
            body={"category_id": 3}
        )
        client.items.patch_item.assert_called_once_with(
            17, body={"title": "Blue laser", "category": 3}
        )

    def test_create_resource_keeps_numeric_selection_without_lookup(self):
        for category in (99, "99"):
            with self.subTest(category=category):
                client = self.client()
                client.create_resource("Blue laser", category=category)
                client.items.post_item_with_http_info.assert_called_once_with(
                    body={"category_id": 99}
                )
                client.items.patch_item.assert_called_once_with(
                    17, body={"title": "Blue laser", "category": 99}
                )
                client.resource_categories.read_items_types.assert_not_called()

    def test_create_resource_without_category_keeps_server_default(self):
        client = self.client()
        client.create_resource("Blue laser")
        client.items.post_item_with_http_info.assert_called_once_with(body={})
        client.items.patch_item.assert_called_once_with(17, body={"title": "Blue laser"})
        client.resource_categories.read_items_types.assert_not_called()

    def test_unknown_name_is_rejected_before_resource_creation(self):
        client = self.client()
        with self.assertRaises(InvalidCategory):
            client.create_resource("Blue laser", category="Missing category")
        client.items.post_item_with_http_info.assert_not_called()
        client.items.patch_item.assert_not_called()


if __name__ == "__main__":
    unittest.main()
