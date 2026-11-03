"""Opt-in live coverage of the direct eLabFTW interface.

Set ELABLINK_LIVE_TESTS=1; configuration is discovered normally, or overridden
with ELABLINK_CONFIG. Uniquely named test records remain for inspection.
Existing team categories, statuses, tags and templates are only read/reused.
"""

import base64
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from elablink import (
    DuplicateTitle, Experiment, InvalidCategory, InvalidStatus, InvalidTag,
    InvalidTemplate, Resource,
)
from tests.live_support import LiveServerTestCase


@unittest.skipUnless(os.environ.get("ELABLINK_LIVE_TESTS") == "1",
                     "Live tests require ELABLINK_LIVE_TESTS=1")
class TestExperimentIntegration(LiveServerTestCase):
    def entities(self, label):
        """Create one record of each kind independently within each subtest."""
        return (("experiment", lambda: self.new_experiment(label)),
                ("resource", lambda: self.new_resource(label)))

    def test_properties_and_metadata(self):
        for kind, create in self.entities("properties"):
            with self.subTest(kind=kind):
                entity = create()
                entity.title += " UPDATED"
                title = entity.title
                entity.main_text = "<p>Live integration test: initial text.</p>"
                self.assertEqual(entity.body, entity.main_text)
                entity.body = "<p>Live integration test: changed text.</p>"
                self.assertIn("changed text", entity.main_text)
                entity.date = date(2026, 1, 2)
                self.assertTrue(str(entity.started_on).startswith("2026-01-02"))
                entity.started_on = "2026-01-03"
                self.assertTrue(str(entity.date).startswith("2026-01-03"))
                self.assertIsNotNone(entity.creation_date)
                self.assertIsNotNone(entity.last_modification)
                entity.metadata = {"extra_fields": {"Original": {"type": "text", "value": "keep"}}}
                entity.set_extra_fields({"Temperature": 4.2, "Ready": True,
                                         "Measured": date(2026, 1, 3)})
                fields = entity.metadata["extra_fields"]
                self.assertEqual(fields["Original"]["value"], "keep")
                self.assertEqual(float(fields["Temperature"]["value"]), 4.2)
                self.assertIn(str(fields["Ready"]["value"]).lower(), ("true", "1", "on"))
                self.assertEqual(fields["Measured"]["value"], "2026-01-03")
                entity.set_extra_fields({"Only": "replacement"}, merge=False)
                self.assertEqual(set(entity.metadata["extra_fields"]), {"Only"})
                entity.metadata = json.dumps({"extra_fields": {"JSON": {"type": "text", "value": "ok"}}})
                entity.refresh()
                self.assertEqual(entity.title, title)
                self.assertEqual(entity.metadata["extra_fields"]["JSON"]["value"], "ok")
                self.assertIn(str(entity.ID), repr(entity))

    def test_categories_statuses_and_invalid_choices(self):
        for kind, create in self.entities("choices"):
            with self.subTest(kind=kind):
                entity = create()
                for field, catalog, error in (
                    ("category", entity.categories, InvalidCategory),
                    ("status", entity.statuses, InvalidStatus),
                ):
                    with self.subTest(kind=kind, field=field):
                        if not catalog:
                            before = getattr(entity, field)
                            for value in (f"missing-{self.run_id}", 1000000):
                                with self.assertRaisesRegex(error, "feature may be disabled"):
                                    setattr(entity, field, value)
                            entity.refresh()
                            self.assertEqual(getattr(entity, field), before)
                            continue
                        name, ID = next(iter(catalog.items()))
                        setattr(entity, field, name)
                        self.assertIn(getattr(entity, field), (name, ID, str(ID)))
                        setattr(entity, field, ID)
                        self.assertIn(getattr(entity, field), (name, ID, str(ID)))
                        invalid = max(int(value) for value in catalog.values()) + 1000000
                        before = getattr(entity, field)
                        for value in (f"missing-{self.run_id}", invalid):
                            with self.assertRaises(error) as raised:
                                setattr(entity, field, value)
                            self.assertIn(name, str(raised.exception))
                        entity.refresh()
                        self.assertEqual(getattr(entity, field), before)

    def test_tags_comments_and_unknown_tag(self):
        for kind, create in self.entities("tags-comments"):
            with self.subTest(kind=kind):
                entity = create()
                available = entity.available_tags
                missing = f"missing-tag-{self.run_id}"
                with self.assertRaises(InvalidTag) as raised:
                    entity.add_tag(missing)
                self.assertIn("Available tags:", str(raised.exception))
                self.assertNotIn(missing, entity.tags)
                if available:
                    tag = next(iter(available))
                    entity.add_tag(tag)
                    entity.refresh()
                    self.assertTrue(entity.has_tag(tag))
                    self.assertIn(tag, entity.tags)
                else:
                    with self.subTest(kind=kind, feature="attach existing tag"):
                        self.skipTest("No existing team tags available")
                comment = f"Live integration test comment {self.run_id}"
                entity.add_comment(comment)
                entity.refresh()
                self.assertIn(comment, entity.comments)

    def test_checkbox_steps_and_duplicate_labels(self):
        for kind, create in self.entities("steps"):
            with self.subTest(kind=kind):
                entity = create()
                first = entity.add_step("Align the laser")
                self.assertFalse(first.status)
                entity.steps["Align the laser"].status = True
                entity.refresh()
                self.assertTrue(entity.steps_by_id[first.ID].status)
                entity.check_step(first.ID, checked=True)
                entity.refresh()
                self.assertTrue(entity.steps_by_id[first.ID].status)
                entity.check_step("Align the laser", checked=False)
                entity.refresh()
                self.assertFalse(entity.steps["Align the laser"].status)
                second = entity.add_step("Acquire a sinusoid", checked=True)
                entity.refresh()
                self.assertTrue(entity.steps_by_id[second.ID].status)
                duplicate = entity.add_step("Align the laser", checked=True)
                self.assertNotEqual(first.ID, duplicate.ID)
                with self.assertRaises(ValueError):
                    _ = entity.steps
                entity.check_step("Align the laser", checked=False, occurrence=1)
                entity.refresh()
                self.assertFalse(entity.steps_by_id[duplicate.ID].status)
                self.assertEqual(len(entity.steps_by_id), 3)
                with self.assertRaises(KeyError):
                    entity.check_step("does not exist")
                self.assertEqual(len(entity.steps_by_id), 3)

    def test_loading_constructors_and_unique_titles(self):
        for kind, create in self.entities("load"):
            with self.subTest(kind=kind):
                entity = create()
                loader = self.client.load_resource if kind == "resource" else self.client.load_experiment
                self.assertEqual(loader(ID=entity.ID).ID, entity.ID)
                self.assertEqual(loader(title=entity.title).ID, entity.ID)
                entity_type = Resource if kind == "resource" else Experiment
                with patch("elablink.client.Client", side_effect=self.make_client):
                    opened = entity_type(ID=entity.ID, config_path=self.config_path)
                self.assertEqual(opened.ID, entity.ID)
                if kind == "experiment" and self.client.enforce_unique_titles:
                    with self.assertRaises(DuplicateTitle):
                        self.client.create_experiment(entity.title)
        choices = self.client.resource_category_dict
        with self.subTest(constructor="Experiment"):
            entity = Experiment(api=self.client, title=self.unique_title("constructor"))
            self.track_record(entity)
            self.assertEqual(self.client.load_experiment(ID=entity.ID).title, entity.title)
        with self.subTest(constructor="Resource"):
            entity = Resource(api=self.client, title=self.unique_title("constructor-resource"),
                              category=next(iter(choices.values()), None))
            self.track_record(entity, "resource")
            self.assertEqual(self.client.load_resource(ID=entity.ID).title, entity.title)

    def test_template_creation_and_unknown_template(self):
        with self.assertRaises(InvalidTemplate):
            self.client.create_experiment_from_template(
                self.unique_title("missing-template"), f"missing-template-{self.run_id}")
        templates = self.client.templates.read_experiments_templates() or []
        if not templates:
            self.skipTest("No accessible experiment template exists")
        title = self.unique_title("template")
        entity = self.client.create_experiment_from_template(title, templates[0].title)
        self.track_record(entity)
        self.assertEqual(entity.title, title)

    def test_links_are_visible_and_idempotent(self):
        experiment = self.new_experiment("links-A")
        other = self.new_experiment("links-B")
        resource = self.new_resource("links-A")
        other_resource = self.new_resource("links-B")
        for owner, method, target in (
            (experiment, "link_resource", resource),
            (experiment, "link_experiment", other),
            (resource, "link_experiment", experiment),
            (resource, "link_resource", other_resource),
        ):
            with self.subTest(owner=owner.entity_type, method=method):
                kind = "items" if method == "link_resource" else "experiments"
                existing = target.ID in owner._linked_ids(kind)
                self.assertEqual(getattr(owner, method)(target.ID), not existing)
                self.assertFalse(getattr(owner, method)(target.ID))
                self.assertIn(target.ID, owner._linked_ids(kind))

    def test_attachment_upload_replacement_download_and_inline_image(self):
        for kind, create in self.entities("files"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                entity = create()
                root = Path(temporary)
                source = root / "measurement.txt"
                source.write_bytes(b"original measured signal")
                entity.add_file(str(source), comment="Live integration attachment")
                upload = entity.get_file(source.name)
                self.assertIsNotNone(upload)
                upload_id = upload["id"]
                self.assertTrue(entity.get_files())
                self.assertIsNone(entity.get_file("missing.txt"))
                entity.upload_file(str(source))
                self.assertEqual(entity.get_file(source.name)["id"], upload_id)
                source.write_bytes(b"updated measured signal - live test")
                entity.upsert_file(str(source), comment="Live integration replacement")
                current = entity.get_file(source.name)
                self.assertNotEqual(current["id"], upload_id)
                self.assertEqual(len([row for row in entity.list_files()
                                      if row["real_name"] == source.name]), 1)
                destination = entity.download_file(current["id"], root / "download" / "measurement.txt")
                self.assertEqual(destination.read_bytes(), source.read_bytes())
                self.assertEqual(self.client.download_upload(entity.entity_type, entity.ID,
                                                             current["id"]), source.read_bytes())
                url = entity.uploaded_file_url(source.name)
                self.assertIn("/app/download.php?", url)
                with self.assertRaises(FileNotFoundError):
                    entity.uploaded_file_url("missing.txt")
                image = root / "pixel.png"
                image.write_bytes(base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgaPgPAAIDAYAkYfWXAAAAAElFTkSuQmCC"))
                with Image.open(image) as decoded:
                    decoded.verify()
                html = entity.upload_inline_image(image, alt='Test "image" & signal')
                self.assertIn("<img ", html)
                self.assertIn("&quot;image&quot; &amp; signal", html)
                self.assertEqual(html, entity.inline_image_html(image.name, alt='Test "image" & signal'))
                entity.main_text = f"<p>Inline live-test image</p>{html}"
                entity.refresh()
                self.assertIn("<img", entity.main_text)
                downloaded_image = entity.download_file(entity.get_file(image.name)["id"],
                                                        root / "download" / image.name)
                self.assertEqual(downloaded_image.read_bytes(), image.read_bytes())
                with Image.open(downloaded_image) as decoded:
                    self.assertEqual(decoded.format, "PNG")
                    decoded.verify()
                with Image.open(downloaded_image) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.size, (1, 1))

    def test_hash_avoids_unchanged_transfer_when_server_supports_it(self):
        for kind, create in self.entities("hash"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                entity = create()
                source = Path(temporary) / "identical.txt"
                source.write_bytes(b"Identical content for hash comparison")
                entity.upload_file(str(source))
                row = entity.get_file(source.name)
                if not row.get("hash") or (row.get("hash_algorithm") or "").lower() not in ("", "sha256"):
                    self.skipTest("Server does not expose a compatible SHA-256 upload hash")
                with (
                    patch.object(self.client.uploads, "post_upload",
                                 wraps=self.client.uploads.post_upload) as create,
                    patch.object(self.client, "replace_upload",
                                 wraps=self.client.replace_upload) as replace,
                ):
                    entity.upload_file(str(source))
                    create.assert_not_called()
                    replace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
