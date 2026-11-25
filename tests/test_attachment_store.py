"""Offline transfer and filesystem checks for the attachment browser."""

from concurrent.futures import CancelledError
import hashlib
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from elablink._attachment_store import AttachmentStore


class Response:
    def __init__(self, data, *, length=None, encoding=None, on_read=None):
        self.stream = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}
        if encoding:
            self.headers["Content-Encoding"] = encoding
        self.on_read = on_read
        self.closed = False
        self.released = False

    def read(self, size, **kwargs):
        data = self.stream.read(min(size, 2))
        if self.on_read:
            self.on_read()
        return data

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeClient:
    def __init__(self):
        self.api_client = SimpleNamespace(call_api=Mock())
        self.configuration = SimpleNamespace(host="https://offline.example/api/v2")
        self.rows = []
        self.contents = {}
        self.reads = []
        self.posts = []
        self.renames = []
        self.replacements = []
        self.deletions = []
        self.responses = []
        self.response_factory = Response
        self.uploads = self

    def add(self, name="measurement.txt", data=b"measured signal", ID=None):
        ID = ID or max((row["id"] for row in self.rows), default=0) + 1
        row = {"id": ID, "real_name": name, "name": name, "filesize": len(data),
               "hash": hashlib.sha256(data).hexdigest(), "hash_algorithm": "sha256",
               "created_at": "2026-11-04 10:00:00", "long_name": f"blob-{ID}",
               "raw": object()}
        self.rows.append(row)
        self.contents[ID] = data
        return dict(row)

    def load_experiment(self, *, ID):
        return SimpleNamespace(title="Offline experiment", refresh=lambda: None,
                               list_files=lambda: [dict(row) for row in self.rows])

    load_resource = load_experiment

    def read_upload(self, kind, entity_id, upload_id, **kwargs):
        self.reads.append((kind, entity_id, upload_id, kwargs))
        response = self.response_factory(self.contents[upload_id])
        self.responses.append(response)
        return response

    def post_upload(self, kind, entity_id, *, file):
        self.posts.append((kind, entity_id, file))
        path = Path(file)
        self.add(path.name, path.read_bytes())

    def replace_upload(self, kind, entity_id, upload_id, *, file_path):
        self.replacements.append((kind, entity_id, upload_id, file_path))
        self.rows = [row for row in self.rows if row["id"] != upload_id]
        path = Path(file_path)
        self.add(path.name, path.read_bytes(), ID=upload_id + 100)

    def patch_upload(self, kind, entity_id, upload_id, *, body):
        self.renames.append((kind, entity_id, upload_id, body))
        row = next(row for row in self.rows if row["id"] == upload_id)
        row["name"] = row["real_name"] = body["real_name"]


    def delete_upload(self, kind, entity_id, upload_id):
        self.deletions.append((kind, entity_id, upload_id))
        self.rows = [row for row in self.rows if row["id"] != upload_id]
        self.contents.pop(upload_id)


class AttachmentStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.client = FakeClient()
        self.factory = Mock(return_value=self.client)
        self.store = AttachmentStore(None, "experiments", 17,
                                     self.root / "cache", self.factory)

    def test_client_is_created_lazily_and_remains_owned_by_one_worker(self):
        self.factory.assert_not_called()
        errors = []

        def list_on_worker():
            try:
                self.store.list_files()
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=list_on_worker)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.factory.assert_called_once_with()
        with self.assertRaisesRegex(RuntimeError, "worker"):
            self.store.list_files()

    def test_download_metadata_keeps_upload_ids_paired_with_renamed_paths(self):
        first = self.client.add("old.zip", b"first", ID=11)
        second = self.client.add("old.svg", b"second", ID=22)
        self.client.rows[0]["real_name"] = "new.zip"
        self.client.rows[1]["real_name"] = "new.svg"
        files = self.store.download([second, first], with_metadata=True)
        self.assertEqual([file["row"]["id"] for file in files], [22, 11])
        self.assertEqual([file["path"].name for file in files], ["new.svg", "new.zip"])
        self.assertEqual([file["path"].read_bytes() for file in files], [b"second", b"first"])
        self.assertEqual([file["row"]["real_name"] for file in files], ["new.svg", "new.zip"])

    def test_listing_removes_api_models_and_retains_title(self):
        self.client.add()
        rows = self.store.list_files()
        self.assertEqual(self.store.title, "Offline experiment")
        self.assertNotIn("raw", rows[0])
        self.assertEqual(rows[0]["id"], 1)

    def test_generated_api_calls_receive_a_timeout_even_when_they_pass_none(self):
        call_api = self.client.api_client.call_api
        self.store.list_files()
        self.client.api_client.call_api("/test", "GET", _request_timeout=None)
        self.assertEqual(call_api.call_args.kwargs["_request_timeout"], (5, 30))
        self.client.api_client.call_api("/test", "GET", _request_timeout=(2, 10))
        self.assertEqual(call_api.call_args.kwargs["_request_timeout"], (2, 10))

    def test_corrupted_zero_byte_cache_is_downloaded_again(self):
        row = self.client.add("empty.txt", b"")
        self.client.rows[0].update(hash=None, hash_algorithm=None)
        path = self.store.download([row])[0]
        path.write_bytes(b"unexpected contents")
        self.assertEqual(self.store.download([row])[0].read_bytes(), b"")
        self.assertEqual(len(self.client.reads), 2)

    def test_download_streams_to_persistent_cache_and_reuses_correct_version(self):
        row = self.client.add()
        progress = []
        paths = self.store.download([row], progress=lambda *args: progress.append(args))
        self.assertEqual(paths[0].read_bytes(), b"measured signal")
        self.assertEqual(paths[0].name, "measurement.txt")
        self.assertTrue(paths[0].is_relative_to((self.root / "cache").resolve()))
        self.assertTrue(self.client.responses[0].released)
        self.assertEqual(progress[-1], (15, 15, "measurement.txt"))
        self.assertEqual(self.store.download([row]), paths)
        self.assertEqual(len(self.client.reads), 1)
        self.client.contents[1] = b"new data"
        self.client.rows[0].update(hash=hashlib.sha256(b"new data").hexdigest(), filesize=8)
        updated = self.store.download([row])[0]
        self.assertNotEqual(updated, paths[0])
        self.assertEqual(updated.read_bytes(), b"new data")
        self.assertEqual(paths[0].read_bytes(), b"measured signal")

    def test_same_named_attachments_have_distinct_cache_paths(self):
        first = self.client.add("same.txt", b"first")
        second = self.client.add("same.txt", b"second")
        paths = self.store.download([first, second])
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual([path.read_bytes() for path in paths], [b"first", b"second"])
        with self.assertRaisesRegex(FileExistsError, "same filename"):
            self.store.download([first, second], self.root / "export")

    def test_cache_is_partitioned_by_server_and_record_kind(self):
        row = self.client.add()
        first = self.store.download([row])[0]
        other = AttachmentStore(None, "items", 17, self.root / "cache", lambda: self.client)
        second = other.download([row])[0]
        self.assertNotEqual(first, second)
        self.client.configuration.host = "https://other.example/api/v2"
        third = self.store.download([row])[0]
        self.assertNotEqual(first, third)

    def test_cancel_and_short_response_preserve_previous_file_and_remove_partial(self):
        row = self.client.add()
        destination = self.root / "saved.txt"
        destination.write_bytes(b"previous copy")
        self.client.response_factory = lambda data: Response(data[:3], length=len(data))
        with self.assertRaisesRegex(OSError, "Incomplete download"):
            self.store.save_file(row, destination, overwrite=True)
        self.assertEqual(destination.read_bytes(), b"previous copy")
        self.assertTrue(self.client.responses[-1].closed)
        self.assertTrue(self.client.responses[-1].released)
        self.assertEqual(list(self.root.glob("*.part")), [])
        event = threading.Event()
        self.client.response_factory = lambda data: Response(data, on_read=event.set)
        with self.assertRaises(CancelledError):
            self.store.save_file(row, destination, overwrite=True, cancel=event)
        self.assertEqual(destination.read_bytes(), b"previous copy")
        self.assertTrue(self.client.responses[-1].released)
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_hash_mismatch_is_rejected_before_overwriting_existing_file(self):
        row = self.client.add()
        destination = self.root / "saved.txt"
        destination.write_bytes(b"previous copy")
        self.client.response_factory = lambda data: Response(b"different contents")
        with self.assertRaisesRegex(OSError, "server hash"):
            self.store.save_file(row, destination, overwrite=True)
        self.assertEqual(destination.read_bytes(), b"previous copy")

    def test_encoded_http_length_is_not_compared_with_decoded_bytes(self):
        row = self.client.add()
        self.client.response_factory = lambda data: Response(data, length=2, encoding="gzip")
        destination = self.store.download([row])[0]
        self.assertEqual(destination.read_bytes(), b"measured signal")

    def test_export_checks_existing_targets_before_starting_any_download(self):
        first = self.client.add("first.txt")
        second = self.client.add("second.txt")
        export = self.root / "export"
        export.mkdir()
        (export / "second.txt").write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            self.store.download([first, second], export)
        self.assertEqual(self.client.reads, [])
        self.assertFalse((export / "first.txt").exists())
        self.assertEqual((export / "second.txt").read_bytes(), b"keep")

    def test_export_does_not_clobber_a_file_created_during_download(self):
        row = self.client.add()
        destination = self.root / "race.txt"

        def appeared():
            if not destination.exists():
                destination.write_bytes(b"another application")

        self.client.response_factory = lambda data: Response(data, on_read=appeared)
        with self.assertRaises(FileExistsError):
            self.store.save_file(row, destination)
        self.assertEqual(destination.read_bytes(), b"another application")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_batch_save_keeps_approved_destinations_when_remote_names_change(self):
        first = self.client.add("first.txt", b"remote first")
        second = self.client.add("second.txt", b"remote second")
        first_path, second_path = self.root / "first.txt", self.root / "second.txt"
        other_path = self.root / "other-notes.txt"
        first_path.write_bytes(b"old first")
        other_path.write_bytes(b"unrelated local notes")
        self.client.rows[1].update(name=other_path.name, real_name=other_path.name)
        paths = self.store.save_files({first["id"]: first_path, second["id"]: second_path},
                                      overwrite=[first_path])
        self.assertEqual(paths, [first_path.resolve(), second_path.resolve()])
        self.assertEqual(first_path.read_bytes(), b"remote first")
        self.assertEqual(second_path.read_bytes(), b"remote second")
        self.assertEqual(other_path.read_bytes(), b"unrelated local notes")

    def test_batch_save_checks_unapproved_collisions_before_transferring_any_file(self):
        first = self.client.add("first.txt", b"remote first")
        second = self.client.add("second.txt", b"remote second")
        first_path, second_path = self.root / "first.txt", self.root / "second.txt"
        first_path.write_bytes(b"approved old copy")
        second_path.write_bytes(b"appeared after confirmation")
        with self.assertRaises(FileExistsError):
            self.store.save_files({first["id"]: first_path, second["id"]: second_path},
                                   overwrite=[first_path])
        self.assertEqual(self.client.reads, [])
        self.assertEqual(first_path.read_bytes(), b"approved old copy")
        self.assertEqual(second_path.read_bytes(), b"appeared after confirmation")

    def test_batch_save_does_not_replace_unapproved_file_created_during_transfer(self):
        first = self.client.add("first.txt", b"remote first")
        second = self.client.add("second.txt", b"remote second")
        first_path, second_path = self.root / "first.txt", self.root / "second.txt"
        first_path.write_bytes(b"approved old copy")

        def appeared():
            if not second_path.exists():
                second_path.write_bytes(b"another application's file")

        self.client.response_factory = lambda data: Response(data, on_read=appeared)
        with self.assertRaises(FileExistsError):
            self.store.save_files({first["id"]: first_path, second["id"]: second_path},
                                   overwrite=[first_path])
        self.assertEqual(first_path.read_bytes(), b"remote first")
        self.assertEqual(second_path.read_bytes(), b"another application's file")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_batch_save_commit_does_not_clobber_a_file_created_while_it_is_downloading(self):
        row = self.client.add("signal.txt", b"remote contents")
        destination = self.root / "signal.txt"

        def appeared():
            if not destination.exists():
                destination.write_bytes(b"another application's file")

        self.client.response_factory = lambda data: Response(data, on_read=appeared)
        with self.assertRaises(FileExistsError):
            self.store.save_files({row["id"]: destination})
        self.assertEqual(destination.read_bytes(), b"another application's file")
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_batch_save_rejects_duplicate_destinations_and_unrelated_approval(self):
        first = self.client.add("first.txt")
        second = self.client.add("second.txt")
        target = self.root / "same.txt"
        with self.assertRaisesRegex(FileExistsError, "same destination"):
            self.store.save_files({first["id"]: target, second["id"]: self.root / "SAME.txt"})
        with self.assertRaisesRegex(ValueError, "selected destination"):
            self.store.save_files({first["id"]: target}, overwrite=[self.root / "other.txt"])
        self.assertEqual(self.client.reads, [])

    def test_batch_save_rejects_redirected_destination_before_overwriting(self):
        row = self.client.add("signal.txt")
        target = self.root / "signal.txt"
        notes = self.root / "notes.txt"
        notes.write_bytes(b"unrelated local notes")
        try:
            target.symlink_to(notes)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symbolic links requires additional privileges")
        with self.assertRaisesRegex(ValueError, "symbolic link|unsafe path"):
            self.store.save_files({row["id"]: target}, overwrite=[target])
        self.assertEqual(self.client.reads, [])
        self.assertEqual(notes.read_bytes(), b"unrelated local notes")

    def test_unsafe_server_names_are_rejected_before_download(self):
        for name in ("../escape", "..\\escape", "C:stream", "CON.txt", "LPT1", "trailing.", "trailing ", "a/b", "a\0b"):
            with self.subTest(name=name):
                self.client.rows.clear()
                row = self.client.add(name)
                with self.assertRaisesRegex(ValueError, "filename"):
                    self.store.download([row])
        self.assertEqual(self.client.reads, [])

    def test_upload_conflicts_and_duplicate_names_are_checked_before_writes(self):
        self.client.add("signal.txt")
        source = self.root / "signal.txt"
        source.write_bytes(b"new")
        with self.assertRaises(FileExistsError):
            self.store.upload([source])
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.replacements, [])
        other = self.root / "other"
        other.mkdir()
        (other / "signal.txt").write_bytes(b"different")
        with self.assertRaisesRegex(FileExistsError, "Several selected"):
            self.store.upload([source, other / "signal.txt"], replace=True)
        self.assertEqual(self.client.replacements, [])
        with self.assertRaisesRegex(ValueError, "directories"):
            self.store.upload([other])

    def test_explicit_upload_replacement_and_new_file_refresh_the_list(self):
        self.client.add("signal.txt")
        source = self.root / "signal.txt"
        source.write_bytes(b"new")
        new = self.root / "new.txt"
        new.write_bytes(b"new file")
        rows = self.store.upload([source, new], replace=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(self.client.replacements), 1)
        self.assertEqual(len(self.client.posts), 1)
        self.assertEqual(source.read_bytes(), b"new")
        self.assertEqual(new.read_bytes(), b"new file")

    def test_rename_changes_server_name_and_rejects_collisions(self):
        row = self.client.add("signal.txt")
        self.client.add("already.txt")
        with self.assertRaises(FileExistsError):
            self.store.rename(row["id"], "ALREADY.txt")
        self.assertEqual(self.client.renames, [])
        rows = self.store.rename(row["id"], "renamed.txt")
        self.assertEqual(self.client.renames[0],
                         ("experiments", 17, 1, {"real_name": "renamed.txt"}))
        self.assertEqual(rows[0]["real_name"], "renamed.txt")
        self.assertEqual(self.client.contents[1], b"measured signal")

    def test_missing_selection_and_early_cancellation_do_not_read_files(self):
        with self.assertRaises(FileNotFoundError):
            self.store.download([{"id": 99}])
        event = threading.Event()
        event.set()
        with self.assertRaises(CancelledError):
            self.store.upload([], cancel=event)
        with self.assertRaises(CancelledError):
            self.store.download([], cancel=event)
        self.assertEqual(self.client.reads, [])


    def test_delete_uses_attachment_ids_and_preserves_cached_and_exported_copies(self):
        first = self.client.add("same.txt", b"first")
        second = self.client.add("same.txt", b"second")
        remaining = self.client.add("keep.txt", b"keep")
        cached = self.store.download([first, second])
        exported = self.store.save_file(first, self.root / "exported.txt")
        rows = self.store.delete([second, first])
        self.assertEqual(self.client.deletions, [("experiments", 17, 2), ("experiments", 17, 1)])
        self.assertEqual([row["id"] for row in rows], [remaining["id"]])
        self.assertEqual([path.read_bytes() for path in cached], [b"first", b"second"])
        self.assertEqual(exported.read_bytes(), b"first")

    def test_delete_resources_allows_names_that_cannot_be_saved_locally(self):
        row = self.client.add("../invalid.txt")
        store = AttachmentStore(None, "items", 23, self.root / "cache", self.factory)
        self.assertEqual(store.delete([row]), [])
        self.assertEqual(self.client.deletions, [("items", 23, row["id"])])
        self.assertEqual(self.client.reads, [])

    def test_delete_checks_all_selected_ids_before_sending_requests(self):
        first = self.client.add()
        with self.assertRaises(FileNotFoundError):
            self.store.delete([first, {"id": 999}])
        with self.assertRaisesRegex(ValueError, "more than once"):
            self.store.delete([first, first])
        self.assertEqual(self.client.deletions, [])
        self.assertEqual(len(self.client.rows), 1)

    def test_delete_empty_selection_and_early_cancellation_do_not_change_server_files(self):
        self.client.add()
        self.assertEqual(len(self.store.delete([])), 1)
        event = threading.Event()
        event.set()
        with self.assertRaises(CancelledError):
            self.store.delete(self.client.rows, cancel=event)
        self.assertEqual(self.client.deletions, [])

    def test_delete_can_be_cancelled_between_requests(self):
        first = self.client.add("first.txt")
        second = self.client.add("second.txt")
        event = threading.Event()
        original = self.client.delete_upload

        def delete_first_then_cancel(*args):
            original(*args)
            event.set()

        self.client.delete_upload = delete_first_then_cancel
        with self.assertRaises(CancelledError):
            self.store.delete([first, second], cancel=event)
        self.assertEqual(self.client.deletions, [("experiments", 17, first["id"])])
        self.assertEqual([row["id"] for row in self.client.rows], [second["id"]])

    def test_refused_delete_keeps_file_and_propagates_error(self):
        row = self.client.add()
        self.client.delete_upload = Mock(side_effect=PermissionError("No write permission"))
        with self.assertRaisesRegex(PermissionError, "No write permission"):
            self.store.delete([row])
        self.assertEqual(self.client.rows[0]["id"], row["id"])
        self.assertIn(row["id"], self.client.contents)


if __name__ == "__main__":
    unittest.main()
