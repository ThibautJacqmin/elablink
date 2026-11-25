"""Offline regressions for TLS, safe errors, snapshot capture and streamed files."""

from contextlib import suppress
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import traceback
import unittest
from unittest.mock import patch

from elabapi_python.rest import ApiException

from elablink import AsyncBridge, Client, Experiment
from elablink.exceptions import safe_error_message
from tests.fakes import OfflineClient


class SecurityCoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()

    def configure(self, *, verify="true", allow_http=None, url="https://offline.invalid/api/v2"):
        client = Client.__new__(Client)
        client.config = {"API_HOST_URL": url, "API_KEY": "offline-test-key",
                         "VERIFY_SSL": verify, "UNIQUE_EXPERIMENTS_TITLES": "true"}
        if allow_http is not None:
            client.config["ALLOW_INSECURE_HTTP"] = allow_http
        client._api_configuration()
        return client

    def test_certificate_verification_accepts_whitespace_and_rejects_typos(self):
        for value in ("true", " true ", "TRUE"):
            with self.subTest(value=value):
                self.assertTrue(self.configure(verify=value).configuration.verify_ssl)
        self.assertFalse(self.configure(verify=" false ").configuration.verify_ssl)
        for value in ("treu", "yes", "1", "", False):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "VERIFY_SSL must be true or false"):
                self.configure(verify=value)

    def test_plain_http_needs_a_valid_explicit_override(self):
        with self.assertRaisesRegex(ValueError, "ALLOW_INSECURE_HTTP=true"):
            self.configure(url="http://offline.invalid/api/v2")
        client = self.configure(url="http://offline.invalid/api/v2", allow_http=" true ")
        self.assertEqual(client.configuration.host, "http://offline.invalid/api/v2")
        for value in ("yes", "ture", "", True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "ALLOW_INSECURE_HTTP must be true or false"):
                self.configure(allow_http=value)
        for url in ("ftp://offline.invalid/api/v2", "offline.invalid/api/v2", "https:///api/v2"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "API_HOST_URL"):
                self.configure(url=url)

    @staticmethod
    def private_error():
        return ApiException(http_resp=SimpleNamespace(
            status=403, reason="private-reason-marker", data="private-body-marker",
            getheaders=lambda: {"Set-Cookie": "private-cookie-marker", "Authorization": "private-key-marker"},
        ))

    def adapter(self, error):
        client = OfflineClient()

        def fail(*args, **kwargs):
            raise error

        client.experiment.upload_file = fail
        adapter = AsyncBridge(
            "Offline security test", client_factory=lambda: client,
            spool_directory=self.directory / "spool",
        )

        def close():
            with suppress(RuntimeError):
                adapter.close(timeout=3)

        self.addCleanup(close)
        return adapter, client

    def data(self):
        path = self.directory / "run.h5"
        path.write_bytes(b"saved data")
        return SimpleNamespace(filepath=path)

    def assert_private_markers_absent(self, text):
        for marker in ("private-reason-marker", "private-body-marker", "private-cookie-marker", "private-key-marker"):
            self.assertNotIn(marker, text)

    def test_async_status_and_traceback_hide_sdk_headers_body_and_original_cause(self):
        adapter, client = self.adapter(self.private_error())
        future = adapter.enqueue(self.data())
        with self.assertRaises(ApiException):
            future.result(timeout=3)
        summary = adapter.status()[0]["error"]
        self.assertIn("HTTP 403", summary)
        self.assert_private_markers_absent(summary)
        try:
            adapter.flush(timeout=3)
        except RuntimeError as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertIn("HTTP 403", rendered)
            self.assertIsNone(error.__cause__)
            self.assert_private_markers_absent(rendered)
        else:
            self.fail("The failed upload must still raise a useful error")

    def test_context_logging_does_not_print_sdk_private_details(self):
        adapter, client = self.adapter(self.private_error())
        with self.assertLogs("elablink.async_bridge", level="ERROR") as messages:
            with self.assertRaisesRegex(ValueError, "analysis stopped"):
                with adapter:
                    adapter.enqueue(self.data())
                    raise ValueError("analysis stopped")
        self.assert_private_markers_absent("\n".join(messages.output))
        self.assertIn("HTTP 403", "\n".join(messages.output))

    def test_safe_formatter_keeps_useful_local_errors_and_omits_wrapped_responses(self):
        self.assertEqual(safe_error_message(ValueError("Choose a file")), "Choose a file")
        wrapped = RuntimeError("Request failed. HTTP response body: private-body-marker")
        self.assertEqual(safe_error_message(wrapped), "Request failed.")
        failed_connection = ApiException(status=0, reason="private-key-marker")
        self.assert_private_markers_absent(safe_error_message(failed_connection))

    def test_linked_snapshot_sibling_is_rejected_before_any_copy_or_submission(self):
        adapter, client = self.adapter(self.private_error())
        data = self.data()
        sibling = self.directory / "run_notes.txt"
        sibling.write_bytes(b"synthetic protected contents")
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", new=lambda path: path == sibling or original(path)):
            with patch("elablink.async_bridge.shutil.copy2") as copy:
                with self.assertRaisesRegex(ValueError, "symbolic links or junctions"):
                    adapter.enqueue(data)
                copy.assert_not_called()
        self.assertEqual(adapter.status(), [])
        self.assertEqual(client.experiment.files, {})
        self.assertEqual(list(adapter.spool_directory.iterdir()), [])

    def test_h5_symbolic_link_is_rejected_without_reading_its_target(self):
        adapter, client = self.adapter(self.private_error())
        target = self.directory / "private.h5"
        target.write_bytes(b"synthetic private file")
        path = self.directory / "run.h5"
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest("This Windows account cannot create symbolic links")
        with self.assertRaisesRegex(ValueError, "symbolic links or junctions"):
            adapter.enqueue(SimpleNamespace(filepath=path))
        self.assertEqual(client.experiment.files, {})
        self.assertEqual(target.read_bytes(), b"synthetic private file")

    def test_directory_junction_is_rejected_before_capture(self):
        try:
            import _winapi
        except ImportError:
            self.skipTest("Windows junctions are unavailable")
        real = self.directory / "measurements"
        real.mkdir()
        (real / "run.h5").write_bytes(b"synthetic private data")
        alias = self.directory / "linked-measurements"
        try:
            _winapi.CreateJunction(str(real), str(alias))
        except OSError:
            self.skipTest("Creating a test junction is unavailable")
        self.addCleanup(alias.rmdir)
        adapter, client = self.adapter(self.private_error())
        with self.assertRaisesRegex(ValueError, "symbolic links or junctions"):
            adapter.enqueue(SimpleNamespace(filepath=alias / "run.h5"))
        self.assertEqual((real / "run.h5").read_bytes(), b"synthetic private data")
        self.assertEqual(client.experiment.files, {})


