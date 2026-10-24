"""Offline regressions for the eLabmate API cleanup."""

import importlib
import json
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import elabmate
from elabmate import (
    DuplicateTitle,
    ElabBridge,
    ElabClient,
    ElabExperiment,
    ElabResource,
    InvalidCategory,
    InvalidTemplate,
)


class ClientCreationTests(unittest.TestCase):
    def setUp(self):
        # Build the wrapper without reading a configuration or opening a connection.
        self.client = object.__new__(ElabClient)
        self.client.enforce_unique_titles = True
        self.client._team_id = 4
        self.client.experiments = Mock()
        self.client.experiments.read_experiments.return_value = []
        self.client.experiments.post_experiment_with_http_info.return_value = (
            None,
            None,
            {"Location": "https://lab.example/api/v2/experiments/17"},
        )
        self.client.experiments.get_experiment.return_value.to_dict.return_value = {
            "title": "Measurement",
            "metadata": "{}",
        }
        self.client.categories = Mock()
        self.client.categories.read_team_experiments_categories.return_value = [
            SimpleNamespace(title="Physics", id=3)
        ]
        self.client.templates = Mock()
        self.client.templates.read_experiments_templates.return_value = [
            SimpleNamespace(title="Protocol", id=7)
        ]

    def test_template_accepts_positional_or_keyword_arguments(self):
        calls = [
            (("Measurement", "Protocol"), {}),
            ((), {"title": "Measurement", "template_name": "Protocol"}),
            (("Measurement",), {"template_name": "Protocol"}),
        ]
        for args, kwargs in calls:
            with self.subTest(args=args, kwargs=kwargs):
                self.client.experiments.reset_mock()
                experiment = self.client.create_experiment_from_template(
                    *args, **kwargs
                )
                self.assertIsInstance(experiment, ElabExperiment)
                self.assertEqual(experiment.ID, 17)
                self.client.experiments.post_experiment_with_http_info.assert_called_once_with(
                    body={"template": 7}
                )
                self.client.experiments.patch_experiment.assert_called_once_with(
                    17, body={"title": "Measurement"}
                )

    def test_template_category_accepts_positional_keyword_name_or_id(self):
        calls = [
            (("Measurement", "Protocol", "Physics"), {}),
            (("Measurement", "Protocol", 3), {}),
            (("Measurement", "Protocol"), {"category": "Physics"}),
            (
                (),
                {"title": "Measurement", "template_name": "Protocol", "category": 3},
            ),
        ]
        for args, kwargs in calls:
            with self.subTest(args=args, kwargs=kwargs):
                self.client.experiments.reset_mock()
                self.client.create_experiment_from_template(*args, **kwargs)
                self.client.experiments.patch_experiment.assert_called_once_with(
                    17, body={"title": "Measurement", "category": 3}
                )

    def test_plain_creation_keeps_positional_and_keyword_categories(self):
        calls = [
            (("Measurement", "Physics"), {}),
            (("Measurement",), {"category": 3}),
            ((), {"title": "Measurement", "category": "Physics"}),
        ]
        for args, kwargs in calls:
            with self.subTest(args=args, kwargs=kwargs):
                self.client.experiments.reset_mock()
                experiment = self.client.create_experiment(*args, **kwargs)
                self.assertEqual(experiment.ID, 17)
                self.client.experiments.post_experiment_with_http_info.assert_called_once_with(
                    body={"title": "Measurement"}
                )
                self.client.experiments.patch_experiment.assert_called_once_with(
                    17, body={"category": 3}
                )

    def test_duplicate_title_is_rejected_before_creating_an_experiment(self):
        self.client.experiments.read_experiments.return_value = [
            SimpleNamespace(title="Measurement", id=12)
        ]
        for method, args in (
            (self.client.create_experiment, ("Measurement",)),
            (self.client.create_experiment_from_template, ("Measurement", "Protocol")),
        ):
            with self.subTest(method=method.__name__):
                with self.assertRaises(DuplicateTitle):
                    method(*args)
        self.client.experiments.post_experiment_with_http_info.assert_not_called()

    def test_disabled_unique_titles_do_not_list_existing_experiments(self):
        self.client.enforce_unique_titles = False
        self.client.experiments.read_experiments.side_effect = AssertionError(
            "Title listing should not be needed when uniqueness is disabled"
        )
        self.client.create_experiment("Measurement")
        self.client.create_experiment_from_template("Measurement", "Protocol")
        self.client.experiments.read_experiments.assert_not_called()
        self.assertEqual(
            self.client.experiments.post_experiment_with_http_info.call_count, 2
        )

    def test_missing_template_does_not_create_an_experiment(self):
        with self.assertRaises(InvalidTemplate):
            self.client.create_experiment_from_template("Measurement", "Missing")
        self.client.experiments.post_experiment_with_http_info.assert_not_called()

    def test_invalid_category_does_not_create_an_experiment(self):
        with self.assertRaises(InvalidCategory):
            self.client.create_experiment("Measurement", category="Missing")
        self.client.experiments.post_experiment_with_http_info.assert_not_called()

    def test_missing_template_argument_keeps_normal_python_type_error(self):
        with self.assertRaises(TypeError):
            self.client.create_experiment_from_template("Measurement")
        self.client.experiments.post_experiment_with_http_info.assert_not_called()


