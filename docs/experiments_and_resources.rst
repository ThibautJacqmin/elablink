Experiments and resources
=========================

Create and manage your eLabFTW experiments and resources directly from Python. The Experiment and Resource classes let you work with familiar notebook features: titles, main text, steps, tags, custom fields, and attachments. Changes made through these objects are saved directly to eLabFTW and can be viewed in the web interface.

Create an experiment
--------------------

.. code-block:: python

   from elablink import Experiment

   experiment = Experiment(title="Laser calibration")
   experiment.main_text = "<p>Measure the laser power.</p>"
   experiment.add_step("Align the beam")
   experiment.add_comment("The signal is stable.")
   tags = list(experiment.available_tags)
   if tags:
       experiment.add_tag(tags[0])

``Experiment()`` also works and starts with the title ``Untitled experiment``. Give each new experiment a meaningful title if unique titles are enabled in the configuration file.

``main_text`` and its alias ``body`` replace the main text.
Tags must already exist on your server; ``experiment.available_tags`` lists the choices.


Edit properties and custom fields
---------------------------------

.. code-block:: python

   experiment.title = "Blue laser calibration"
   experiment.started_on = "2026-10-25"
   experiment.set_extra_fields({"Power (mW)": 2.5, "Operator": "Thibaut"})
   experiment.add_step("Check the power", checked=True)
   experiment.steps["Align the beam"].status = True

``started_on`` (also named ``date``) is the editable date, not the creation
timestamp. ``add_step()`` creates a step; assigning its ``status`` checks or
unchecks an existing step. Repeated step names can be accessed through ``steps_by_id``.

The corresponding readable properties include:

.. code-block:: python

   print(experiment.title)
   print(experiment.steps)
   print(experiment.comments)
   print(experiment.tags)
   print(experiment.metadata)

``experiment.refresh()`` reloads record properties changed in the browser.
Category and status choices are fetched when queried. Custom fields are part of ``metadata``;
``set_extra_fields()`` merges the supplied fields by default.

Create a resource
-----------------

A resource can represent a sample, instrument or other inventory entry.
It has the same notes, steps, comments, tags, custom-field and file helpers:

.. code-block:: python

   from elablink import Resource

   laser = Resource(title="Blue laser")
   laser.main_text = "<p>Laser on the optics table.</p>"
   laser.set_extra_fields({"Wavelength (nm)": 405, "Location": "Table 2"})
   tags = list(laser.available_tags)
   if tags:
       laser.add_tag(tags[0])
   experiment.link_resource(laser.ID)

Resource categories and statuses are optional; ``laser.categories`` and
``laser.statuses`` are empty when disabled.
Existing links are not added twice.
``laser.link_experiment(experiment.ID)`` links in the other direction if needed.

Load a record instead of creating another
-----------------------------------------

``Client`` provides loading, templates and the available category/status names:

.. code-block:: python

   from elablink import Client

   client = Client()
   experiment = client.load_experiment(ID=123)
   laser = client.load_resource(ID=456)

Use your real record IDs. You can also load by exact ``title="..."``. The client
offers ``create_experiment(...)`` and ``create_resource(...)`` if you want to
reuse one configured connection for several records.

.. code-block:: python

   print(client.category_dict)
   print(client.status_dict)
   print(client.resource_category_dict)
   print(client.resource_status_dict)

   # Choose names that actually exist on your server:
   # experiment.category = "Measurement"
   # experiment.status = "Finished"

Category and status setters accept an existing name or
integer ID. To use an existing experiment template:

.. code-block:: python

   experiment = client.create_experiment_from_template(
       "Another calibration", template_name="Your template name"
   )

There is no resource-template helper.

Attach a file
-------------

.. code-block:: python

   experiment.upload_file("measurement.csv")
   print(experiment.list_files())

   attachment = experiment.get_file("measurement.csv")
   if attachment is not None:
       experiment.download_file(attachment["id"], "downloads/measurement.csv")

By default, upload replaces an attachment with the same filename. Unchanged
contents can be skipped when the server provides a compatible hash. See
:doc:`background_uploads` for the replacement limitations.

For an image in the main text:

.. code-block:: python

   image = experiment.upload_inline_image("plot.png", alt="Measured signal")
   experiment.main_text = experiment.main_text + image

The image upload returns HTML; you choose where to insert it. It does not
replace the main text automatically.

Runnable example
----------------

``examples/experiments_and_resources.py`` combines the basic operations above.
It selects existing categories, statuses and tags when available. Run it from
the project root:

.. code-block:: console

   python examples/experiments_and_resources.py

Read the example before running or importing it: it creates real records.
Each run uses a timestamped experiment title. There are no command-line arguments.

.. literalinclude:: ../examples/experiments_and_resources.py
   :language: python
