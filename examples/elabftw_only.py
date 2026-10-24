"""Create a real experiment and resource when explicitly run.

Importing this file does not connect to eLabFTW. Running it writes to the server
specified in your configuration; use a test account while exploring the example.

Author: Thibaut Jacqmin
"""

import argparse
from datetime import date

from elabmate import ElabClient


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="elabmate.conf")
    parser.add_argument("--title", default="First measurement")
    parser.add_argument(
        "--resource-category",
        type=int,
        required=True,
        help="Numeric resource category ID from your eLabFTW installation.",
    )
    args = parser.parse_args(argv)

    client = ElabClient(args.config)
    experiment = client.create_experiment(args.title)
    experiment.main_text = "<p>Characterisation of a new sample.</p>"
    experiment.started_on = date.today()
    experiment.set_extra_fields({"Temperature (K)": 4.2, "Operator": "Thibaut"})
    experiment.add_step("Cool the sample")
    experiment.add_comment("The signal is stable.")
    experiment.add_tag("calibration")

    sample = client.create_resource(
        f"Sample for {args.title}", category=args.resource_category
    )
    sample.main_text = "<p>A sample used for the measurement.</p>"
    sample.set_extra_fields({"Serial number": "SAMPLE-001"})
    sample.add_tag("sample")
    experiment.link_resource(sample.ID)
    sample.link_experiment(experiment.ID)

    print(f"Created experiment {experiment.ID} and resource {sample.ID}.")
    return experiment, sample


if __name__ == "__main__":
    main()
