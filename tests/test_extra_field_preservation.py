"""Custom-field updates preserve current server metadata and field definitions."""

from copy import deepcopy
from datetime import date, datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from elablink import Experiment, Resource


class ExtraFieldPreservationTests(unittest.TestCase):
    def entity(self, kind=Experiment, metadata=None):
        server = {"metadata": json.dumps(metadata or {}), "title": "Calibration"}
        api = Mock()
        route = api.items if kind is Resource else api.experiments
        get = route.get_item if kind is Resource else route.get_experiment
        patch = route.patch_item if kind is Resource else route.patch_experiment
        get.side_effect = lambda ID: SimpleNamespace(to_dict=lambda: deepcopy(server))
        patch.side_effect = lambda *, id, body: server.update(deepcopy(body))
        entity = kind(api, 17)
        entity.refresh()
        return entity, server, get, patch

    def test_merge_keeps_fields_added_in_gui_after_python_object_was_loaded(self):
        for kind in (Experiment, Resource):
            with self.subTest(kind=kind):
                entity, server, _, _ = self.entity(kind, {"extra_fields": {
                    "Operator": {"type": "text", "value": "Alice"},
                }})
                latest = json.loads(server["metadata"])
                latest["extra_fields"]["Sample ID"] = {"type": "text", "value": "wafer-42"}
                latest["elabftw"] = {"extra_fields_groups": [{"id": 1, "name": "Sample"}]}
                server["metadata"] = json.dumps(latest)
                entity.set_extra_fields({"Temperature": 4.2})
                expected = deepcopy(latest)
                expected["extra_fields"]["Temperature"] = {"type": "number", "value": 4.2}
                self.assertEqual(json.loads(server["metadata"]), expected)
                self.assertEqual(entity.metadata, expected)

    def test_plain_value_updates_preserve_units_options_and_other_field_settings(self):
        definitions = {
            "Temperature": {"type": "number", "value": "4.0", "unit": "K", "required": True},
            "Phase": {"type": "select", "value": "setup", "options": ["setup", "measurement"], "group_id": 1},
            "Unchanged": {"type": "text", "value": "keep", "description": "A note"},
        }
        for kind in (Experiment, Resource):
            with self.subTest(kind=kind):
                entity, server, _, _ = self.entity(kind, {"extra_fields": definitions})
                entity.set_extra_fields({"Temperature": 4.2, "Phase": "measurement"})
                expected = deepcopy(definitions)
                expected["Temperature"]["value"] = 4.2
                expected["Phase"]["value"] = "measurement"
                self.assertEqual(json.loads(server["metadata"])["extra_fields"], expected)

    def test_explicit_definition_changes_keep_settings_not_supplied(self):
        entity, server, _, _ = self.entity(metadata={"extra_fields": {
            "Temperature": {"type": "number", "value": 4, "unit": "K", "required": True},
            "Phase": {"type": "text", "value": "setup", "description": "Measurement stage"},
        }})
        change = {
            "Temperature": {"value": 20, "unit": "degC"},
            "Phase": {"type": "select", "value": "measurement", "options": ["setup", "measurement"]},
        }
        before = deepcopy(change)
        entity.set_extra_fields(change)
        self.assertEqual(json.loads(server["metadata"])["extra_fields"]["Temperature"],
                         {"type": "number", "value": 20, "unit": "degC", "required": True})
        self.assertEqual(json.loads(server["metadata"])["extra_fields"]["Phase"],
                         {"type": "select", "value": "measurement", "options": ["setup", "measurement"],
                          "description": "Measurement stage"})
        self.assertEqual(change, before)

    def test_new_fields_infer_types_and_existing_date_values_are_serialized(self):
        entity, server, _, _ = self.entity(metadata={"extra_fields": {
            "Measured": {"type": "date", "value": "2020-01-01", "required": True},
        }})
        entity.set_extra_fields({"Measured": date(2026, 11, 6), "Ready": True,
                                 "When": datetime(2026, 11, 6, 14, 30), "Name": "sample"})
        self.assertEqual(json.loads(server["metadata"])["extra_fields"], {
            "Measured": {"type": "date", "value": "2026-11-06", "required": True},
            "Ready": {"type": "checkbox", "value": True},
            "When": {"type": "datetime-local", "value": "2026-11-06T14:30:00"},
            "Name": {"type": "text", "value": "sample"},
        })

    def test_explicit_replace_still_replaces_all_metadata(self):
        entity, server, _, _ = self.entity(metadata={"elabftw": {"display_main_text": True}, "extra_fields": {
            "Temperature": {"type": "number", "value": 4, "unit": "K"},
        }})
        entity.set_extra_fields({"Only": "new value"}, merge=False)
        self.assertEqual(json.loads(server["metadata"]), {"extra_fields": {
            "Only": {"type": "text", "value": "new value"},
        }})

    def test_failed_refresh_does_not_write_stale_metadata(self):
        entity, server, get, patch = self.entity(metadata={"extra_fields": {"Old": {"value": "keep"}}})
        before = deepcopy(server)
        get.side_effect = OSError("server unavailable")
        with self.assertRaisesRegex(OSError, "server unavailable"):
            entity.set_extra_fields({"Temperature": 4.2})
        patch.assert_not_called()
        self.assertEqual(server, before)

    def test_malformed_current_metadata_is_not_overwritten(self):
        for raw in ('not JSON', '[]', '{"extra_fields": []}', '{"extra_fields": {"Broken": "not an object"}}'):
            with self.subTest(raw=raw):
                entity, server, _, patch = self.entity()
                server["metadata"] = raw
                with self.assertRaises(ValueError):
                    entity.set_extra_fields({"Temperature": 4.2})
                patch.assert_not_called()
                self.assertEqual(server["metadata"], raw)
