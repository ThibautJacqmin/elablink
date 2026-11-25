"""Reject title ambiguity before choosing or editing an experiment or resource."""

from contextlib import suppress
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from elablink import AmbiguousTitle, AsyncBridge, Bridge, Client, DuplicateTitle, InvalidTitle


class AmbiguousTitleTests(unittest.TestCase):
    def client(self):
        client = Client.__new__(Client)
        client.enforce_unique_titles = True
        client.configuration = SimpleNamespace(host="https://offline.invalid/api/v2")
        client.experiments = Mock()
        client.items = Mock()
        client.uploads = Mock()
        records = [SimpleNamespace(title="Calibration", id=11),
                   SimpleNamespace(title="Calibration", id=22),
                   SimpleNamespace(title="Other experiment", id=33)]
        client.experiments.read_experiments.return_value = records
        client.items.read_items.return_value = records
        client.experiments.get_experiment.side_effect = lambda ID: SimpleNamespace(
            to_dict=lambda: {"id": ID, "title": "Calibration", "metadata": "{}"})
        client.items.get_item.side_effect = client.experiments.get_experiment.side_effect
        client.experiments.post_experiment_with_http_info.return_value = (
            None, None, {"Location": "https://offline.invalid/api/v2/experiments/44"})
        return client, records

    def test_duplicate_title_lookup_reports_matching_ids_regardless_of_order(self):
        for kind in ("experiment", "resource"):
            for reverse in (False, True):
                with self.subTest(kind=kind, reverse=reverse):
                    client, records = self.client()
                    if reverse:
                        records.reverse()
                    loader = getattr(client, f"load_{kind}")
                    with self.assertRaises(AmbiguousTitle) as raised:
                        loader(title="Calibration")
                    self.assertEqual(raised.exception.ids, (11, 22))
                    self.assertIn(kind, str(raised.exception))
                    self.assertIn("Calibration", str(raised.exception))
                    self.assertIn("11, 22", str(raised.exception))
                    self.assertIn("ID=<number>", str(raised.exception))
                    client.experiments.get_experiment.assert_not_called()
                    client.items.get_item.assert_not_called()

    def test_unique_requested_title_works_amid_unrelated_duplicates(self):
        client, _ = self.client()
        self.assertEqual(client.load_experiment(title="Other experiment").ID, 33)
        self.assertEqual(client.load_resource(title="Other experiment").ID, 33)

    def test_explicit_id_bypasses_ambiguous_title_listing(self):
        client, _ = self.client()
        self.assertEqual(client.load_experiment(ID=11).ID, 11)
        self.assertEqual(client.load_resource(ID=22).ID, 22)
        client.experiments.read_experiments.assert_not_called()
        client.items.read_items.assert_not_called()

    def test_title_dictionaries_do_not_silently_discard_records(self):
        for property_name in ("experiments_dict", "resources_dict"):
            with self.subTest(property_name=property_name):
                client, records = self.client()
                records.append(SimpleNamespace(title="Calibration", id=44))
                with self.assertRaises(AmbiguousTitle) as raised:
                    getattr(client, property_name)
                self.assertEqual(raised.exception.ids, (11, 22, 44))

    def test_repeated_identical_record_is_not_an_ambiguous_title(self):
        client, records = self.client()
        records[:] = [records[0], records[0]]
        self.assertEqual(client.experiments_dict, {"Calibration": 11})
        self.assertEqual(client.resources_dict, {"Calibration": 11})
        self.assertEqual(client.load_experiment(title="Calibration").ID, 11)

    def test_missing_title_is_not_confused_with_unrelated_ambiguity(self):
        client, _ = self.client()
        for loader in (client.load_experiment, client.load_resource):
            with self.subTest(loader=loader.__name__):
                with self.assertRaises(InvalidTitle):
                    loader(title="Missing")

    def test_uniqueness_check_still_allows_new_titles_with_unrelated_duplicates(self):
        client, _ = self.client()
        with self.assertRaises(DuplicateTitle):
            client.create_experiment("Calibration")
        client.experiments.post_experiment_with_http_info.assert_not_called()
        self.assertEqual(client.create_experiment("New measurement").ID, 44)
        client.experiments.post_experiment_with_http_info.assert_called_once_with(body={"title": "New measurement"})

    def test_bridge_refuses_ambiguous_destination_before_any_remote_write(self):
        client, _ = self.client()
        bridge = Bridge(client)
        with self.assertRaises(AmbiguousTitle):
            bridge.save_experiment(SimpleNamespace(experiment_name="Calibration"),
                                   attachments=["run.h5"], metadata={"sample": "A"})
        client.experiments.post_experiment_with_http_info.assert_not_called()
        client.experiments.get_experiment.assert_not_called()
        client.experiments.patch_experiment.assert_not_called()
        client.uploads.post_upload.assert_not_called()

    def test_async_failure_keeps_local_checkpoint_and_reports_available_ids(self):
        client, _ = self.client()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "run.h5"
            original.write_bytes(b"saved measurement")
            bridge = AsyncBridge("Calibration", spool_directory=root / "spool", client_factory=lambda: client)
            try:
                bridge.enqueue(SimpleNamespace(filepath=original))
                with self.assertRaisesRegex(RuntimeError, "IDs: 11, 22"):
                    bridge.flush(timeout=3)
                self.assertEqual(bridge.status()[0]["state"], "failed")
                self.assertEqual(original.read_bytes(), b"saved measurement")
                self.assertTrue(list((root / "spool").rglob("*.h5")))
                client.experiments.post_experiment_with_http_info.assert_not_called()
                client.experiments.patch_experiment.assert_not_called()
                client.uploads.post_upload.assert_not_called()
            finally:
                with suppress(RuntimeError):
                    bridge.close(timeout=3)
