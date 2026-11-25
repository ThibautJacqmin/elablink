"""Offline checks of the desktop browser launch boundary."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from elablink import Experiment, Resource
from elablink.attachment_browser import _find_klayout, browse_files, open_h5viewer, open_klayout


class BrowserLaunchTests(unittest.TestCase):
    def test_core_import_does_not_require_qt(self):
        source = (
            "import builtins\n"
            "original = builtins.__import__\n"
            "def restricted(name, *args, **kwargs):\n"
            "    if name.startswith('PyQt6'):\n"
            "        raise ImportError('Qt deliberately unavailable')\n"
            "    return original(name, *args, **kwargs)\n"
            "builtins.__import__ = restricted\n"
            "from elablink import Experiment, Resource, Client\n"
        )
        result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_record_types_delegate_without_loading_remote_metadata(self):
        for entity_type in (Experiment, Resource):
            with self.subTest(entity_type=entity_type.__name__):
                api = Mock()
                entity = entity_type(api, 17)
                with patch("elablink.attachment_browser.browse_files") as launch:
                    self.assertIs(entity.browse_files(), launch.return_value)
                launch.assert_called_once_with(entity, folder=None)
                api.experiments.get_experiment.assert_not_called()
                api.items.get_item.assert_not_called()

    def test_missing_browser_dependency_has_actionable_error(self):
        with patch("elablink.attachment_browser.version", side_effect=PackageNotFoundError("PyQt6")):
            with self.assertRaisesRegex(ImportError, r"elablink\[browser\]"):
                browse_files(SimpleNamespace())

    def test_launch_uses_record_identity_and_config_path_without_credentials_or_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            configuration = Path(temporary) / "private.conf"
            configuration.write_text("API_KEY=private-test-key", encoding="utf-8")
            entity = Resource(SimpleNamespace(config_path=configuration), 19)
            process = Mock()
            with (
                patch("elablink.attachment_browser.version", return_value="6.8.1"),
                patch("elablink.attachment_browser.subprocess.Popen", return_value=process) as launch,
            ):
                self.assertIs(entity.browse_files(), process)
            command = launch.call_args.args[0]
            self.assertIn("elablink._attachment_browser_qt", command)
            self.assertEqual(command[1:4], ["-I", "-m", "elablink._attachment_browser_qt"])
            self.assertEqual(command[-4:], ["--entity-type", "items", "--entity-id", "19"])
            self.assertIn(str(configuration.resolve()), command)
            self.assertNotIn("private-test-key", " ".join(command))
            process.wait.assert_not_called()
            process.communicate.assert_not_called()
            self.assertNotEqual(launch.call_args.kwargs["stderr"], subprocess.PIPE)
            if sys.platform == "win32":
                self.assertEqual(launch.call_args.kwargs["startupinfo"].wShowWindow, 1)

    def test_invalid_record_or_missing_config_does_not_start_a_process(self):
        with (
            patch("elablink.attachment_browser.version", return_value="6.8.1"),
            patch("elablink.attachment_browser.subprocess.Popen") as launch,
        ):
            with self.assertRaises(ValueError):
                Experiment(SimpleNamespace(), 0).browse_files()
            with tempfile.TemporaryDirectory() as temporary:
                missing = Path(temporary) / "missing.conf"
                with self.assertRaises(FileNotFoundError):
                    Experiment(SimpleNamespace(config_path=missing), 17).browse_files()
            launch.assert_not_called()

    def test_selected_working_folder_is_passed_to_the_independent_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = root / "private.conf"
            configuration.touch()
            selected = root / "my experiment"
            entity = Experiment(SimpleNamespace(config_path=configuration), 17)
            with (patch("elablink.attachment_browser.version", return_value="6.8.1"),
                  patch("elablink.attachment_browser.subprocess.Popen", return_value=Mock()) as launch):
                entity.browse_files(folder=selected)
            command = launch.call_args.args[0]
            self.assertEqual(command[-2:], ["--folder", str(selected.resolve())])

    def test_spyder_pyqt5_guard_does_not_block_launching_the_pyqt6_child(self):
        for package in ("PyQt5", "IPython"):
            try:
                version(package)
            except PackageNotFoundError:
                self.skipTest(f"Optional Spyder dependency {package} is unavailable")
        source = (
            "import sys, tempfile\n"
            "from pathlib import Path\n"
            "from types import SimpleNamespace\n"
            "from unittest.mock import Mock, patch\n"
            "from importlib.util import find_spec\n"
            "from PyQt5 import QtCore\n"
            "from IPython.external.qt_loaders import commit_api, QT_API_PYQT5\n"
            "commit_api(QT_API_PYQT5)\n"
            "from elablink import Experiment\n"
            "with tempfile.TemporaryDirectory() as directory:\n"
            "    config = Path(directory) / 'elablink.conf'\n"
            "    config.touch()\n"
            "    with patch('elablink.attachment_browser.subprocess.Popen', return_value=Mock()) as launch:\n"
            "        Experiment(SimpleNamespace(config_path=config), 17).browse_files()\n"
            "        assert launch.call_args.args[0][1:4] == ['-I', '-m', 'elablink._attachment_browser_qt']\n"
            "assert 'PyQt6' not in sys.modules\n"
            "try:\n"
            "    find_spec('PyQt6')\n"
            "except ImportError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('The parent Qt guard must remain active')\n"
        )
        result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class H5ViewerLaunchTests(unittest.TestCase):
    def test_optional_viewer_missing_has_actionable_error_without_importing_it(self):
        with patch("elablink.attachment_browser.find_spec", return_value=None) as probe:
            with self.assertRaisesRegex(ImportError, "pip install -e"):
                open_h5viewer("run.h5")
        probe.assert_called_once_with("h5viewer")

    def test_viewer_gets_exact_file_and_working_directory_in_independent_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "measurement with spaces.h5"
            path.touch()
            process = Mock()
            with (
                patch("elablink.attachment_browser.find_spec", return_value=object()),
                patch("elablink.attachment_browser.subprocess.Popen", return_value=process) as launch,
            ):
                self.assertIs(open_h5viewer(path), process)
            command = launch.call_args.args[0]
            self.assertEqual(command[1:], ["-I", "-c", "from h5viewer.main import main; main()",
                                           str(path.resolve())])
            self.assertEqual(launch.call_args.kwargs["cwd"], str(path.parent.resolve()))
            self.assertFalse(launch.call_args.kwargs.get("shell", False))
            self.assertNotEqual(launch.call_args.kwargs["stderr"], subprocess.PIPE)
            self.assertTrue(process.browser_log_path.name.startswith("h5viewer-"))
            process.wait.assert_not_called()
            process.communicate.assert_not_called()
            process.terminate.assert_not_called()

    def test_missing_file_never_launches_viewer(self):
        with (
            patch("elablink.attachment_browser.find_spec", return_value=object()),
            patch("elablink.attachment_browser.subprocess.Popen") as launch,
            tempfile.TemporaryDirectory() as temporary,
        ):
            with self.assertRaises(FileNotFoundError):
                open_h5viewer(Path(temporary) / "missing.h5")
            launch.assert_not_called()


class KLayoutLaunchTests(unittest.TestCase):
    def test_path_is_used_when_no_known_installation_exists(self):
        with (patch("elablink.attachment_browser.shutil.which", return_value="/opt/klayout") as which,
              patch("elablink.attachment_browser.Path.is_file", return_value=False)):
            self.assertEqual(_find_klayout(), "/opt/klayout")
        self.assertEqual(which.call_args.args[0], "klayout_app")

    def test_current_directory_executable_is_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with (patch("elablink.attachment_browser.shutil.which", return_value=str(root / "klayout_app.exe")),
                  patch("elablink.attachment_browser.Path.is_file", return_value=False),
                  patch("elablink.attachment_browser.Path.cwd", return_value=root)):
                self.assertIsNone(_find_klayout())

    def test_windows_install_is_found_without_a_path_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "Program Files" / "KLayout" / "klayout_app.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with (
                patch("elablink.attachment_browser.shutil.which", return_value=None),
                patch("elablink.attachment_browser.sys.platform", "win32"),
                patch.dict("elablink.attachment_browser.os.environ",
                           {"ProgramFiles": str(executable.parent.parent)}),
            ):
                self.assertEqual(_find_klayout(), str(executable))

    def test_macos_app_bundle_is_found_in_user_applications(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            executable = home / "Applications" / "klayout.app" / "Contents" / "MacOS" / "klayout"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with (
                patch("elablink.attachment_browser.shutil.which", return_value=None),
                patch("elablink.attachment_browser.sys.platform", "darwin"),
                patch("elablink.attachment_browser.Path.home", return_value=home),
            ):
                self.assertEqual(_find_klayout(), str(executable))

    def test_missing_desktop_application_returns_none(self):
        with (
            patch("elablink.attachment_browser.shutil.which", return_value=None),
            patch("elablink.attachment_browser.Path.is_file", return_value=False),
        ):
            self.assertIsNone(_find_klayout())

    def test_gds_with_spaces_is_passed_as_one_argument_without_shell_or_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test layout with spaces.GDS"
            path.touch()
            executable = str(Path(temporary) / "Program Files" / "KLayout" / "klayout_app.exe")
            process = Mock()
            with (
                patch("elablink.attachment_browser._find_klayout", return_value=executable),
                patch("elablink.attachment_browser.subprocess.Popen", return_value=process) as launch,
            ):
                self.assertIs(open_klayout(path), process)
            self.assertEqual(launch.call_args.args[0], [executable, str(path.resolve())])
            self.assertEqual(launch.call_args.kwargs["cwd"], str(path.parent.resolve()))
            self.assertFalse(launch.call_args.kwargs.get("shell", False))
            self.assertTrue(process.browser_log_path.name.startswith("klayout-"))
            process.wait.assert_not_called()
            process.communicate.assert_not_called()

    def test_missing_application_explains_how_to_enable_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.gds"
            path.touch()
            with (
                patch("elablink.attachment_browser._find_klayout", return_value=None),
                patch("elablink.attachment_browser.subprocess.Popen") as launch,
            ):
                with self.assertRaisesRegex(ImportError, "KLayout desktop application"):
                    open_klayout(path)
                launch.assert_not_called()

    def test_missing_file_does_not_launch_or_search_for_application(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("elablink.attachment_browser._find_klayout") as find,
            patch("elablink.attachment_browser.subprocess.Popen") as launch,
        ):
            with self.assertRaises(FileNotFoundError):
                open_klayout(Path(temporary) / "missing.gds")
            find.assert_not_called()
            launch.assert_not_called()
