"""Check upload timeouts through the generated SDK and its real REST adapter."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import urllib3

from elablink import Client
from elablink._attachment_store import AttachmentStore


class AttachmentUploadTimeoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        configuration = self.directory / "elablink.conf"
        configuration.write_text(
            "API_HOST_URL=https://offline.invalid/api/v2\n"
            "API_KEY=offline-test-key\n"
            "VERIFY_SSL=true\n"
            "TEAM_ID=4\n"
            "UNIQUE_EXPERIMENTS_TITLES=false\n",
            encoding="utf-8",
        )
        self.client = Client(configuration)
        self.rows = []
        self.requests = []
        transport = patch.object(
            self.client.api_client.rest_client.pool_manager, "request", side_effect=self.respond
        )
        transport.start()
        self.addCleanup(transport.stop)
        self.addCleanup(self.client.api_client.rest_client.pool_manager.clear)
        self.store = AttachmentStore(
            configuration, "experiments", 17, self.directory / "cache", lambda: self.client
        )
        self.source = self.directory / "measurement.h5"
        self.source.write_bytes(b"synthetic upload contents")

    def respond(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if method == "GET":
            payload = self.rows if url.endswith("/uploads") else {"id": 17, "title": "Timeout test"}
            status = 200
        elif method == "POST" and "/uploads" in url:
            fields = dict(kwargs["fields"])
            name, contents, content_type = fields["file"]
            self.assertEqual(contents, self.source.read_bytes())
            identifier = max((row["id"] for row in self.rows), default=0) + 1
            self.rows = [{"id": identifier, "real_name": name, "filesize": len(contents)}]
            payload, status = {}, 201
        else:
            raise AssertionError(f"Unexpected request: {method} {url}")
        return urllib3.HTTPResponse(
            body=json.dumps(payload).encode("utf-8"), status=status,
            headers={"Content-Type": "application/json"}, preload_content=True,
        )

    def assert_timeout(self, request, expected):
        timeout = request[2]["timeout"]
        self.assertIsInstance(timeout, urllib3.Timeout)
        self.assertEqual((timeout.connect_timeout, timeout.read_timeout), expected)

    def test_new_upload_has_long_send_and_read_timeout_but_metadata_stays_short(self):
        rows = self.store.upload([self.source])
        self.assertEqual(len(rows), 1)
        uploads = [request for request in self.requests if request[0] == "POST"]
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0][1].endswith("/experiments/17/uploads"))
        self.assertTrue(uploads[0][2]["encode_multipart"])
        self.assert_timeout(uploads[0], (300, 300))
        metadata = [request for request in self.requests if request[0] == "GET"]
        self.assertTrue(metadata)
        for request in metadata:
            self.assert_timeout(request, (5, 30))

    def test_replacement_upload_uses_long_timeout_and_exact_attachment(self):
        self.rows = [{"id": 9, "real_name": self.source.name, "filesize": 1}]
        rows = self.store.upload([self.source], replace=True)
        self.assertEqual(rows[0]["id"], 10)
        uploads = [request for request in self.requests if request[0] == "POST"]
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0][1].endswith("/experiments/17/uploads/9"))
        self.assertEqual(dict(uploads[0][2]["fields"])["action"], "replace")
        self.assert_timeout(uploads[0], (300, 300))

    def test_explicit_timeout_is_preserved_for_metadata_and_multipart_upload(self):
        self.store.list_files()
        self.client.uploads.read_uploads("experiments", 17, _request_timeout=(2, 10))
        self.assert_timeout(self.requests[-1], (2, 10))
        self.client.uploads.post_upload(
            "experiments", 17, file=str(self.source), _request_timeout=(3, 20)
        )
        self.assert_timeout(self.requests[-1], (3, 20))


if __name__ == "__main__":
    unittest.main()
