"""Exercise the installed generated SDK, mocking only its HTTP transport."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from elablink import Client, Experiment, Resource


class SDKContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        configuration = Path(temporary.name) / "elablink.conf"
        configuration.write_text(
            "API_HOST_URL=https://offline.invalid/api/v2\n"
            "API_KEY=offline-test-key\n"
            "VERIFY_SSL=true\n"
            "TEAM_ID=4\n"
            "UNIQUE_EXPERIMENTS_TITLES=false\n",
            encoding="utf-8",
        )
        self.client = Client(configuration)
        transport = patch.object(self.client.api_client, "call_api")
        self.request = transport.start()
        self.addCleanup(transport.stop)

    def test_generated_sdk_authentication_sends_the_raw_authorization_key(self):
        headers = {}
        self.client.api_client.update_params_for_auth(headers, [], ["token"])
        self.assertEqual(headers, {"Authorization": "offline-test-key"})
        self.assertEqual(self.client.api_client.default_headers["Authorization"], "offline-test-key")

    def test_catalogs_keep_their_existing_server_endpoints(self):
        self.request.return_value = []
        for attribute, endpoint, parameters in (
            ("category_dict", "/teams/{id}/experiments_categories", {"id": 4}),
            ("status_dict", "/teams/{id}/experiments_status", {"id": 4}),
            ("resource_category_dict", "/items_types", {}),
            ("resource_status_dict", "/teams/{id}/items_status", {"id": 4}),
        ):
            with self.subTest(attribute=attribute):
                self.request.reset_mock()
                self.assertEqual(getattr(self.client, attribute), {})
                self.request.assert_called_once()
                self.assertEqual(self.request.call_args.args[:3],
                                 (endpoint, "GET", parameters))

    def test_entity_edits_reach_sdk_patch_and_refresh_with_correct_body_and_id(self):
        self.request.return_value = SimpleNamespace(to_dict=lambda: {"id": 17, "title": "Changed"})
        for entity_type, endpoint in (
            (Experiment, "/experiments/{id}"), (Resource, "/items/{id}"),
        ):
            with self.subTest(entity=entity_type.__name__):
                self.request.reset_mock()
                entity = entity_type(self.client, 17)
                entity.title = "Changed"
                edit, refresh = self.request.call_args_list
                self.assertEqual(edit.args[:3], (endpoint, "PATCH", {"id": 17}))
                self.assertEqual(edit.kwargs["body"], {"title": "Changed"})
                self.assertEqual(refresh.args[:3], (endpoint, "GET", {"id": 17}))
                self.assertEqual(entity.title, "Changed")

    def test_creation_uses_the_generated_post_method_and_location_header(self):
        self.request.return_value = (None, None, {"Location": "/api/v2/experiments/17"})
        entity = self.client.create_experiment("Measurement")
        self.assertEqual(entity.ID, 17)
        self.request.assert_called_once()
        self.assertEqual(self.request.call_args.args[:2], ("/experiments", "POST"))
        self.assertEqual(self.request.call_args.kwargs["body"], {"title": "Measurement"})

    def test_attachment_operations_keep_their_exact_record_and_upload_ids(self):
        self.request.return_value = None
        uploads = self.client.uploads
        operations = (
            (lambda: uploads.post_upload("experiments", 17, file="data.h5"),
             "/{entity_type}/{id}/uploads", "POST", {"entity_type": "experiments", "id": 17}),
            (lambda: uploads.patch_upload("items", 23, 9, body={"real_name": "renamed.h5"}),
             "/{entity_type}/{id}/uploads/{subid}", "PATCH", {"entity_type": "items", "id": 23, "subid": 9}),
            (lambda: uploads.delete_upload("items", 23, 9),
             "/{entity_type}/{id}/uploads/{subid}", "DELETE", {"entity_type": "items", "id": 23, "subid": 9}),
            (lambda: uploads.read_upload("experiments", 17, 9, format="binary", _preload_content=False),
             "/{entity_type}/{id}/uploads/{subid}", "GET", {"entity_type": "experiments", "id": 17, "subid": 9}),
        )
        for operation, endpoint, method, parameters in operations:
            with self.subTest(method=method):
                self.request.reset_mock()
                operation()
                self.request.assert_called_once()
                self.assertEqual(self.request.call_args.args[:3], (endpoint, method, parameters))
                if method == "GET":
                    self.assertFalse(self.request.call_args.kwargs["_preload_content"])


if __name__ == "__main__":
    unittest.main()
