"""Create and link an experiment and a resource in eLabFTW.

This example creates real records on your server and selects existing choices
for categories, statuses and tags when available.
A timestamp distinguishes repeated runs.

Author: Thibaut Jacqmin
"""

from datetime import date, datetime

from elablink import Experiment, Resource


# An experiment is the notebook entry for one piece of work.
experiment = Experiment(title=f"First measurement {datetime.now():%Y-%m-%d %H:%M:%S.%f}")
categories = list(experiment.categories)
statuses = list(experiment.statuses)
tags = list(experiment.available_tags)
print("Experiment categories:", categories)
print("Experiment statuses:", statuses)
print("Available tags:", tags)
# Select the first available choice.
if categories:
    experiment.category = categories[0]
if statuses:
    experiment.status = statuses[0]
experiment.main_text = "<p>Characterisation of Sample A.</p>"
experiment.started_on = date.today()
experiment.set_extra_fields({"Temperature (K)": 4.2})
experiment.add_step("Cool the sample")
experiment.steps["Cool the sample"].status = True
experiment.add_step("Record the signal", checked=True)
experiment.add_comment("The signal is stable.")
if tags:
    experiment.add_tag(tags[0])

# A resource describes something used in the lab: a sample, laser, etc.
sample = Resource(title="Sample A")
categories = list(sample.categories)
statuses = list(sample.statuses)
tags = list(sample.available_tags)
print("Resource categories:", categories)
print("Resource statuses:", statuses)
if categories:
    sample.category = categories[0]
if statuses:
    sample.status = statuses[0]
sample.main_text = "<p>A sample used for the measurement.</p>"
sample.set_extra_fields({"Serial number": "SAMPLE-001"})
if tags:
    sample.add_tag(tags[0])
sample.add_comment("Ready for use.")

# These links are also visible in the eLabFTW interface.
experiment.link_resource(sample.ID)
sample.link_experiment(experiment.ID)

print(f"Created experiment {experiment.ID} and resource {sample.ID}.")
