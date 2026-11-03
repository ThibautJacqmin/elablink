"""Create and link an experiment and a resource in eLabFTW.

This example creates real records on your server. Replace the category ID
and category/status names with choices from your installation (printed below).
Choose a new experiment title on each run if unique titles are enabled.

Author: Thibaut Jacqmin
"""

from datetime import date

from elablink import Experiment, Resource


resource_category = 3  # Replace with your resource category ID.

# An experiment is the notebook entry for one piece of work.
experiment = Experiment(title="First measurement")
print("Experiment categories:", experiment.categories)
print("Experiment statuses:", experiment.statuses)
# Replace these names with ones shown above.
experiment.category = experiment.categories["Calibration"]
experiment.status = experiment.statuses["Completed"]
experiment.main_text = "<p>Characterisation of Sample A.</p>"
experiment.started_on = date.today()
experiment.set_extra_fields({"Temperature (K)": 4.2})
experiment.add_step("Cool the sample")
experiment.steps["Cool the sample"].status = True
experiment.add_step("Record the signal", checked=True)
experiment.add_comment("The signal is stable.")
experiment.add_tag("calibration")

# A resource describes something used in the lab: a sample, laser, etc.
sample = Resource(title="Sample A", category=resource_category)
print("Resource categories:", sample.categories)
print("Resource statuses:", sample.statuses)
# Replace these names with ones shown above.
sample.category = sample.categories["Samples"]
sample.status = sample.statuses["Available"]
sample.main_text = "<p>A sample used for the measurement.</p>"
sample.set_extra_fields({"Serial number": "SAMPLE-001"})
sample.add_tag("sample")
sample.add_comment("Ready for use.")

# These links are also visible in the eLabFTW interface.
experiment.link_resource(sample.ID)
sample.link_experiment(experiment.ID)

print(f"Created experiment {experiment.ID} and resource {sample.ID}.")
