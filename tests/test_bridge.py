"""Offline checks of the current checkpoint-to-experiment integration engine."""

import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from elablink import Bridge, DuplicateTitle


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.acquisition = SimpleNamespace(experiment_name="Sinusoid")
        self.experiment = Mock()
        self.client = Mock()
        self.client.create_experiment.return_value = self.experiment
        self.bridge = Bridge(self.client)

    def test_checkpoint_creates_an_experiment_and_uploads_explicit_attachments(self):
        paths = (Path("run.h5"), Path("run_FIG1.png"))
        result = self.bridge.save_experiment(
            self.acquisition, attachments=iter(paths), metadata={"sample": "A"}
        )
        self.assertIs(result, self.experiment)
        self.client.create_experiment.assert_called_once_with(title="Sinusoid")
        self.assertEqual(self.experiment.metadata, {"sample": "A"})
        self.assertEqual(
            [call.args[0] for call in self.experiment.upload_file.call_args_list],
            [str(path) for path in paths],
        )

    def test_repeated_uploads_use_the_experiment_supplied_by_the_async_worker(self):
        for path in ("first.h5", "second.h5"):
            result = self.bridge.save_experiment(
                self.acquisition, attachments=(path,), experiment=self.experiment
            )
            self.assertIs(result, self.experiment)
        self.client.create_experiment.assert_not_called()
        self.client.load_experiment.assert_not_called()
        self.assertEqual(self.experiment.upload_file.call_count, 2)

    def test_duplicate_title_loads_the_existing_experiment(self):
        self.client.create_experiment.side_effect = DuplicateTitle("Sinusoid")
        self.client.load_experiment.return_value = self.experiment
        result = self.bridge.save_experiment(self.acquisition)
        self.assertIs(result, self.experiment)
        self.client.load_experiment.assert_called_once_with(title="Sinusoid")

    def test_custom_payload_and_resolver_are_used(self):
        payload = {
            "title": "Custom title",
            "body": "A sine measurement",
            "metadata": {},
            "category": 4,
            "status": 2,
            "tags": ["optics"],
            "steps": ["Align the laser"],
            "comments": ["Stable signal"],
        }
        builder = Mock(return_value=payload)
        resolver = Mock(return_value=self.experiment)
        bridge = Bridge(
            self.client, payload_builder=builder, experiment_resolver=resolver
        )
        metadata = {"sample": "A"}
        result = bridge.save_experiment(
            self.acquisition, attachments=(Path("run.h5"),), metadata=metadata
        )
        self.assertIs(result, self.experiment)
        builder.assert_called_once_with(self.acquisition, ("run.h5",), metadata)
        resolver.assert_called_once_with(self.acquisition, payload)
        self.client.create_experiment.assert_not_called()
        self.assertEqual(self.experiment.main_text, payload["body"])
        self.assertEqual(self.experiment.metadata, {})
        self.assertEqual(self.experiment.category, 4)
        self.assertEqual(self.experiment.status, 2)
        self.experiment.add_tag.assert_called_once_with("optics")
        self.experiment.add_step.assert_called_once_with("Align the laser")
        self.experiment.add_comment.assert_called_once_with("Stable signal")
        self.experiment.upload_file.assert_called_once_with("run.h5")

    def test_resolver_can_defer_to_normal_creation(self):
        resolver = Mock(return_value=None)
        bridge = Bridge(self.client, experiment_resolver=resolver)
        self.assertIs(bridge.save_experiment(self.acquisition), self.experiment)
        self.client.create_experiment.assert_called_once_with(title="Sinusoid")

    def test_missing_client_or_title_reports_a_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "No eLabFTW client"):
            Bridge(None).save_experiment(self.acquisition)
        with self.assertRaisesRegex(ValueError, "experiment title"):
            self.bridge.save_experiment(SimpleNamespace())
        self.client.create_experiment.assert_not_called()

    def test_server_failures_propagate_instead_of_being_hidden(self):
        error = ConnectionError("Server is unavailable")
        self.client.create_experiment.side_effect = error
        with self.assertRaises(ConnectionError) as raised:
            self.bridge.save_experiment(self.acquisition)
        self.assertIs(raised.exception, error)
        self.client.load_experiment.assert_not_called()

    def test_only_the_current_checkpoint_api_is_present(self):
        self.assertEqual(Bridge.__bases__, (object,))
        self.assertNotIn(
            "AcquisitionBackend", vars(importlib.import_module("elablink.bridge"))
        )
        for name in (
            "save_snapshot", "load_snapshot", "ensure_local_file",
            "get_experiment", "get_experiment_from_analysis",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(self.bridge, name))
        original = vars(self.acquisition).copy()
        self.bridge.save_experiment(self.acquisition)
        self.assertEqual(vars(self.acquisition), original)
        self.assertEqual(
            set(vars(self.bridge)),
            {"_client", "_payload_builder", "_experiment_resolver"},
        )


if __name__ == "__main__":
    unittest.main()
