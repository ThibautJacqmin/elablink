"""Check browser links against eLabFTW's ordinary page/download URL formats."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from elablink._attachment_store import AttachmentStore


class AttachmentLinkTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"id": 8, "real_name": "signal & \u03c0.csv", "long_name": "ab/cd blob", "storage": 2}]
        self.client = SimpleNamespace(
            configuration=SimpleNamespace(host="https://offline.example/elab/api/v2/"),
            api_client=SimpleNamespace(call_api=Mock()),
            load_experiment=lambda **kwargs: SimpleNamespace(
                title="Offline record", refresh=lambda: None, list_files=lambda: list(self.rows),
            ),
        )
        self.client.load_resource = self.client.load_experiment

    def store(self, kind="experiments"):
        return AttachmentStore(None, kind, 17, client_factory=lambda: self.client)

    def test_experiment_and_resource_links_preserve_installation_subpath(self):
        self.assertEqual(self.store().record_link(), "https://offline.example/elab/experiments.php?mode=view&id=17")
        self.assertEqual(self.store("items").record_link(), "https://offline.example/elab/database.php?mode=view&id=17")

    def test_attachment_link_encodes_filename_storage_and_actual_server_path(self):
        link = self.store().attachment_links(self.rows)[0]
        address = urlsplit(link)
        self.assertEqual(address.path, "/elab/app/download.php")
        self.assertEqual(parse_qs(address.query), {"f": ["ab/cd blob"], "name": ["signal & \u03c0.csv"], "storage": ["2"]})
        self.assertNotIn("api_key", link)
        self.rows[0].pop("storage")
        link = self.store().attachment_links(self.rows)[0]
        self.assertEqual(parse_qs(urlsplit(link).query)["storage"], ["1"])

    def test_deleted_or_incomplete_attachment_has_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            self.store().attachment_links([{"id": 99}])
        self.rows[0].pop("long_name")
        with self.assertRaisesRegex(ValueError, "download link"):
            self.store().attachment_links(self.rows)
