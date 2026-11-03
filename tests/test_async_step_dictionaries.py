"""Offline checks of automatic metadata reads and queued checkbox edits."""

from concurrent.futures import Future
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from elablink import AsyncBridge, AsyncExperiment, InvalidTag


class DictionaryExperiment:
    def __init__(self, ID):
        self.ID = ID
        self.rows = [{"ID": 7, "text": "Align the beam", "status": False}]
        self._category = "Calibration"
        self._status = "Planned"
        self._tags = ["optics"]
        self._available_tags = {"optics": 11, "calibration": 12}
        self._categories = {"Calibration": 2, "Measurement": 3}
        self._statuses = {"Planned": 4, "Completed": 5}
        self.calls = []
        self.started = threading.Event()
        self.release = None
        self.edit_started = threading.Event()
        self.edit_release = None
        self.fail_refresh = False
        self.fail_edit = False

    def record(self, name):
        self.calls.append((name, threading.get_ident()))

    def refresh(self):
        self.record("refresh")
        self.started.set()
        if self.release is not None and not self.release.wait(3):
            raise TimeoutError("Test did not release refresh")
        if self.fail_refresh:
            raise RuntimeError("Offline refresh failed")

    @property
    def steps(self):
        self.record("steps")
        return {row["text"]: SimpleNamespace(**row) for row in self.rows}

    @property
    def steps_by_id(self):
        self.record("steps")
        return {row["ID"]: SimpleNamespace(**row) for row in self.rows}

    @property
    def categories(self):
        self.record("categories")
        return self._categories

    @property
    def statuses(self):
        self.record("statuses")
        return self._statuses

    @property
    def tags(self):
        self.record("tags")
        return self._tags

    @property
    def available_tags(self):
        self.record("available_tags")
        return self._available_tags

    def add_tag(self, tag):
        self.record("add tag")
        if tag not in self._available_tags:
            raise InvalidTag(tag, self._available_tags)
        self._tags.append(tag)

    @property
    def category(self):
        self.record("category")
        return self._category

    @category.setter
    def category(self, value):
        self.record("set category")
        self._category = {2: "Calibration", 3: "Measurement"}.get(value, value)

    @property
    def status(self):
        self.record("status")
        return self._status

    @status.setter
    def status(self, value):
        self.record("set status")
        self._status = {4: "Planned", 5: "Completed"}.get(value, value)

    def add_step(self, text, *, checked=False):
        self.record("add step")
        self.rows.append({"ID": 7 + len(self.rows), "text": text, "status": checked})

    def check_step(self, text_or_ID, *, checked=True, occurrence=None):
        self.record("check step")
        self.edit_started.set()
        if self.edit_release is not None and not self.edit_release.wait(3):
            raise TimeoutError("Test did not release checkbox edit")
        if self.fail_edit:
            raise RuntimeError("Offline checkbox failed")
        matches = [row for row in self.rows
                   if row["ID"] == text_or_ID or row["text"] == text_or_ID]
        if not matches:
            raise KeyError(text_or_ID)
        matches[0 if occurrence is None else occurrence]["status"] = checked


class AsyncStepDictionaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bridge = AsyncBridge(
            spool_directory=Path(self.directory.name) / "spool",
            client_factory=lambda: None,
        )
        self.releases = []
        self.addCleanup(self.close_bridge)
        self.bridge._client = SimpleNamespace(
            configuration=SimpleNamespace(host="https://offline.example/api/v2")
        )
        self.remote = DictionaryExperiment(1)
        self.handle = self.make_handle(self.remote)

    def close_bridge(self):
        for release in self.releases:
            release.set()
        with suppress(RuntimeError, TimeoutError):
            self.bridge.close(timeout=5)

    def make_handle(self, remote):
        self.bridge._experiments[remote.ID] = remote
        prerequisite = Future()
        prerequisite.set_result(None)
        return AsyncExperiment(self.bridge, remote.ID, str(remote.ID), prerequisite)

    def test_reads_fetch_automatically_on_worker_without_explicit_refresh(self):
        self.assertEqual(list(self.handle.steps), ["Align the beam"])
        self.assertEqual(self.handle.categories["Calibration"], 2)
        self.assertEqual(self.handle.statuses["Completed"], 5)
        self.assertEqual(self.handle.category, "Calibration")
        self.assertEqual(self.handle.status, "Planned")
        self.assertEqual(self.handle.tags, ["optics"])
        self.assertEqual(self.handle.available_tags["calibration"], 12)
        self.assertTrue(all(t != threading.get_ident() for _, t in self.remote.calls))

    def test_catalog_reads_fetch_only_requested_choices_and_see_server_edits(self):
        self.assertEqual(self.handle.categories["Calibration"], 2)
        self.assertEqual([name for name, _ in self.remote.calls], ["categories"])
        self.remote._categories["New category"] = 6
        self.remote._statuses["New status"] = 8
        self.remote._available_tags["new tag"] = 13
        self.assertEqual(self.handle.categories["New category"], 6)
        self.assertEqual(self.handle.statuses["New status"], 8)
        self.assertEqual(self.handle.available_tags["new tag"], 13)
        self.assertNotIn("refresh", [name for name, _ in self.remote.calls])

    def test_attached_tags_are_fresh_independent_lists_and_follow_queued_edits(self):
        tags = self.handle.tags
        tags.append("local only")
        self.remote._tags.append("added in browser")
        self.assertEqual(self.handle.tags, ["optics", "added in browser"])
        self.handle.add_tag("calibration")
        self.assertEqual(self.handle.tags, ["optics", "added in browser", "calibration"])

    def test_missing_tag_error_lists_choices_in_future_and_flush(self):
        future = self.handle.add_tag("missing")
        with self.assertRaisesRegex(InvalidTag, r"Available tags: 'optics' \(ID 11\)"):
            future.result(timeout=3)
        with self.assertRaisesRegex(RuntimeError, "Available tags: 'optics'"):
            self.bridge.flush(timeout=3)
        self.assertEqual(self.remote._tags, ["optics"])

    def test_refresh_remains_optional_and_queued(self):
        release = threading.Event()
        self.releases.append(release)
        self.remote.release = release
        future = self.handle.refresh()
        self.assertTrue(self.remote.started.wait(2))
        self.assertFalse(future.done())
        release.set()
        future.result(timeout=3)
        count = len(self.remote.calls)
        self.assertEqual(list(self.handle.steps), ["Align the beam"])
        self.assertFalse(self.handle.steps["Align the beam"].status)
        self.assertEqual(self.handle.categories["Calibration"], 2)
        self.assertEqual(self.handle.statuses["Completed"], 5)
        self.assertEqual(self.handle.category, "Calibration")
        self.assertEqual(self.handle.status, "Planned")
        self.assertGreater(len(self.remote.calls), count)
        self.assertTrue(all(t != threading.get_ident() for _, t in self.remote.calls))

    def test_step_assignment_uses_stable_id_and_does_not_wait(self):
        self.handle.refresh().result(timeout=3)
        step = self.handle.steps["Align the beam"]
        self.remote.rows[0]["text"] = "Renamed on server"
        release = threading.Event()
        self.releases.append(release)
        self.remote.edit_release = release
        step.status = True
        self.assertTrue(self.remote.edit_started.wait(2))
        self.assertFalse(step.status)
        release.set()
        self.bridge.flush(timeout=3)
        self.assertTrue(step.status)
        self.assertTrue(self.handle.steps["Renamed on server"].status)

    def test_writes_do_not_refetch_unrelated_catalogs(self):
        self.handle.refresh().result(timeout=3)
        count = sum(name == "categories" for name, _ in self.remote.calls)
        self.handle.add_step("Measure power", checked=True)
        self.handle.check_step("Align the beam")
        self.handle.category = 3
        self.handle.status = 5
        self.bridge.flush(timeout=3)
        self.assertTrue(self.handle.steps["Measure power"].status)
        self.assertTrue(self.handle.steps["Align the beam"].status)
        self.assertEqual(self.handle.category, "Measurement")
        self.assertEqual(self.handle.status, "Completed")
        self.assertEqual(sum(name == "categories" for name, _ in self.remote.calls), count)

    def test_reads_and_step_handle_stay_bound_after_switching(self):
        self.handle.refresh().result(timeout=3)
        same = self.make_handle(self.remote)
        other_remote = DictionaryExperiment(2)
        other = self.make_handle(other_remote)
        self.bridge._latest_experiment = other
        same.steps["Align the beam"].status = True
        self.bridge.flush(timeout=3)
        self.assertTrue(self.handle.steps["Align the beam"].status)
        self.assertFalse(other_remote.rows[0]["status"])
        self.assertEqual(other_remote.calls, [])
        self.assertFalse(other.steps["Align the beam"].status)

    def test_dictionary_changes_do_not_mutate_cached_choices(self):
        self.handle.refresh().result(timeout=3)
        self.handle.categories["Wrong"] = 99
        self.handle.statuses.clear()
        self.assertNotIn("Wrong", self.handle.categories)
        self.assertEqual(len(self.handle.statuses), 2)

    def test_duplicate_step_text_does_not_fail_refresh_or_a_successful_write(self):
        self.handle.refresh().result(timeout=3)
        self.handle.add_step("Align the beam", checked=True).result(timeout=3)
        self.bridge.flush(timeout=3)
        with self.assertRaises(ValueError):
            _ = self.handle.steps
        self.assertEqual(len(self.handle.steps_by_id), 2)
        self.assertFalse(self.handle.steps_by_id[7].status)
        self.assertTrue(self.handle.steps_by_id[8].status)
        self.handle.refresh().result(timeout=3)
        self.assertEqual(self.handle.categories["Calibration"], 2)
        self.handle.steps_by_id[7].status = True
        self.bridge.flush(timeout=3)
        self.assertTrue(self.handle.steps_by_id[7].status)

    def test_failed_fresh_read_reports_error_instead_of_returning_stale_snapshot(self):
        self.handle.refresh().result(timeout=3)
        self.remote._category = "Measurement"
        self.remote.fail_refresh = True
        future = self.handle.refresh()
        with self.assertRaisesRegex(RuntimeError, "refresh failed"):
            future.result(timeout=3)
        self.assertEqual(self.bridge._experiment_states[1]["category"], "Calibration")
        with self.assertRaisesRegex(RuntimeError, "refresh failed"):
            _ = self.handle.category
        self.remote.fail_refresh = False
        self.assertEqual(self.handle.category, "Measurement")
        with self.assertRaisesRegex(RuntimeError, "eLabFTW jobs failed"):
            self.bridge.flush(timeout=3)

    def test_checkbox_error_preserves_last_confirmed_value(self):
        self.handle.refresh().result(timeout=3)
        step = self.handle.steps["Align the beam"]
        self.remote.fail_edit = True
        step.status = True
        with self.assertRaisesRegex(RuntimeError, "eLabFTW jobs failed"):
            self.bridge.flush(timeout=3)
        self.assertFalse(step.status)
        self.assertFalse(self.handle.steps["Align the beam"].status)

    def test_invalid_checkbox_value_is_rejected_before_submission(self):
        with self.assertRaises(TypeError):
            self.handle.add_step("Align", checked=1)
        with self.assertRaises(TypeError):
            self.handle.check_step("Align", checked="yes")
        self.assertEqual(self.bridge._jobs, [])


if __name__ == "__main__":
    unittest.main()
