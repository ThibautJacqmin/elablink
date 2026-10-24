Work directly with eLabFTW
==========================

Use this interface for experiment records, sample inventories and other
eLabFTW tasks **without Labmate**. Calls are synchronous: the next Python line
waits for the server. The examples below create or modify real server records
when you run them.

An experiment and its notes
---------------------------

.. code-block:: python

   from elabmate import ElabClient

   client = ElabClient()
   experiment = client.create_experiment("Sample A — first measurement")
   experiment.main_text = "<p>Measure the sample at room temperature.</p>"
   experiment.started_on = "2026-10-24"
   experiment.add_step("Connect the sample")
   experiment.ensure_step("Check the signal", finished=True)
   experiment.add_comment("The signal is stable.")
   experiment.add_tag("calibration")
   experiment.set_extra_fields({"Temperature (K)": 295, "Sample": "A"})

``main_text`` (also named ``body``) replaces the main text; it does not append a
comment. Use ``add_comment()`` for a separate note. ``started_on`` changes the
editable date, not the immutable creation timestamp.

Running creation twice with the default unique-title setting raises
``DuplicateTitle``. To continue working on an existing record, load it instead:

.. code-block:: python

   experiment = client.load_experiment(ID=123)  # Use your record's ID.
   # Or: experiment = client.load_experiment(title="Sample A — first measurement")
   print(experiment.title)
   print(experiment.steps)
   print(experiment.comments)
   print(experiment.tags)
   print(experiment.metadata)

Properties are locally cached after fetching. ``experiment.refresh()`` fetches
the current server state, for example after somebody edits the record in a
browser.

Categories, statuses and templates
----------------------------------

Discover the names and IDs available to your team before assigning them:

.. code-block:: python

   print(client.category_dict)
   print(client.status_dict)
   # Then choose names that actually exist on your server:
   # experiment.category = "Measurement"
   # experiment.status = "Finished"

Both setters accept an existing name or integer ID. To use a template already
defined on your server:

.. code-block:: python

   experiment = client.create_experiment_from_template(
       "Sample B — first measurement",
       template_name="Your existing template name",
   )

A resource, for example a sample
--------------------------------

Resources use the same notes, steps, comments, tags, custom-field, attachment and
link helpers as experiments. Resource category IDs are specific to your server:

.. code-block:: python

   sample = client.create_resource("Sample A", category=3)  # Use a real category ID.
   sample.main_text = "<p>Reference sample prepared in the clean room.</p>"
   sample.set_extra_fields({"Location": "Cabinet 2", "Mass (mg)": 12.4})
   sample.add_tag("reference")
   experiment.link_resource(sample.ID)

``client.load_resource(ID=...)`` or ``title=...`` reopens a resource. Read
``client.resources_dict`` and ``client.resource_status_dict`` for accessible
titles and resource statuses. Resource category assignment currently accepts
numeric IDs, not category names; there is no resource-template helper.

Files and inline images
-----------------------

.. code-block:: python

   experiment.upload_file("measurement.csv")
   print(experiment.list_files())

   attachment = experiment.get_file("measurement.csv")
   if attachment is not None:
       experiment.download_file(attachment["id"], "downloads/measurement.csv")

``upload_file()`` normally creates or replaces an attachment with the same
filename. It skips unchanged contents when the server exposes a compatible
hash. See :doc:`background_uploads` for replacement limitations.

For an image inside the main text:

.. code-block:: python

   image_html = experiment.upload_inline_image("plot.png", alt="Measured signal")
   experiment.main_text = experiment.main_text + image_html

Uploading an inline image returns HTML. It does not insert that HTML into the
main text automatically. ``link_experiment(other_experiment.ID)`` links another
experiment; ``link_resource(sample.ID)`` links a resource. Already-existing
links are skipped.

A complete runnable example
---------------------------

The repository's ``examples/elabftw_only.py`` creates one demonstration
experiment and one resource. Supply a category ID from your server:

.. code-block:: console

   python examples/elabftw_only.py --resource-category 3

Use ``--config path/to/elabmate.conf`` for another configuration file. Read the
file before running it: these are real writes, not an offline simulation.

.. literalinclude:: ../examples/elabftw_only.py
   :language: python
