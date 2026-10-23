# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import urllib3
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ElabClient import ElabClient


path_to_conf_file = 'C:/Users/ThibautJacqmin/Documents/Lkb/Elab API key'
CONFIG_PATH = path_to_conf_file + '/elab_server.conf'


class TestElabExperimentIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        cls.client = ElabClient(CONFIG_PATH)
        cls.team_id = cls.client._get_team_id()
        token = uuid.uuid4().hex[:8]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        cls.token = f"{timestamp}-{token}"
        cls.category_name = f"ELABMATE CAT {cls.token}"
        cls.status_name = f"ELABMATE STATUS {cls.token}"

        cls.client.categories.post_team_one_expcat(
            cls.team_id,
            body={"name": cls.category_name, "color": "00AEEF", "default": 0},
        )
        cls.category_id = None
        cls._wait_for(
            lambda: cls._capture_category_id(),
            "Category not visible after creation.",
        )

        cls.client.statuses.post_team_one_expstatus(
            cls.team_id,
            body={"name": cls.status_name, "color": "00AEEF", "default": 0},
        )
        cls.status_id = None
        cls._wait_for(
            lambda: cls._capture_status_id(),
            "Status not visible after creation.",
        )

        cls.exp = cls.client.create_experiment(
            title=f"ELABMATE TEST {cls.token}",
            category=cls.category_id,
        )

    @staticmethod
    def _wait_for(predicate, message: str, retries: int = 10, delay: float = 0.5):
        last_error: Exception | None = None
        for _ in range(retries):
            try:
                if predicate():
                    return
            except Exception as exc:  # pragma: no cover - best-effort diagnostics
                last_error = exc
            time.sleep(delay)
        if last_error:
            raise AssertionError(f"{message} (last error: {last_error})")
        raise AssertionError(message)

    @classmethod
    def _get_statuslike_id(cls, items, name: str):
        for item in items or []:
            if isinstance(item, dict):
                title = item.get("title")
                item_id = item.get("id")
            else:
                title = getattr(item, "title", None)
                item_id = getattr(item, "id", None)
            if title == name:
                return item_id
        return None

    @classmethod
    def _capture_category_id(cls) -> bool:
        items = cls.client.categories.read_team_experiments_categories(cls.team_id)
        cls.category_id = cls._get_statuslike_id(items, cls.category_name)
        return cls.category_id is not None

    @classmethod
    def _capture_status_id(cls) -> bool:
        items = cls.client.statuses.read_team_experiments_status(cls.team_id)
        cls.status_id = cls._get_statuslike_id(items, cls.status_name)
        return cls.status_id is not None

    def _get_file_with_retry(self, filename: str, retries: int = 10, delay: float = 0.5):
        for _ in range(retries):
            meta = self.exp.get_file(filename)
            if meta is not None:
                return meta
            time.sleep(delay)
        self.fail(f"Upload '{filename}' not found after {retries} retries.")

    def test_end_to_end(self) -> None:
        exp = self.exp
        token = self.token

        # Title
        new_title = f"ELABMATE TEST {token} UPDATED"
        exp.title = new_title
        self.assertEqual(exp.title, new_title)

        # Main text
        body_text = f"Integration body {token}"
        exp.main_text = body_text
        self.assertEqual(exp.main_text, body_text)

        # Tags
        tag = f"elabmate-{token}"
        exp.add_tag(tag)
        self._wait_for(lambda: tag in exp.tags, "Tag not visible after add.")
        self.assertTrue(exp.has_tag(tag))
        exp.remove_tag(tag)
        self._wait_for(lambda: tag not in exp.tags, "Tag still visible after remove.")

        # Steps
        step_text = f"Step {token}"
        exp.add_step(step_text)
        self._wait_for(lambda: step_text in exp.steps, "Step not visible after add.")

        # Comments
        comment_text = f"Comment {token}"
        exp.add_comment(comment_text)
        self._wait_for(lambda: comment_text in exp.comments, "Comment not visible after add.")

        # Category and status
        exp.category = self.category_name
        self.assertEqual(exp.category, self.category_name)
        exp.status = self.status_name
        self.assertEqual(exp.status, self.status_name)

        # File upload -> replace -> download
        current_dir = Path.cwd()
        filename = f"elabmate_{token}.txt"
        source_path = current_dir / filename
        original_text = f"original {token}"
        source_path.write_text(original_text, encoding="utf-8")
        exp.add_file(str(source_path), comment="integration test upload")

        meta = self._get_file_with_retry(filename)
        file_id = meta["id"]
        self.assertIsNotNone(file_id)

        updated_text = f"updated {token}"
        source_path.write_text(updated_text, encoding="utf-8")
        exp.upsert_file(str(source_path))

        meta = self._get_file_with_retry(filename)
        file_id = meta["id"]

        download_path = current_dir / f"elabmate_{token}_download.txt"
        exp.download_file(file_id=file_id, destination=download_path)
        downloaded = download_path.read_text(encoding="utf-8")
        self.assertEqual(downloaded, updated_text)

        # Local cleanup (leave server data as-is for manual inspection).
        for path in (source_path, download_path):
            try:
                path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
