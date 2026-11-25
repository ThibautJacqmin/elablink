"""macOS storage aliases may precede user-controlled files and folders."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from elablink._paths import absolute_storage_path
from elablink.async_bridge import AsyncBridge
from tests.fakes import OfflineClient


@unittest.skipUnless(sys.platform == "darwin", "Requires native macOS filesystem aliases")
class MacStoragePathTests(unittest.TestCase):
    def test_only_verified_system_aliases_are_rewritten(self):
        for name in ("var", "tmp"):
            with self.subTest(name=name):
                alias = Path("/") / name
                path = alias / "example" / "nested"
                self.assertEqual(absolute_storage_path(path), Path("/private") / name / "example/nested")
                with patch.object(Path, "readlink", return_value=Path("unexpected")):
                    self.assertEqual(absolute_storage_path(path), path)
                info = SimpleNamespace(st_mode=alias.lstat().st_mode, st_uid=501)
                with patch.object(Path, "lstat", return_value=info):
                    self.assertEqual(absolute_storage_path(path), path)
        self.assertEqual(absolute_storage_path("/etc/example"), Path("/etc/example"))

    def test_acquisition_under_tmp_preserves_nested_link_protection(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            data = root / "run.h5"
            data.write_bytes(b"measurement")
            acquisition_client = OfflineClient()
            with AsyncBridge("Run", spool_directory=root / "queue",
                             client_factory=lambda: acquisition_client) as bridge:
                bridge.enqueue(SimpleNamespace(filepath=data))
                bridge.flush(timeout=5)
                self.assertEqual(acquisition_client.experiment.files["run.h5"], b"measurement")

                folder = root / "working"
                folder.mkdir()
                link = root / "linked"
                link.symlink_to(folder, target_is_directory=True)
                (folder / "other.h5").write_bytes(b"private")
                with self.assertRaisesRegex(ValueError, "symbolic links"):
                    bridge.enqueue(SimpleNamespace(filepath=link / "other.h5"))
