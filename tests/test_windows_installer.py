"""Offline coverage of the downloadable Windows browser installer."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


INSTALLER = Path(__file__).resolve().parents[1] / "tools" / "windows_installer"
if not (INSTALLER / "install_browser.py").is_file():
    raise unittest.SkipTest("The separate Windows installer sources are unavailable")
with patch.object(sys, "path", [str(INSTALLER), *sys.path]):
    import browser_env
    import install_browser


class WindowsInstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="elablink installer tests ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.install_dir = self.root / "installed browser"
        self.desktop = self.root / "redirected desktop"
        self.desktop.mkdir()
        self.wheel = self.root / "elablink-0.3.0a3-py3-none-any.whl"
        self.wheel.write_bytes(b"test release archive")
        self.config = self.root / "configuration with spaces" / "team.conf"
        self.config.parent.mkdir()

    def test_private_config_preserves_server_path_and_enables_verification(self):
        for supplied in ("https://office.invalid/lab", "https://office.invalid/lab/api/v2/"):
            with self.subTest(supplied=supplied):
                self.assertEqual(install_browser.normalize_api_url(supplied),
                                 "https://office.invalid/lab/api/v2")
        install_browser.create_config(self.config, "https://office.invalid/lab", "offline-test-key")
        settings = dict(line.split("=", 1) for line in self.config.read_text(encoding="utf-8").splitlines()
                        if line and not line.startswith("#"))
        self.assertEqual(settings["API_HOST_URL"], "https://office.invalid/lab/api/v2")
        self.assertEqual(settings["VERIFY_SSL"].lower(), "true")
        self.assertEqual(settings["API_KEY"], "offline-test-key")
        self.assertEqual(settings["UNIQUE_EXPERIMENTS_TITLES"].lower(), "true")
        if os.name == "nt":
            authorized = install_browser._powershell(
                "$acl=Get-Acl -LiteralPath $env:ELABLINK_TEST_CONFIG;"
                "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
                "$rules=$acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]);"
                "$readers=@($rules | Where-Object { $_.AccessControlType -eq 'Allow' "
                "-and ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ReadData) });"
                "$acl.AreAccessRulesProtected -and $readers.Count -eq 1 -and $readers[0].IdentityReference.Value -eq $sid",
                ELABLINK_TEST_CONFIG=self.config,
            )
            self.assertEqual(authorized, "True")

    def test_invalid_config_never_leaves_a_file_or_prints_the_key(self):
        for supplied in ("http://office.invalid", "https://person@office.invalid",
                         "https://office.invalid/?token=example", "https://office.invalid/#fragment"):
            with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                install_browser.create_config(self.config, supplied, "offline-test-key")
            self.assertFalse(self.config.exists())
        with self.assertRaises(ValueError) as raised:
            install_browser.create_config(self.config, "https://office.invalid", "not-a-key\nVERIFY_SSL=false")
        self.assertNotIn("not-a-key", str(raised.exception))
        self.assertFalse(self.config.exists())

    def test_existing_private_configuration_is_never_overwritten(self):
        self.config.write_bytes(b"private existing settings\r\n")
        with self.assertRaises(FileExistsError):
            install_browser.create_config(self.config, "https://office.invalid", "sample")
        self.assertEqual(self.config.read_bytes(), b"private existing settings\r\n")

    def fake_runtime(self, directory, wheel, conda):
        directory.mkdir(parents=True, exist_ok=True)
        for executable in ("python.exe", "pythonw.exe"):
            location = directory / ("Scripts" if conda is None else "") / executable
            location.parent.mkdir(parents=True, exist_ok=True)
            location.touch()
        (directory / "mock-runtime").write_bytes(wheel.read_bytes())

    def test_healthy_environment_is_verified_and_reused_for_unchanged_wheel(self):
        for conda in (None, self.root / "base with spaces" / "Scripts" / "conda.exe"):
            root = self.root / ("standard build" if conda is None else "conda build")
            with self.subTest(conda=conda), patch.object(browser_env, "_conda_executable", return_value=conda), \
                    patch.object(browser_env, "_build_environment", side_effect=self.fake_runtime) as build, \
                    patch.object(browser_env, "_verify_environment") as verify:
                first = browser_env.prepare_environment(root, self.wheel)
                second = browser_env.prepare_environment(root, self.wheel)
                self.assertEqual(first, second)
                self.assertEqual(build.call_count, 1)
                self.assertGreaterEqual(verify.call_count, 2)
                launcher, runtime, is_conda = first
                self.assertTrue(launcher.is_relative_to(runtime))
                self.assertTrue(launcher.is_file())
                self.assertEqual(is_conda, conda is not None)

    def test_a_failed_replacement_leaves_previous_runtime_usable(self):
        with patch.object(browser_env, "_conda_executable", return_value=None), \
                patch.object(browser_env, "_build_environment", side_effect=self.fake_runtime), \
                patch.object(browser_env, "_verify_environment") as verify:
            old_launch, previous, _ = browser_env.prepare_environment(self.install_dir, self.wheel)
            old_marker = (previous / "mock-runtime").read_bytes()
            self.wheel.write_bytes(b"different release archive")
            verify.side_effect = subprocess.CalledProcessError(1, ["python", "-I"])
            with self.assertRaises((OSError, RuntimeError, subprocess.CalledProcessError)):
                browser_env.prepare_environment(self.install_dir, self.wheel)
            self.assertEqual((previous / "mock-runtime").read_bytes(), old_marker)
            self.assertTrue(old_launch.is_file())

    def test_existing_config_is_referenced_in_place_without_copying_key(self):
        install_browser.create_config(self.config, "https://office.invalid", "local-only-test-key")
        selected = install_browser.select_config(self.install_dir, None, supplied=self.config, interactive=False)
        self.assertEqual(selected, self.config.resolve())
        self.assertEqual(list(self.install_dir.rglob("*.conf")), [])

    def test_installer_preserves_unrelated_or_record_specific_shortcut(self):
        first = self.desktop / "eLabLink Attachment Browser.lnk"
        first.touch()
        unrelated = {"TargetPath": "C:\\OtherApp\\app.exe", "Arguments": "", "Description": ""}
        custom = {"TargetPath": "C:\\Browser\\elablink-attachments.exe", "WorkingDirectory": str(self.root),
                  "Arguments": '--entity-id 4 --config-path "' + str(self.config) + '"'}
        for existing in (unrelated, custom):
            def info_for(path):
                return existing if path == first else None

            with self.subTest(existing=existing), patch.object(install_browser, "read_shortcut", side_effect=info_for):
                target, previous = install_browser._shortcut_slot(self.desktop)
            self.assertEqual(target, self.desktop / "eLabLink Attachment Browser (Installed).lnk")
            self.assertIsNone(previous)

    def test_previous_anaconda_shortcut_contributes_its_existing_config(self):
        first = self.desktop / "eLabLink Attachments.lnk"
        first.write_bytes(b"legacy Anaconda launcher")
        self.config.touch()
        old = {"TargetPath": r"C:\Anaconda3\pythonw.exe", "WorkingDirectory": str(self.root),
               "Arguments": (r'-I "C:\Anaconda3\cwp.py" --no-console "C:\Anaconda3\envs\myenv" '
                             r'"C:\Anaconda3\envs\myenv\Scripts\elablink-attachments.exe" '
                             '--config-path "' + str(self.config) + '"')}
        with patch.object(install_browser, "read_shortcut", return_value=old):
            target, previous = install_browser._shortcut_slot(self.desktop)
        self.assertEqual(target, self.desktop / "eLabLink Attachment Browser.lnk")
        self.assertEqual(previous, self.config)
        self.assertEqual(first.read_bytes(), b"legacy Anaconda launcher")

    def test_fallback_shortcuts_preserve_the_configuration_across_rename_and_retry(self):
        self.config.touch()
        old_link = self.desktop / "eLabLink Attachments (Installed 3).lnk"
        old_link.write_bytes(b"older managed shortcut")
        info = {"Description": "eLabLink Attachments (per-user installer)",
                "Arguments": '--config-path "' + str(self.config) + '"'}
        with patch.object(install_browser, "read_shortcut", return_value=info):
            target, previous = install_browser._shortcut_slot(self.desktop)
        self.assertEqual(target, self.desktop / "eLabLink Attachment Browser.lnk")
        self.assertEqual(previous, self.config)

        current_config = self.root / "current.conf"
        current_config.touch()
        current = self.desktop / "eLabLink Attachment Browser (Installed 2).lnk"
        current.touch()
        new_info = {"Description": "eLabLink Attachment Browser (per-user installer)",
                    "Arguments": '--config-path "' + str(current_config) + '"'}
        with patch.object(install_browser, "read_shortcut",
                          side_effect=lambda path: new_info if path == current else info):
            self.assertEqual(install_browser._shortcut_slot(self.desktop), (current, current_config))
            current_config.unlink()
            self.assertEqual(install_browser._shortcut_slot(self.desktop), (current, self.config))
        self.assertEqual(old_link.read_bytes(), b"older managed shortcut")

    def test_build_commands_use_isolated_python_and_pinned_qt_for_both_flavors(self):
        for conda in (None, self.root / "base with spaces" / "Scripts" / "conda.exe"):
            runtime = self.root / ("standard runtime" if conda is None else "conda runtime")
            runtime.mkdir()
            with self.subTest(conda=conda), patch.object(subprocess, "run") as execute:
                browser_env._build_environment(runtime, self.wheel, conda)
                commands = [call.args[0] for call in execute.call_args_list]
                if conda is None:
                    self.assertEqual(commands[0], [sys.executable, "-I", "-m", "venv", str(runtime)])
                else:
                    self.assertEqual(commands[0][0:2], [str(conda), "create"])
                    self.assertIn(str(runtime), commands[0])
                pip_install = next(command for command in commands if "install" in command)
                self.assertIn(str(self.wheel) + "[browser]", pip_install)
                self.assertIn("PyQt6==" + browser_env.QT_VERSION, pip_install)
                self.assertIn("PyQt6-Qt6==" + browser_env.QT_VERSION, pip_install)
                self.assertIn(str(runtime / ("Scripts" if conda is None else "") / "python.exe"), pip_install)
                self.assertTrue(all(call.kwargs.get("check") for call in execute.call_args_list))

    def test_failed_environment_build_never_calls_shortcut_publisher(self):
        install_browser.create_config(self.config, "https://office.invalid", "local-only-test-key")
        old_link = self.desktop / "eLabLink Attachment Browser.lnk"
        old_link.write_bytes(b"known previous desktop shortcut")
        old_bytes = old_link.read_bytes()
        previous = {"TargetPath": str(self.root / "previous" / "pythonw.exe"),
                    "Arguments": "", "WorkingDirectory": str(self.root),
                    "Description": "eLabLink Attachments (per-user installer)"}
        output = io.StringIO()
        args = ["--install-dir", str(self.install_dir), "--desktop-dir", str(self.desktop),
                "--config-path", str(self.config), "--wheel", str(self.wheel), "--non-interactive"]
        with patch.object(install_browser, "read_shortcut", return_value=previous), \
                patch.object(install_browser.browser_env, "prepare_environment",
                             side_effect=RuntimeError("verification failed")) as prepare, \
                patch.object(install_browser, "publish_shortcut") as publish, \
                redirect_stdout(output), redirect_stderr(output):
            result = install_browser.main(args)
        self.assertNotEqual(result, 0)
        prepare.assert_called_once()
        publish.assert_not_called()
        self.assertEqual(old_link.read_bytes(), old_bytes)
        self.assertNotIn("local-only-test-key", output.getvalue())
        self.assertIn("eLabLink core library", output.getvalue())
        self.assertIn("private Python environment", output.getvalue())

    @unittest.skipUnless(sys.platform == "win32", "Windows batch entry point")
    def test_batch_entry_point_loads_the_packaged_helper_with_isolated_python(self):
        cmd = INSTALLER / "Install-eLabLink-Attachment-Browser.cmd"
        result = subprocess.run([os.environ["COMSPEC"], "/d", "/c", "call", str(cmd), "--help"],
                                cwd=self.root, capture_output=True, text=True, timeout=20,
                                env={**os.environ, "CONDA_PREFIX": str(Path(sys.executable).parent),
                                     "PYTHONPATH": "", "USERPROFILE": str(self.root)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--config-path", result.stdout)

    @unittest.skipUnless(sys.platform == "win32", "Windows shortcut format")
    def test_real_shortcut_preserves_spaces_and_future_failed_publish_preserves_old_link(self):
        install_browser.create_config(self.config, "https://office.invalid", "local-only-test-key")
        runtime = self.install_dir / "runtime with spaces"
        pythonw = runtime / "Scripts" / "pythonw.exe"
        pythonw.parent.mkdir(parents=True)
        pythonw.touch()
        target = self.desktop / "eLabLink Attachment Browser.lnk"
        install_browser.publish_shortcut(target, pythonw, runtime, self.config)
        initial_link = target.read_bytes()
        info = install_browser.read_shortcut(target)
        self.assertEqual(info["Description"], "eLabLink Attachment Browser (per-user installer)")
        self.assertEqual(Path(info["TargetPath"]), pythonw)
        self.assertEqual(Path(info["WorkingDirectory"]), runtime)
        self.assertIn(str(self.config), info["Arguments"])
        self.assertNotIn("local-only-test-key", info["Arguments"])
        self.assertNotIn("local-only-test-key".encode("utf-16-le"), initial_link)

        def fail_after_writing(staged, *args):
            self.assertNotEqual(staged, target)
            staged.write_bytes(b"incomplete new shortcut")
            raise OSError("Temporary shortcut could not be saved")

        with patch.object(install_browser, "write_shortcut", side_effect=fail_after_writing):
            with self.assertRaises(OSError):
                install_browser.publish_shortcut(target, pythonw, runtime, self.config)
        self.assertEqual(target.read_bytes(), initial_link)
        self.assertEqual(list(self.desktop.iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
