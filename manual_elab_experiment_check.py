# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ElabClient import ElabClient


def _pause(message: str) -> None:
    input(f"\n[CHECK] {message}\nPress Enter to continue...")


def main() -> int:
    path_to_conf_file = 'C:/Users/ThibautJacqmin/Documents/Lkb/Elab API key'
    client = ElabClient(path_to_conf_file + '/elab_server.conf')

    suffix = uuid.uuid4().hex[:6]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    title = f"ELABMATE INTEGRATION {timestamp} {suffix}"

    print("Creating experiment...")
    exp = client.create_experiment(title=title)
    print(f"Created experiment ID={exp.ID} title='{exp.title}'")
    _pause("Verify the experiment is visible in ElabFTW.")

    exp.main_text = "Integration test body (elabmate)."
    _pause("Verify the main text/body was updated.")

    exp.add_tag("elabmate-integration")
    _pause("Verify the tag was added.")

    exp.add_step("Integration test step 1.")
    _pause("Verify the step was added.")

    exp.add_comment("Integration test comment.")
    _pause("Verify the comment was added.")

    temp_dir = Path(os.getenv("ELAB_TMP_DIR", "."))
    temp_dir.mkdir(parents=True, exist_ok=True)
    attachment = temp_dir / f"elabmate_integration_{suffix}.txt"
    attachment.write_text("Integration test attachment.")
    exp.add_file(str(attachment), comment="integration test file")
    _pause("Verify the attachment was uploaded.")

    exp.remove_tag("elabmate-integration")
    _pause("Verify the tag was removed.")

    category = os.getenv("ELAB_CATEGORY")
    if category:
        exp.category = category
        _pause(f"Verify the category was set to '{category}'.")

    status = os.getenv("ELAB_STATUS")
    if status:
        exp.status = status
        _pause(f"Verify the status was set to '{status}'.")

    exp.title = f"{title} UPDATED"
    _pause("Verify the title was updated.")

    print("\nDone. Consider archiving or deleting the test experiment in the UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
