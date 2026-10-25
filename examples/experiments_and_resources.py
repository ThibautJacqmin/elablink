"""Create a sample resource and its measurement experiment in eLabFTW.

Run from the project folder, for example:
    python examples/experiments_and_resources.py --resource-category 7

Replace 7 with a resource category ID from your own eLabFTW installation.
This example writes to the server configured in elablink.conf.
Choose a new --title when running again if unique experiment titles are enabled.

Author: Thibaut Jacqmin
"""

import argparse
from datetime import date

from elablink import Client


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="elablink.conf")
    parser.add_argument("--title", default="First measurement")
    parser.add_argument(
        "--resource-category",
        type=int,
        required=True,
        help="Resource category ID shown by your eLabFTW installation.",
    )
    args = parser.parse_args(argv)

    client = Client(args.config)

    # An experiment is the notebook entry for one piece of work.
    experiment = client.create_experiment(args.title)
    experiment.main_text = "<p>Characterisation of Sample A.</p>"
    experiment.started_on = date.today()
    experiment.set_extra_fields({"Temperature (K)": 4.2})
    experiment.add_step("Cool the sample")
    experiment.add_comment("The signal is stable.")
    experiment.add_tag("calibration")

    # A resource describes something used in the lab: a sample, laser, etc.
    sample = client.create_resource("Sample A", category=args.resource_category)
    sample.main_text = "<p>A sample used for the measurement.</p>"
    sample.set_extra_fields({"Serial number": "SAMPLE-001"})
    sample.add_tag("sample")

    # These links are also visible in the eLabFTW interface.
    experiment.link_resource(sample.ID)
    sample.link_experiment(experiment.ID)

    print(f"Created experiment {experiment.ID} and resource {sample.ID}.")
    return experiment, sample


if __name__ == "__main__":
    main()
