"""Exercise background upload timeouts through the actual generated SDK."""

from contextlib import suppress
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import urllib3

from elablink import AsyncBridge, Client


class AsyncUploadTimeoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.configuration = self.root / "elablink.conf"
        self.configuration.write_text(
            "API_HOST_URL=https://offline.invalid/api/v2\n"
            "API_KEY=offline-test-key\nVERIFY_SSL=true\nTEAM_ID=4\n"
            "UNIQUE_EXPERIMENTS_TITLES=false\n", encoding="utf-8",
        )
        self.client = Client(self.configuration)
        self.rows, self.requests = [], []
        self.source = self.root / "measurement.h5"
        self.source.write_bytes(b"synthetic upload contents")
        transport = patch.object(self.client.api_client.rest_client.pool_manager, "request", side_effect=self.respond)
        transport.start()
        self.addCleanup(transport.stop)
        self.addCleanup(self.client.api_client.rest_client.pool_manager.clear)
        self.bridges = []
        self.addCleanup(self.close_bridges)

    def close_bridges(self):
        for bridge in self.bridges:
            with suppress(RuntimeError):
                bridge.close(timeout=5)

    def respond(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if method == "GET":
            payload = self.rows if url.endswith("/uploads") else {"id": 17, "title": "Timeout test"}
            status = 200
        elif method == "POST" and "/uploads" in url:
            fields = dict(kwargs["fields"])
            name, contents, _ = fields["file"]
            self.assertEqual(contents, self.source.read_bytes())
            identifier = max((row["id"] for row in self.rows), default=0) + 1
            self.rows = [{"id": identifier, "real_name": name, "filesize": len(contents)}]
            payload, status = {}, 201
        else:
            raise AssertionError(f"Unexpected request: {method} {url}")
        return urllib3.HTTPResponse(body=json.dumps(payload).encode(), status=status,
                                    headers={"Content-Type": "application/json"}, preload_content=True)

    def upload(self, **kwargs):
        bridge = AsyncBridge("Timeout test", experiment_id=17, config_path=self.configuration,
                             spool_directory=self.root / "spool", **kwargs)
        self.bridges.append(bridge)
        with patch("elablink.async_bridge.Client", return_value=self.client):
            bridge.enqueue(SimpleNamespace(filepath=self.source))
            bridge.flush(timeout=5)
        return bridge

    def assert_timeout(self, request, expected):
        timeout = request[2]["timeout"]
        self.assertIsInstance(timeout, urllib3.Timeout)
        self.assertEqual((timeout.connect_timeout, timeout.read_timeout), expected)

    def test_background_upload_has_long_timeout_while_metadata_stays_short(self):
        self.upload()
        uploads = [request for request in self.requests if request[0] == "POST"]
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0][1].endswith("/experiments/17/uploads"))
        self.assertTrue(uploads[0][2]["encode_multipart"])
        self.assert_timeout(uploads[0], (300, 300))
        reads = [request for request in self.requests if request[0] == "GET"]
        self.assertTrue(reads)
        for request in reads:
            self.assert_timeout(request, (5, 30))

    def test_background_replacement_uses_configured_upload_timeout(self):
        self.rows = [{"id": 9, "real_name": self.source.name, "filesize": 1}]
        self.upload(request_timeout=(2, 10), upload_timeout=(45, 90))
        uploads = [request for request in self.requests if request[0] == "POST"]
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0][1].endswith("/experiments/17/uploads/9"))
        self.assertEqual(dict(uploads[0][2]["fields"])["action"], "replace")
        self.assert_timeout(uploads[0], (45, 90))
        for request in self.requests:
            if request[0] == "GET":
                self.assert_timeout(request, (2, 10))

    def test_explicit_sdk_request_timeouts_take_precedence_over_bridge_defaults(self):
        self.upload()
        self.client.uploads.read_uploads("experiments", 17, _request_timeout=(2, 10))
        self.assert_timeout(self.requests[-1], (2, 10))
        self.client.uploads.post_upload("experiments", 17, file=str(self.source), _request_timeout=(3, 20))
        self.assert_timeout(self.requests[-1], (3, 20))

    def test_invalid_upload_timeouts_are_rejected_before_starting_work(self):
        for timeout in ((0, 30), (5, -1), (30,)):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "Upload.*positive"):
                AsyncBridge(config_path=self.configuration, spool_directory=self.root / "spool",
                            upload_timeout=timeout)
        self.assertFalse((self.root / "spool").exists())
        self.assertEqual(self.requests, [])

    def test_acquisition_manager_forwards_the_upload_timeout_to_its_background_worker(self):
        try:
            from elablink import AcquisitionAnalysisManager
        except ImportError:
            self.skipTest("Optional Labmate is not installed")
        with patch("elablink.async_bridge.Client", return_value=self.client):
            with AcquisitionAnalysisManager(str(self.root / "data"), experiment_id=17,
                                            config_path=self.configuration, shell=None, use_magic=False,
                                            request_timeout=(2, 15), upload_timeout=(80, 140)) as manager:
                manager.acquisition_cell("Timeout test", cell="none")
                self.source = Path(str(manager.current_filepath) + ".h5")
                manager.save_acquisition(x=[1, 2, 3])
                manager.flush(timeout=5)
        uploads = [request for request in self.requests if request[0] == "POST"]
        self.assertEqual(len(uploads), 1)
        self.assert_timeout(uploads[0], (80, 140))
        for request in self.requests:
            if request[0] == "GET":
                self.assert_timeout(request, (2, 15))
