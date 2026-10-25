"""Offline project-configuration discovery, including background uploads."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from elablink import AsyncBridge, Client
import elablink.config as configuration

from tests.fakes import OfflineClient


@contextmanager
def working_directory(directory):
    previous = Path.cwd()
    os.chdir(directory)
    try:
        yield
    finally:
        os.chdir(previous)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.project = self.root / "project"
        self.examples = self.project / "examples" / "notebooks"
        self.examples.mkdir(parents=True)
        fallback = patch.object(configuration, "_PROJECT_ROOT", self.root / "no-checkout")
        fallback.start()
        self.addCleanup(fallback.stop)

    def write(self, path, content=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path.resolve()

    def pointer(self, directory, filename="elablink.conf"):
        # TOML literal strings also accept Windows backslashes unchanged.
        return self.write(
            directory / "pyproject.toml",
            "[tool.elablink]\nconfig_path = '" + str(filename) + "'\n",
        )

    def credentials(self, path):
        return self.write(
            path,
            "API_HOST_URL=https://offline.invalid/api/v2\n"
            "API_KEY=offline-test-key\n"
            "VERIFY_SSL=true\n"
            "UNIQUE_EXPERIMENTS_TITLES=true\n",
        )

    def resolve_from(self, directory, config_path=None):
        with working_directory(directory):
            return configuration.resolve_config_path(config_path)

    def test_project_pointer_is_relative_to_the_project_not_the_notebook(self):
        expected = self.credentials(self.project / "settings" / "server.conf")
        self.pointer(self.project, "settings/server.conf")
        self.assertEqual(self.resolve_from(self.examples), expected)

    def test_project_pointer_supports_an_absolute_path_outside_the_project(self):
        expected = self.credentials(self.root / "private" / "server.conf")
        self.pointer(self.project, expected)
        self.assertEqual(self.resolve_from(self.examples), expected)

    def test_unrelated_nested_pyproject_does_not_hide_the_project_pointer(self):
        expected = self.credentials(self.project / "elablink.conf")
        self.pointer(self.project)
        self.write(self.project / "examples" / "pyproject.toml", "[tool.other]\nflag = true\n")
        self.assertEqual(self.resolve_from(self.examples), expected)

    def test_nearest_declaring_project_wins(self):
        self.credentials(self.project / "parent.conf")
        self.pointer(self.project, "parent.conf")
        expected = self.credentials(self.project / "examples" / "nested.conf")
        self.pointer(self.project / "examples", "nested.conf")
        self.assertEqual(self.resolve_from(self.examples), expected)

    def test_declared_project_pointer_takes_precedence_over_nearer_default_file(self):
        expected = self.credentials(self.project / "private" / "server.conf")
        self.pointer(self.project, "private/server.conf")
        self.credentials(self.examples / "elablink.conf")
        self.assertEqual(self.resolve_from(self.examples), expected)

    def test_explicit_relative_path_does_not_consult_project_settings(self):
        self.pointer(self.project, "missing.conf")
        expected = self.credentials(self.examples / "chosen.conf")
        self.assertEqual(self.resolve_from(self.examples, "chosen.conf"), expected)

    def test_explicit_missing_path_never_falls_back_to_another_configuration(self):
        self.credentials(self.project / "elablink.conf")
        self.pointer(self.project)
        missing = self.examples / "missing.conf"
        with self.assertRaises(FileNotFoundError):
            self.resolve_from(self.examples, missing)

    def test_missing_project_target_does_not_use_a_different_server_configuration(self):
        self.credentials(self.project / "elablink.conf")
        self.pointer(self.project, "missing.conf")
        self.credentials(self.root / "parent.conf")
        self.pointer(self.root, "parent.conf")
        with self.assertRaises(FileNotFoundError):
            self.resolve_from(self.examples)

    def test_invalid_project_pointer_fails_instead_of_falling_back(self):
        self.credentials(self.project / "elablink.conf")
        for setting in ("config_path = 42", "config_path = ''", "config_path = ['a.conf']"):
            with self.subTest(setting=setting):
                self.write(self.project / "pyproject.toml", "[tool.elablink]\n" + setting + "\n")
                with self.assertRaises(ValueError):
                    self.resolve_from(self.examples)

    def test_declared_tool_table_without_a_path_is_a_configuration_error(self):
        self.credentials(self.project / "elablink.conf")
        self.write(self.project / "pyproject.toml", "[tool.elablink]\n")
        with self.assertRaises(ValueError):
            self.resolve_from(self.examples)

    def test_invalid_tool_table_is_a_configuration_error(self):
        self.credentials(self.project / "elablink.conf")
        self.write(self.project / "pyproject.toml", "[tool]\nelablink = 'server.conf'\n")
        with self.assertRaises(ValueError):
            self.resolve_from(self.examples)

    def test_malformed_project_settings_fail_instead_of_falling_back(self):
        self.credentials(self.project / "elablink.conf")
        self.write(self.project / "pyproject.toml", "[tool.elablink\nconfig_path = 'server.conf'\n")
        with self.assertRaises(ValueError):
            self.resolve_from(self.examples)

    def test_default_file_is_discovered_when_no_project_declares_a_pointer(self):
        self.credentials(self.project / "elablink.conf")
        expected = self.credentials(self.project / "examples" / "elablink.conf")
        self.write(self.project / "pyproject.toml", "[tool.other]\nflag = true\n")
        self.assertEqual(self.resolve_from(self.examples), expected)

    def test_editable_checkout_root_is_a_last_resort(self):
        checkout = self.root / "checkout"
        expected = self.credentials(checkout / "settings" / "server.conf")
        self.pointer(checkout, "settings/server.conf")
        with patch.object(configuration, "_PROJECT_ROOT", checkout):
            self.assertEqual(self.resolve_from(self.examples), expected)

    def test_user_project_configuration_wins_over_the_editable_checkout(self):
        expected = self.credentials(self.project / "user.conf")
        self.pointer(self.project, "user.conf")
        checkout = self.root / "checkout"
        self.credentials(checkout / "library.conf")
        self.pointer(checkout, "library.conf")
        with patch.object(configuration, "_PROJECT_ROOT", checkout):
            self.assertEqual(self.resolve_from(self.examples), expected)

    def test_absent_configuration_reports_how_to_configure_the_project(self):
        with self.assertRaises(FileNotFoundError) as raised:
            self.resolve_from(self.examples)
        self.assertIn("elablink.conf", str(raised.exception))

    def test_client_reads_project_configuration_without_network_access(self):
        expected = self.credentials(self.project / "settings" / "server.conf")
        self.pointer(self.project, "settings/server.conf")
        with (
            working_directory(self.examples),
            patch("elabapi_python.ApiClient.call_api", side_effect=AssertionError("Network access")) as call_api,
        ):
            client = Client()
            self.assertEqual(Path(client.config_path), expected)
            self.assertEqual(client.configuration.host, "https://offline.invalid/api/v2")
            self.assertTrue(client.configuration.verify_ssl)
            self.assertTrue(client.enforce_unique_titles)
            call_api.assert_not_called()

    def test_bridge_pins_project_configuration_before_the_worker_starts(self):
        expected = self.credentials(self.project / "settings" / "server.conf")
        self.pointer(self.project, "settings/server.conf")
        other_project = self.root / "other-project"
        self.credentials(other_project / "other.conf")
        self.pointer(other_project, "other.conf")
        acquisition = self.write(self.project / "data" / "run.h5", "saved data")
        client = OfflineClient()
        caller_thread = threading.get_ident()
        worker_threads = []

        def make_client(config_path):
            self.assertEqual(Path(config_path), expected)
            worker_threads.append(threading.get_ident())
            return client

        with (
            patch("elablink.async_bridge.Client", side_effect=make_client) as factory,
            patch("elablink.async_bridge._set_request_timeouts") as set_timeouts,
        ):
            with working_directory(self.examples):
                bridge = AsyncBridge("Sinusoid", spool_directory=self.root / "spool")
            try:
                factory.assert_not_called()
                self.assertEqual(bridge.config_path, expected)
                with working_directory(other_project):
                    bridge.enqueue(SimpleNamespace(filepath=acquisition))
                    bridge.flush(timeout=3)
                set_timeouts.assert_called_once_with(client, (5, 30))
            finally:
                bridge.close(timeout=3)
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], caller_thread)

    def test_custom_client_factory_needs_no_configuration_file(self):
        with patch("elablink.async_bridge.resolve_config_path", side_effect=AssertionError("Unexpected discovery")):
            bridge = AsyncBridge(
                "Sinusoid",
                spool_directory=self.root / "spool",
                client_factory=OfflineClient,
            )
            bridge.close(timeout=3)

    def test_manager_uses_project_configuration_without_an_explicit_argument(self):
        try:
            from elablink import AcquisitionAnalysisManager
            from labmate.acquisition import LifecycleHooks
        except ImportError:
            self.skipTest("Requires Labmate")
        if not hasattr(LifecycleHooks, "add_analysis_figure_saved"):
            self.skipTest("Requires Labmate with analysis_figure_saved")
        expected = self.credentials(self.project / "settings" / "server.conf")
        self.pointer(self.project, "settings/server.conf")
        with (
            working_directory(self.examples),
            patch("elablink.async_bridge.Client") as factory,
        ):
            manager = AcquisitionAnalysisManager(
                str(self.project / "data"), shell=None, use_magic=False
            )
            try:
                self.assertEqual(manager._elab_bridge.config_path, expected)
                factory.assert_not_called()
            finally:
                manager.close(timeout=3)


if __name__ == "__main__":
    unittest.main()
