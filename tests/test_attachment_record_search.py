"""Search queries and category catalogs for the desktop record picker."""

import json
import unittest
from unittest.mock import Mock

import elabapi_python as ep

from elablink._attachment_record_search import filter_choices, search_records
from elablink.client import _api_type


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.config = {"TEAM_ID": "7"}
        api = ep.ApiClient()
        self.client.api_client = api
        self.client.items = ep.ItemsApi(api)
        self.client.experiments = ep.ExperimentsApi(api)
        self.responses = []
        self.requests = []

        def call(path, method, path_params, query_params, *args, **kwargs):
            self.assertEqual(method, "GET")
            self.assertIsNotNone(kwargs["_request_timeout"])
            query = api.parameters_to_tuples(query_params, kwargs.get("collection_formats"))
            self.requests.append((path, path_params, query))
            response = Mock()
            response.data = json.dumps(self.responses.pop(0)).encode()
            return response

        api.call_api = call

    def test_filters_use_native_query_and_keep_duplicate_titles(self):
        self.responses = [[
            {"id": 52, "title": "Run", "tags_decoded": [{"tag": "A|B"}]},
            {"id": 44, "title": "Run", "tags": "A|B"},
        ]]
        rows = search_records(self.client, "experiments", "  MPDRIE  ", [2, "null"],
                              [8], ["Phononic crystal", "notion-import"], 25, 25)
        self.assertEqual(self.requests, [("/experiments", {}, [
            ("q", "MPDRIE"), ("category", "2,null"), ("status", "8"),
            ("tags[]", "Phononic crystal"), ("tags[]", "notion-import"),
            ("limit", 25), ("offset", 25),
        ])])
        self.assertEqual([(r["id"], r["tags"]) for r in rows], [(52, ["A|B"]), (44, ["A", "B"])])

    def test_unfiltered_resources_use_only_pagination(self):
        self.responses = [[{"id": 42, "tags": None}]]
        rows = search_records(self.client, "items", "", [], [], [], 0, 10)
        self.assertEqual(rows[0]["tags"], [])
        self.assertEqual(self.requests, [("/items", {}, [("limit", 10), ("offset", 0)])])

    def test_team_resource_options_use_categories_not_templates(self):
        api = self.client.api_client
        self.client.resource_statuses = _api_type("ResourceStatusApi", "ResourcesStatusApi")(api)
        self.client.team_tags = ep.TeamTagsApi(api)
        self.responses = [[{"id": 4, "title": "Cryostat", "color": "abcdef"}],
                          [{"id": 1, "title": "Available", "color": "00ff00"}],
                          [{"tag": "z"}, {"tag": "A"}]]
        categories, statuses, tags = filter_choices(self.client, "items")
        self.assertEqual([path for path, _, _ in self.requests], [
            "/teams/{id}/resources_categories", "/teams/{id}/items_status", "/teams/{id}/tags",
        ])
        self.assertTrue(all(params == {"id": "7"} for _, params, _ in self.requests))
        self.assertEqual(categories[0]["title"], "Cryostat")
        self.assertEqual(statuses[0]["color"], "00ff00")
        self.assertEqual(tags, ["A", "z"])