class MetadataValidationTests(unittest.TestCase):
    def entity(self, entity_class=ElabExperiment):
        api = Mock()
        entity = entity_class(api, 17)
        entity._cache = {"metadata": None}
        return entity, api

    def test_missing_empty_and_json_object_metadata_are_supported(self):
        values = [(None, {}), ("", {}), ({}, {}), ('{"sample": "A"}', {"sample": "A"})]
        for entity_class in (ElabExperiment, ElabResource):
            for raw, expected in values:
                with self.subTest(entity=entity_class.__name__, raw=raw):
                    entity, _ = self.entity(entity_class)
                    entity._cache["metadata"] = raw
                    self.assertEqual(entity.metadata, expected)

    def test_metadata_dictionary_is_not_returned_as_the_cached_object(self):
        for entity_class in (ElabExperiment, ElabResource):
            with self.subTest(entity=entity_class.__name__):
                entity, _ = self.entity(entity_class)
                entity._cache["metadata"] = {"sample": "A"}
                metadata = entity.metadata
                metadata["sample"] = "B"
                self.assertEqual(entity._cache["metadata"], {"sample": "A"})

    def test_malformed_or_non_object_server_metadata_raises(self):
        invalid = ["{broken", "[]", "null", "42", '"text"', [], 42]
        for entity_class in (ElabExperiment, ElabResource):
            for raw in invalid:
                with self.subTest(entity=entity_class.__name__, raw=raw):
                    entity, _ = self.entity(entity_class)
                    entity._cache["metadata"] = raw
                    with self.assertRaises(ValueError):
                        _ = entity.metadata

    def test_metadata_setter_accepts_dictionary_and_json_object(self):
        for entity_class in (ElabExperiment, ElabResource):
            for value in ({"sample": "A"}, '{"sample": "A"}'):
                with self.subTest(entity=entity_class.__name__, value=value):
                    entity, api = self.entity(entity_class)
                    entity.metadata = value
                    endpoint = (
                        api.experiments.patch_experiment
                        if entity_class is ElabExperiment
                        else api.items.patch_item
                    )
                    endpoint.assert_called_once()
                    body = endpoint.call_args.kwargs["body"]
                    self.assertEqual(json.loads(body["metadata"]), {"sample": "A"})

    def test_invalid_metadata_is_rejected_before_any_server_write(self):
        for entity_class in (ElabExperiment, ElabResource):
            for value in ("{broken", "[]", "null", "42", '"text"'):
                with self.subTest(entity=entity_class.__name__, value=value):
                    entity, api = self.entity(entity_class)
                    with self.assertRaises(ValueError):
                        entity.metadata = value
                    api.experiments.patch_experiment.assert_not_called()
                    api.items.patch_item.assert_not_called()

    def test_extra_fields_reject_bad_metadata_without_overwriting_it(self):
        entity, api = self.entity()
        entity._cache["metadata"] = "[]"
        with self.assertRaises(ValueError):
            entity.set_extra_fields({"Temperature": 4})
        api.experiments.patch_experiment.assert_not_called()
        self.assertEqual(entity._cache["metadata"], "[]")


class CleanupContractTests(unittest.TestCase):
    def test_tag_removal_is_not_part_of_either_entity_api(self):
        for entity_class in (ElabExperiment, ElabResource):
            with self.subTest(entity=entity_class.__name__):
                self.assertFalse(hasattr(entity_class, "remove_tag"))
                self.assertFalse(hasattr(entity_class, "clear_tags"))
        self.assertFalse(hasattr(elabmate, "InvalidTag"))
        self.assertNotIn("InvalidTag", elabmate.__all__)
        self.assertFalse(
            hasattr(importlib.import_module("elabmate.exceptions"), "InvalidTag")
        )

    def test_bridge_payload_tags_preserve_existing_tags(self):
        tags = ["existing"]
        experiment = SimpleNamespace(
            add_tag=lambda tag: tags.append(tag),
            clear_tags=Mock(side_effect=AssertionError("Tags must never be cleared")),
        )
        bridge = ElabBridge(
            None,
            payload_builder=lambda acquisition, attachments, metadata: {
                "title": acquisition.experiment_name,
                "tags": ["new"],
            },
        )
        acquisition = SimpleNamespace(experiment_name="Measurement")
        result = bridge.save_experiment(acquisition, experiment=experiment)
        self.assertIs(result, experiment)
        self.assertEqual(tags, ["existing", "new"])
        experiment.clear_tags.assert_not_called()

    def test_bridge_applies_metadata_including_an_explicit_empty_object(self):
        for metadata in ({"sample": "A"}, {}):
            with self.subTest(metadata=metadata):
                experiment = SimpleNamespace(metadata={"old": True})
                bridge = ElabBridge(None)
                acquisition = SimpleNamespace(experiment_name="Measurement")
                bridge.save_experiment(
                    acquisition, metadata=metadata, experiment=experiment
                )
                self.assertEqual(experiment.metadata, metadata)

    def test_unused_private_id_lookup_is_removed(self):
        self.assertFalse(hasattr(ElabClient, "_has_ID"))

    def test_importing_client_does_not_silence_process_wide_tls_warnings(self):
        # A fresh process avoids mutating module identities in the rest of the suite.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from unittest.mock import patch\n"
                "with patch('urllib3.disable_warnings') as disable_warnings:\n"
                "    import elabmate.client\n"
                "    disable_warnings.assert_not_called()\n",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
