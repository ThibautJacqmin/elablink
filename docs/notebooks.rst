Acquisition in a notebook
=========================

`Labmate <https://kyrylo-gr.github.io/labmate/>`_ keeps acquisition and analysis
separate: acquire once, then analyse the saved data without relying on variables
left in memory. It stores data and code together so a measurement can be
reopened later.

eLabLink adds background uploads to that workflow. The local Labmate files
remain your primary copy; saved data and figures are also sent to eLabFTW.
Install and configure the projects as described in :doc:`installation` first.

Open ``examples/labmate_notebook.ipynb`` in Jupyter or VS Code and select your
Python environment. Its main cells are shown below. They write to your real
server when run.

1. Set up
---------

.. code-block:: python

   import numpy as np
   import matplotlib.pyplot as plt
   from elablink import AcquisitionAnalysisManager

   aqm = AcquisitionAnalysisManager("data")

Use eLabLink's manager instead of importing Labmate's manager. No extra hook,
bridge or configuration setup is needed in the notebook.

2. Acquire a sinusoid
---------------------

.. code-block:: python

   aqm.acquisition_cell("Sinusoid")
   t = np.linspace(0, 1, 500)
   y = np.sin(2 * np.pi * 5 * t)
   aqm.save_acquisition(t=t, y=y)

Labmate saves the run locally in ``data/Sinusoid/``. eLabLink captures a stable
local copy and queues its upload. You can continue while the server is busy.

3. Analyse the saved data
-------------------------

.. code-block:: python

   aqm.analysis_cell()
   plt.figure()
   plt.plot(aqm.data.t, aqm.data.y)
   plt.xlabel("Time (s)")
   plt.ylabel("Signal (V)")
   aqm.save_fig(name="sinusoid.png")
   plt.show()

Read ``aqm.data`` rather than the earlier ``t`` and ``y`` variables. This makes
the analysis work when you reopen the saved measurement in another session.
The saved figure and updated data file are queued for the same experiment.

4. Add a note and finish
------------------------

.. code-block:: python

   experiment = aqm.experiment
   experiment.add_step("Sinusoid acquired and plotted.")
   experiment.add_comment("The signal is stable.")
   information = aqm.flush()
   print(information["url"])
   aqm.close()

After a save, ``aqm.experiment`` retrieves an
:class:`~elablink.async_experiment.AsyncExperiment`
handle immediately; the edits run on the same background worker as the uploads.
The handle stays bound to that experiment even if you start another acquisition.
``flush()`` waits and returns the last submitted job's experiment ID, title and
URL. ``close()`` waits too, and finishes the manager. Run it before shutting down
or restarting the kernel; create a new manager for further acquisitions.

More experiment edits
---------------------

Before closing, the handle also supports:

.. code-block:: python

   experiment.add_tag("calibration")
   experiment.main_text = "<p>Sinusoidal signal measurement.</p>"
   experiment.set_extra_fields({"Temperature (K)": 4.2})

``main_text`` replaces the main text. This small, write-only handle is not the
direct :class:`~elablink.experiment.Experiment` object: it does not fetch remote properties
or attachments. For those operations, use ``Client`` separately as described in
:doc:`experiments_and_resources`.

Which eLabFTW experiment?
-------------------------

By default, the acquisition name ``Sinusoid`` selects the experiment title.
Repeated names in one manager reuse its experiment; different run files remain
separate attachments. Reuse across fresh managers follows
``UNIQUE_EXPERIMENTS_TITLES`` in your configuration.

To put everything into one existing experiment:

.. code-block:: python

   aqm = AcquisitionAnalysisManager("data", experiment_id=123)

Use your real ID. Alternatively, ``experiment_title="My experiment"`` fixes the
title instead of following each acquisition name.

Reanalyse an earlier measurement
--------------------------------

.. code-block:: python

   aqm.analysis_cell(filepath="data/Sinusoid/your-run-file.h5")
   plt.figure()
   plt.plot(aqm.data.t, aqm.data.y)
   aqm.save_fig(name="reanalysis.png")

Use a real local file path. Saving the figure queues an updated checkpoint even
when this manager did not acquire the data. By default, the data folder's name
selects the experiment. Automatic retrieval of missing data from eLabFTW is not
provided: keep your local files.

``aqm.upload_status()`` inspects progress without waiting or contacting the
server. See :doc:`background_uploads` for timeouts and failures.
