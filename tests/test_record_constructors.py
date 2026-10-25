"""Short public constructors use the same creation/loading paths as Client."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from elablink import Client, Experiment, Resource


class RecordConstructorTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(spec=Client)
        self.record = SimpleNamespace(ID=17, _cache={"title": "Measurement"})
        self.client.create_experiment.return_value = self.record
        self.client.create_resource.return_value = self.record
        self.client.load_experiment.return_value = self.record
        self.client.load_resource.return_value = self.record

    def test_default_constructors_create_the_matching_entity(self):
        for entity_class, method, title in (
            (Experiment, "create_experiment", "Untitled experiment"),
            (Resource, "create_resource", "Untitled resource"),
        ):
            with self.subTest(entity=entity_class.__name__):
                with patch("elablink.client.Client", return_value=self.client) as factory:
                    entity = entity_class()
                factory.assert_called_once_with("elablink.conf")
                getattr(self.client, method).assert_called_once_with(title=title, category=None)
                self.assertIs(entity.api, self.client)
                self.assertEqual(entity.ID, 17)
                self.assertEqual(entity._cache, self.record._cache)

    def test_custom_configuration_and_title(self):
        with patch("elablink.client.Client", return_value=self.client) as factory:
            Experiment(title="Sinusoid", category="Physics", config_path="other.conf")
        factory.assert_called_once_with("other.conf")
        self.client.create_experiment.assert_called_once_with(title="Sinusoid", category="Physics")

    def test_resource_can_share_a_client_and_numeric_category(self):
        entity = Resource(api=self.client, title="Laser", category=3)
        self.client.create_resource.assert_called_once_with(title="Laser", category=3)
        self.client.create_experiment.assert_not_called()
        self.assertEqual(entity.ID, 17)

    def test_open_existing_entity_validates_through_client(self):
        for entity_class, method in ((Experiment, "load_experiment"), (Resource, "load_resource")):
            with self.subTest(entity=entity_class.__name__):
                with patch("elablink.client.Client", return_value=self.client):
                    entity = entity_class(ID=17)
                getattr(self.client, method).assert_called_once_with(ID=17)
                self.assertEqual(entity.ID, 17)
                self.client.create_experiment.assert_not_called()
                self.client.create_resource.assert_not_called()

    def test_internal_client_binding_does_not_recurse_or_contact_server(self):
        for entity_class in (Experiment, Resource):
            entity = entity_class(self.client, ID=17)
            self.assertEqual(entity.ID, 17)
        self.assertEqual(self.client.mock_calls, [])

    def test_ambiguous_create_and_load_arguments_fail_before_connecting(self):
        with patch("elablink.client.Client") as factory:
            for entity_class in (Experiment, Resource):
                with self.assertRaises(ValueError):
                    entity_class(ID=17, title="New title")
                with self.assertRaises(ValueError):
                    entity_class(ID=17, category=3)
            factory.assert_not_called()

    def test_client_errors_propagate_without_creating_an_unbound_record(self):
        self.client.create_experiment.side_effect = RuntimeError("Offline")
        with patch("elablink.client.Client", return_value=self.client):
            with self.assertRaisesRegex(RuntimeError, "Offline"):
                Experiment(title="Measurement")


if __name__ == "__main__":
    unittest.main()