class StreamResponse:
    status = 200

    def __init__(self, length, *, fail=False, declared=None, encoding=None):
        self.length = length
        self.position = 0
        self.reads = []
        self.fail = fail
        self.closed = self.released = False
        self.headers = {"Content-Length": str(length if declared is None else declared)}
        if encoding:
            self.headers["Content-Encoding"] = encoding

    @property
    def data(self):
        raise AssertionError("A file download must never preload the complete response")

    def read(self, amount, **kwargs):
        if not 0 < amount <= 1024 * 1024:
            raise AssertionError("Read requests must be bounded")
        self.reads.append(amount)
        if self.fail and self.position:
            raise OSError("Synthetic interrupted connection")
        count = min(amount, self.length - self.position)
        self.position += count
        return b"x" * count

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class StreamingDownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        configuration = self.directory / "elablink.conf"
        configuration.write_text(
            "API_HOST_URL=https://offline.invalid/api/v2\nAPI_KEY=offline-test-key\n"
            "VERIFY_SSL=true\nUNIQUE_EXPERIMENTS_TITLES=true\n", encoding="utf-8",
        )
        self.client = Client(configuration)
        self.addCleanup(self.client.api_client.rest_client.pool_manager.clear)
        self.entity = Experiment(self.client, 17)

    def test_real_sdk_streams_chunks_and_preserves_exact_remote_identity(self):
        response = StreamResponse(8 * 1024 * 1024 + 71)
        destination = self.directory / "downloads/run.h5"
        with patch.object(self.client.api_client.rest_client.pool_manager, "request", return_value=response) as request:
            self.assertEqual(self.entity.download_file(8, destination), destination)
        method, url = request.call_args.args
        self.assertEqual((method, url), ("GET", "https://offline.invalid/api/v2/experiments/17/uploads/8"))
        self.assertFalse(request.call_args.kwargs["preload_content"])
        self.assertEqual(destination.stat().st_size, response.length)
        with destination.open("rb") as stream:
            actual = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                actual.update(chunk)
        digest = actual.hexdigest()
        expected = hashlib.sha256()
        for _ in range(8):
            expected.update(b"x" * (1024 * 1024))
        expected.update(b"x" * 71)
        self.assertEqual(digest, expected.hexdigest())
        self.assertGreater(len(response.reads), 8)
        self.assertTrue(response.released)
        self.assertEqual(list(destination.parent.glob(".elablink-*.part")), [])

    def test_interrupted_or_incomplete_download_keeps_previous_file_and_closes_response(self):
        destination = self.directory / "run.h5"
        for response in (StreamResponse(2 * 1024 * 1024, fail=True), StreamResponse(10, declared=20)):
            with self.subTest(response=response):
                destination.write_bytes(b"existing local data")
                with patch.object(self.client.api_client.rest_client.pool_manager, "request", return_value=response):
                    with self.assertRaises(OSError):
                        self.entity.download_file(8, destination)
                self.assertEqual(destination.read_bytes(), b"existing local data")
                self.assertTrue(response.closed)
                self.assertTrue(response.released)
                self.assertEqual(list(destination.parent.glob(".elablink-*.part")), [])

    def test_encoded_content_length_is_not_compared_with_decoded_file_size(self):
        response = StreamResponse(40, declared=20, encoding="gzip")
        with patch.object(self.client.api_client.rest_client.pool_manager, "request", return_value=response):
            destination = self.entity.download_file(8, self.directory / "run.h5")
        self.assertEqual(destination.stat().st_size, 40)
        self.assertTrue(response.released)
