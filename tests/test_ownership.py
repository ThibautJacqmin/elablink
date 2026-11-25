"""Offline ownership behavior, including partial success and creation order."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from elabapi_python.rest import ApiException

from elablink import (
    Client, DeletedExperiment, Experiment, OwnershipTransferError, Resource,
)


class OwnershipTests(unittest.TestCase):
    def entity(self, resource=False):
        api = Mock()
        api._get_team_id.return_value = 4
        row = {"userid": 1, "team": 4, "title": "Test"}
        entity = (Resource if resource else Experiment)(api, 17)
        entity._cache = dict(row)
        endpoint = api.items if resource else api.experiments
        patch = endpoint.patch_item if resource else endpoint.patch_experiment
        get = endpoint.get_item if resource else endpoint.get_experiment

        def transfer(*, id, body):
            self.assertEqual(id, 17)
            row.update(userid=body["userid"], team=body["team"])

        patch.side_effect = transfer
        get.side_effect = lambda id: SimpleNamespace(to_dict=lambda: dict(row))
        return entity, api, row, patch, get

    def test_transfer_confirms_owner_and_refreshes_stale_cache_for_both_entities(self):
        for resource in (False, True):
            with self.subTest(resource=resource):
                entity, api, _, patch, get = self.entity(resource)
                self.assertEqual(entity.owner_id, 1)
                entity.transfer_ownership(9)
                patch.assert_called_once_with(
                    id=17, body={"action": "updateowner", "userid": 9, "team": 4}
                )
                get.assert_called_once_with(17)
                self.assertEqual(entity.owner_id, 9)
                if resource:
                    api.experiments.patch_experiment.assert_not_called()
                else:
                    api.items.patch_item.assert_not_called()

    def test_explicit_destination_team_does_not_resolve_current_team(self):
        entity, api, _, patch, _ = self.entity()
        entity.transfer_ownership(9, team_id=12)
        self.assertEqual(patch.call_args.kwargs["body"]["team"], 12)
        api._get_team_id.assert_not_called()

    def test_invalid_ids_never_send_a_transfer(self):
        for value in (None, True, False, 0, -1, 9.0, "9", "Patange"):
            for field in ("user_id", "team_id"):
                with self.subTest(field=field, value=value):
                    if field == "team_id" and value is None:
                        continue  # None intentionally selects the current team.
                    entity, _, _, patch, get = self.entity()
                    with self.assertRaises(ValueError):
                        if field == "user_id":
                            entity.transfer_ownership(value)
                        else:
                            entity.transfer_ownership(9, team_id=value)
                    patch.assert_not_called()
                    get.assert_not_called()
                    self.assertEqual(entity.owner_id, 1)

    def test_missing_record_id_never_sends_a_request(self):
        entity, _, _, patch, _ = self.entity()
        entity.ID = None
        with self.assertRaises(DeletedExperiment):
            entity.transfer_ownership(9)
        patch.assert_not_called()

    def test_server_rejection_is_preserved_and_not_retried(self):
        entity, _, _, patch, get = self.entity()
        error = ApiException(status=403, reason="Forbidden")
        patch.side_effect = error
        with self.assertRaises(ApiException) as caught:
            entity.transfer_ownership(9)
        self.assertIs(caught.exception, error)
        self.assertIsNone(entity._cache)
        self.assertEqual(patch.call_count, 1)
        get.assert_not_called()

    def test_lost_read_access_reports_possible_success_and_retains_id(self):
        entity, _, row, patch, get = self.entity()
        get.side_effect = ApiException(status=403, reason="Forbidden")
        with self.assertRaises(OwnershipTransferError) as caught:
            entity.transfer_ownership(9)
        self.assertEqual(row["userid"], 9)
        self.assertEqual(caught.exception.entity_id, 17)
        self.assertEqual(caught.exception.entity_type, "experiments")
        self.assertIn("server accepted", str(caught.exception))
        self.assertIsNone(entity._cache)
        self.assertEqual(patch.call_count, 1)

    def test_wrong_or_missing_owner_or_team_is_not_reported_as_success(self):
        for row in ({"userid": 1, "team": 4}, {"userid": 9, "team": 3},
                    {"userid": 9}, {"team": 4}):
            with self.subTest(row=row):
                entity, _, _, patch, get = self.entity()
                get.side_effect = lambda id: SimpleNamespace(to_dict=lambda: row)
                with self.assertRaises(OwnershipTransferError):
                    entity.transfer_ownership(9)
                self.assertIsNone(entity._cache)
                self.assertEqual(patch.call_count, 1)


class CreationOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.client = Client.__new__(Client)
        self.client.enforce_unique_titles = False
        self.client._team_id = 4
        self.client.experiments = Mock()
        self.endpoint = self.client.experiments
        self.endpoint.post_experiment_with_http_info.return_value = (
            None, None, {"Location": "https://lab.example/api/v2/experiments/17"}
        )
        self.client.categories = Mock()
        self.client.categories.read_team_experiments_categories.return_value = [
            SimpleNamespace(title="Physics", id=3)
        ]
        self.client.templates = Mock()
        self.client.templates.read_experiments_templates.return_value = [
            SimpleNamespace(title="Protocol", id=7)
        ]
        self.row = {"userid": 1, "team": 4, "title": "New"}
        self.calls = []

        def patch(*, id, body):
            self.calls.append(dict(body))
            self.row.update({k: v for k, v in body.items() if k != "action"})

        self.endpoint.patch_experiment.side_effect = patch
        self.endpoint.get_experiment.side_effect = lambda id: SimpleNamespace(
            to_dict=lambda: dict(self.row)
        )

    def test_plain_creation_sets_category_before_transferring(self):
        experiment = self.client.create_experiment("New", "Physics", owner_id=9)
        self.assertEqual(experiment.owner_id, 9)
        self.assertEqual(self.calls, [
            {"category": 3}, {"action": "updateowner", "userid": 9, "team": 4},
        ])
        self.endpoint.post_experiment_with_http_info.assert_called_once_with(
            body={"title": "New"}
        )

    def test_template_sets_title_and_category_before_transferring(self):
        experiment = self.client.create_experiment_from_template(
            "New", "Protocol", "Physics", owner_id=9, team_id=12
        )
        self.assertEqual(experiment.owner_id, 9)
        self.assertEqual(self.calls, [
            {"title": "New", "category": 3},
            {"action": "updateowner", "userid": 9, "team": 12},
        ])
        self.endpoint.post_experiment_with_http_info.assert_called_once_with(
            body={"template": 7}
        )

    def test_omitting_owner_preserves_existing_creation_behavior(self):
        self.client.create_experiment("New")
        self.endpoint.patch_experiment.assert_not_called()
        self.client.create_experiment_from_template("New", "Protocol")
        self.assertEqual(self.calls, [{"title": "New"}])

    def test_invalid_owner_or_team_is_rejected_before_creating(self):
        for kwargs in ({"owner_id": 0}, {"owner_id": True}, {"owner_id": "9"},
                       {"owner_id": -1}, {"owner_id": 9, "team_id": False},
                       {"owner_id": 9, "team_id": 0}, {"team_id": 4}):
            for template in (False, True):
                with self.subTest(kwargs=kwargs, template=template):
                    with self.assertRaises(ValueError):
                        if template:
                            self.client.create_experiment_from_template(
                                "New", "Protocol", **kwargs
                            )
                        else:
                            self.client.create_experiment("New", **kwargs)
        self.endpoint.post_experiment_with_http_info.assert_not_called()
        self.endpoint.patch_experiment.assert_not_called()

    def test_failed_transfer_reports_created_id_without_retry_or_delete(self):
        for status in (403, 422):
            with self.subTest(status=status):
                self.endpoint.reset_mock()
                self.endpoint.patch_experiment.side_effect = ApiException(status=status)
                with self.assertRaises(OwnershipTransferError) as caught:
                    self.client.create_experiment("New", owner_id=9)
                self.assertEqual(caught.exception.entity_id, 17)
                self.assertEqual(caught.exception.__cause__.status, status)
                self.assertIn("was created", str(caught.exception))
                self.endpoint.post_experiment_with_http_info.assert_called_once()
                self.endpoint.patch_experiment.assert_called_once()
                self.endpoint.delete_experiment.assert_not_called()

    def test_transfer_verification_failure_on_creation_reports_created_id(self):
        self.endpoint.get_experiment.side_effect = ApiException(status=403)
        with self.assertRaises(OwnershipTransferError) as caught:
            self.client.create_experiment("New", owner_id=9)
        self.assertEqual(caught.exception.entity_id, 17)
        self.assertEqual(self.row["userid"], 9)
        self.endpoint.post_experiment_with_http_info.assert_called_once()
        self.endpoint.patch_experiment.assert_called_once()


if __name__ == "__main__":
    unittest.main()
