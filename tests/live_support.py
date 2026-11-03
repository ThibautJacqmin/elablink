"""Shared support for explicitly enabled, real-server integration tests.

Only newly created test records are edited. Records and artifacts are kept for
inspection; API credentials and raw response headers are never reported.
"""

import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from elablink import Client


class LiveServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("ELABLINK_LIVE_TESTS") != "1":
            raise unittest.SkipTest("Set ELABLINK_LIVE_TESTS=1 to contact the real server")
        cls.run_id = os.environ.get("ELABLINK_LIVE_RUN_ID") or uuid.uuid4().hex[:12]
        cls.report_dir = Path(os.environ.get("ELABLINK_LIVE_REPORT_DIR") or
                              tempfile.mkdtemp(prefix="elablink-live-"))
        cls.report_dir.mkdir(parents=True, exist_ok=True)
        cls.records = []
        cls.client = cls.make_client()
        cls.config_path = cls.client.config_path

    @classmethod
    def make_client(cls, config_path=None):
        client = Client(config_path or os.environ.get("ELABLINK_CONFIG") or None)
        original_call = client.api_client.call_api

        def bounded_call(*args, **kwargs):
            kwargs.setdefault("_request_timeout", (5, 30))
            return original_call(*args, **kwargs)

        client.api_client.call_api = bounded_call
        return client

    new_client = make_client

    @classmethod
    def unique_title(cls, label):
        return f"ELABLINK TEST {cls.run_id} {label} {uuid.uuid4().hex[:6]}"

    @classmethod
    def track_record(cls, entity_or_info, kind="experiment"):
        if isinstance(entity_or_info, dict):
            record = dict(entity_or_info)
            record["kind"] = kind
        else:
            entity = entity_or_info
            host = cls.client.configuration.host.rstrip("/")
            if host.endswith("/api/v2"):
                host = host[:-7]
            page = "database.php" if kind == "resource" else "experiments.php"
            record = {"kind": kind, "id": entity.ID, "title": entity.title,
                      "url": f"{host}/{page}?mode=view&id={entity.ID}"}
        if not any(row["id"] == record["id"] and row["kind"] == kind
                   for row in cls.records):
            cls.records.append(record)
        cls._save_records()
        return entity_or_info

    remember = track_record

    @classmethod
    def _save_records(cls):
        destination = cls.report_dir / f"{cls.__name__}-records.json"
        destination.write_text(json.dumps({"run_id": cls.run_id,
                                           "records": cls.records}, indent=2),
                               encoding="utf-8")

    def new_experiment(self, label):
        entity = self.client.create_experiment(self.unique_title(label))
        return self.track_record(entity)

    def new_resource(self, label, category=None):
        if category is None:
            choices = self.client.resource_category_dict
            category = next(iter(choices.values()), None)
        entity = self.client.create_resource(self.unique_title(label), category=category)
        return self.track_record(entity, "resource")

    @classmethod
    def tearDownClass(cls):
        cls._save_records()
