"""Offline coverage of warning repetition without changing TLS settings."""

from types import SimpleNamespace
import unittest
import warnings

from urllib3 import HTTPSConnectionPool
from urllib3.exceptions import InsecureRequestWarning

from elablink import Client


class TLSWarningTests(unittest.TestCase):
    def configure(self, *, verify_ssl=False, url="https://lab.example/api/v2"):
        client = Client.__new__(Client)
        client.config = {
            "API_HOST_URL": url,
            "API_KEY": "offline-test-key",
            "VERIFY_SSL": str(verify_ssl),
            "UNIQUE_EXPERIMENTS_TITLES": "true",
        }
        client._api_configuration()
        return client

    @staticmethod
    def warn_unverified(host):
        # _validate_conn only checks these flags; no socket is opened.
        connection = SimpleNamespace(
            host=host, is_closed=False, is_verified=False, proxy_is_verified=False
        )
        HTTPSConnectionPool(host)._validate_conn(connection)

    def test_unverified_server_warns_once_and_keeps_tls_configuration(self):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            client = self.configure()
            for _ in range(3):
                self.warn_unverified("lab.example")
        self.assertFalse(client.configuration.verify_ssl)
        self.assertEqual(client.configuration.host, "https://lab.example/api/v2")
        self.assertEqual(len(captured), 1)
        self.assertIs(captured[0].category, InsecureRequestWarning)
        self.assertIn("Adding certificate verification is strongly advised", str(captured[0].message))

    def test_other_hosts_and_modules_are_not_filtered(self):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            self.configure()
            for _ in range(2):
                self.warn_unverified("labXexample")
                self.warn_unverified("lab.example.other")
                warnings.warn(
                    "Unverified HTTPS request is being made to host 'lab.example'.",
                    InsecureRequestWarning,
                )
        self.assertEqual(len(captured), 6)

    def test_verified_client_keeps_warning_filters_and_tls_verification(self):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            previous = warnings.filters[:]
            client = self.configure(verify_ssl=True)
            self.assertEqual(warnings.filters, previous)
            for _ in range(2):
                self.warn_unverified("lab.example")
        self.assertTrue(client.configuration.verify_ssl)
        self.assertEqual(len(captured), 2)

    def test_http_configuration_does_not_install_tls_filters(self):
        with warnings.catch_warnings():
            previous = warnings.filters[:]
            self.configure(url="http://lab.example/api/v2")
            self.assertEqual(warnings.filters, previous)
