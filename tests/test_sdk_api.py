"""Exercise the installed generated SDK, mocking only its HTTP transport."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from elablink import Client, Experiment, Resource, OwnershipTransferError
from elabapi_python.rest import ApiException


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

    @staticmethod
    def raw_record(payload):
        response = Mock()
        response.data = json.dumps(payload).encode()
        return response

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
        self.request.return_value = self.raw_record({"id": 17, "title": "Changed"})
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

    def test_ownership_transfer_uses_generated_patch_then_reads_back(self):
        for entity_type, endpoint in (
            (Experiment, "/experiments/{id}"), (Resource, "/items/{id}"),
        ):
            with self.subTest(entity=entity_type.__name__):
                self.request.reset_mock()
                raw = self.raw_record({"id": 17, "userid": 9, "team": 4})
                self.request.side_effect = [None, raw]
                entity = entity_type(self.client, 17)
                entity.transfer_ownership(9)
                transfer, refresh = self.request.call_args_list
                self.assertEqual(transfer.args[:3], (endpoint, "PATCH", {"id": 17}))
                self.assertEqual(transfer.kwargs["body"],
                                 {"action": "updateowner", "userid": 9, "team": 4})
                self.assertEqual(refresh.args[:3], (endpoint, "GET", {"id": 17}))
                self.assertFalse(refresh.kwargs["_preload_content"])
                raw.close.assert_called_once_with()
                raw.release_conn.assert_called_once_with()
                self.assertEqual(entity.owner_id, 9)

    def test_unverified_ownership_responses_do_not_confirm_a_transfer(self):
        for record in (
            [], {}, {"id": 17, "userid": 9}, {"userid": 9, "team": 4},
            {"id": 18, "userid": 9, "team": 4}, {"id": 17, "userid": 8, "team": 4},
            {"id": 17, "userid": 9, "team": 3}, {"id": 17, "userid": 9, "team": "4"},
            {"id": 17, "userid": 9, "team": True},
        ):
            for entity_type in (Experiment, Resource):
                with self.subTest(record=record, entity_type=entity_type.__name__):
                    raw = self.raw_record(record)
                    self.request.reset_mock()
                    self.request.side_effect = [None, raw]
                    entity = entity_type(self.client, 17)
                    with self.assertRaises(OwnershipTransferError) as caught:
                        entity.transfer_ownership(9)
                    self.assertEqual(caught.exception.entity_id, 17)
                    self.assertIsNone(entity._cache)
                    self.assertEqual(self.request.call_count, 2)
                    raw.close.assert_called_once_with()
                    raw.release_conn.assert_called_once_with()

    def test_unreadable_transfer_confirmation_preserves_id_and_releases_response(self):
        raw = self.raw_record({})
        raw.data = b"invalid JSON"
        for confirmation in (ApiException(status=403), raw):
            with self.subTest(confirmation=type(confirmation).__name__):
                self.request.reset_mock()
                self.request.side_effect = [None, confirmation]
                entity = Experiment(self.client, 17)
                with self.assertRaises(OwnershipTransferError) as caught:
                    entity.transfer_ownership(9)
                self.assertEqual(self.request.call_count, 2)
                self.assertEqual(caught.exception.entity_id, 17)
                self.assertIsNone(entity._cache)
                if isinstance(confirmation, ApiException):
                    self.assertIs(caught.exception.__cause__, confirmation)
        raw.close.assert_called_once_with()
        raw.release_conn.assert_called_once_with()

    def test_creation_with_owner_posts_transfers_then_verifies(self):
        self.request.side_effect = [
            (None, None, {"Location": "/api/v2/experiments/17"}),
            None,
            self.raw_record({"id": 17, "userid": 9, "team": 4}),
        ]
        experiment = self.client.create_experiment("Measurement", owner_id=9)
        create, transfer, refresh = self.request.call_args_list
        self.assertEqual(create.args[:2], ("/experiments", "POST"))
        self.assertEqual(create.kwargs["body"], {"title": "Measurement"})
        self.assertEqual(transfer.kwargs["body"],
                         {"action": "updateowner", "userid": 9, "team": 4})
        self.assertEqual(refresh.args[:3], ("/experiments/{id}", "GET", {"id": 17}))
        self.assertEqual(experiment.owner_id, 9)

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
