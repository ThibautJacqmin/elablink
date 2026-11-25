"""Exercise eLabFTW metadata through the generated SDK and its actual HTTP codec."""

from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import elabapi_python as ep
from urllib3.response import HTTPResponse

from elablink import Experiment, Resource


class SdkMetadataTests(unittest.TestCase):
    def bind_record(self, kind, raw_metadata):
        config = ep.Configuration()
        config.host = "https://fixture.invalid/api/v2"
        sdk = ep.ApiClient(config)
        api = SimpleNamespace(experiments=ep.ExperimentsApi(sdk), items=ep.ItemsApi(sdk))
        row = {"id": 17, "title": "Calibration", "date": "2026-11-01", "metadata": raw_metadata}
        methods = []

        def request(method, url, **options):
            self.assertEqual(urlsplit(url).path, f"/api/v2/{kind.entity_type}/17")
            methods.append(method)
            if method == "PATCH":
                payload = json.loads(options["body"])
                self.assertEqual(set(payload), {"metadata"})
                self.assertIsInstance(payload["metadata"], str)
                row.update(payload)
            else:
                self.assertEqual(method, "GET")
            return HTTPResponse(
                body=BytesIO(json.dumps(row).encode()),
                status=200,
                headers={"Content-Type": "application/json"},
                preload_content=options.get("preload_content", True),
            )

        http = patch.object(sdk.rest_client.pool_manager, "request", side_effect=request)
        http.start()
        self.addCleanup(http.stop)
        return kind(api, 17), row, methods

    def test_refresh_and_merge_preserve_wire_metadata(self):
        original = {
            "elabftw": {"extra_fields_groups": [{"id": 1, "name": "Measurement"}]},
            "extra_fields": {
                "Frequency (Hz)": {"type": "number", "value": 5, "group_id": 1, "units": ["Hz"]},
                "Original": {"type": "text", "value": "Étalon"},
            },
            "LabSettings": {"display": "expanded"},
        }
        for kind in (Experiment, Resource):
            for raw in (json.dumps(original), original):
                with self.subTest(kind=kind.__name__, wire_type=type(raw).__name__):
                    entity, row, methods = self.bind_record(kind, raw)
                    entity.refresh()
                    self.assertEqual(methods, ["GET"])
                    self.assertEqual(entity.date, "2026-11-01")
                    self.assertEqual(entity.metadata, original)
                    entity.set_extra_fields({"Frequency (Hz)": 6, "Ready": True})
                    expected = deepcopy(original)
                    expected["extra_fields"]["Frequency (Hz)"]["value"] = 6
                    expected["extra_fields"]["Ready"] = {"type": "checkbox", "value": True}
                    self.assertEqual(json.loads(row["metadata"]), expected)
                    self.assertEqual(entity.metadata, expected)
                    self.assertEqual(methods, ["GET", "GET", "PATCH", "GET"])

    def test_malformed_fields_on_wire_are_not_overwritten(self):
        for kind in (Experiment, Resource):
            for raw in ('{"extra_fields":null}', {"extra_fields": ["legacy"]}, "invalid JSON"):
                with self.subTest(kind=kind.__name__, raw=raw):
                    entity, row, methods = self.bind_record(kind, raw)
                    with self.assertRaises(ValueError):
                        entity.set_extra_fields({"Frequency (Hz)": 6})
                    self.assertEqual(methods, ["GET"])
                    self.assertEqual(row["metadata"], raw)
