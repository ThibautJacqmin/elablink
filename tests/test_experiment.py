"""Opt-in integration tests that create real records on an eLabFTW server.

Set ELABLINK_LIVE_TESTS=1 and ELABLINK_CONFIG to a configuration file before
running. Test records are intentionally left on the server for inspection.
No team categories or statuses are created or removed.
"""

import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from elablink import Client


@unittest.skipUnless(
    os.environ.get("ELABLINK_LIVE_TESTS") == "1",
    "Live server tests require explicit ELABLINK_LIVE_TESTS=1 opt-in",
)
class TestExperimentIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = os.environ.get("ELABLINK_CONFIG")
        if not config:
            raise unittest.SkipTest(
                "Set ELABLINK_CONFIG to a test-server configuration"
            )
        cls.client = Client(config)

    @staticmethod
    def wait_for(predicate, message):
        for _ in range(10):
            if predicate():
                return
            time.sleep(0.5)
        raise AssertionError(message)

    def test_experiment_properties_and_attachment_round_trip(self):
        token = uuid.uuid4().hex
        experiment = self.client.create_experiment(f"ELABLINK TEST {token}")
        experiment.title = f"ELABLINK TEST {token} UPDATED"
        self.assertEqual(experiment.title, f"ELABLINK TEST {token} UPDATED")
        experiment.main_text = "Integration-test record; safe to archive manually."
        self.assertIn("Integration-test record", experiment.main_text)
        experiment.set_extra_fields({"Integration test": token})
        self.assertEqual(
            experiment.metadata["extra_fields"]["Integration test"]["value"], token
        )
        experiment.add_tag("elablink-integration-test")
        self.wait_for(
            lambda: experiment.has_tag("elablink-integration-test"), "Tag not visible"
        )
        experiment.add_step("Check the upload")
        self.wait_for(
            lambda: "Check the upload" in experiment.steps, "Step not visible"
        )
        experiment.add_comment("Offline tests have already passed.")
        self.wait_for(
            lambda: "Offline tests have already passed." in experiment.comments,
            "Comment not visible",
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / f"elablink-{token}.txt"
            source.write_text("original", encoding="utf-8")
            experiment.add_file(str(source))
            self.wait_for(
                lambda: experiment.get_file(source.name) is not None, "Upload missing"
            )
            source.write_text("updated", encoding="utf-8")
            experiment.upsert_file(str(source))
            upload = experiment.get_file(source.name)
            downloaded = Path(temporary) / "downloaded.txt"
            experiment.download_file(upload["id"], downloaded)
            self.assertEqual(downloaded.read_text(encoding="utf-8"), "updated")

    def test_resource_properties(self):
        category = os.environ.get("ELABLINK_RESOURCE_CATEGORY_ID")
        if not category:
            self.skipTest("Set ELABLINK_RESOURCE_CATEGORY_ID for live resource tests")
        resource = self.client.create_resource(
            f"ELABLINK TEST RESOURCE {uuid.uuid4().hex}", category=int(category)
        )
        resource.main_text = "Integration-test resource; safe to archive manually."
        resource.set_extra_fields({"Integration test": True})
        self.assertIn("Integration-test resource", resource.main_text)
        self.assertTrue(resource.metadata["extra_fields"]["Integration test"]["value"])


if __name__ == "__main__":
    unittest.main()
