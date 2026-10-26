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

After a save or a successful remote analysis open, ``aqm.experiment`` retrieves an
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

   aqm = AcquisitionAnalysisManager("data")
   aqm.analysis_cell(filepath="data/Sinusoid/your-run-file.h5")
   plt.figure()
   plt.plot(aqm.data.t, aqm.data.y)
   aqm.save_fig(name="reanalysis.png")

Use a real local file path. Saving the figure queues an updated checkpoint even
when this manager did not acquire the data. By default, the data folder's name
selects the experiment.

To start from eLabFTW instead, use the experiment ID and exact attachment filename
shown on your server. In a new session, with the usual imports:

.. code-block:: python

   aqm = AcquisitionAnalysisManager("data")
   aqm.analysis_cell(experiment_id=123, filename="run.h5")
   plt.figure()
   plt.plot(aqm.data.t, aqm.data.y)
   aqm.save_fig(name="reanalysis.png")
   aqm.experiment.add_comment("Reanalysed the saved measurement.")
   aqm.close()

The call first looks for the local file already associated with this server,
experiment ID and attachment filename. This can be the original acquisition file
if eLabLink recorded its association after a successful upload. If that file is
missing or you are using a new computer, eLabLink downloads only the HDF5 file
to a local working folder, then opens it.

Existing local files take precedence, including your local analysis changes.
They are not overwritten or refreshed automatically from the server. eLabLink
does not guess an unknown original folder from the experiment title, and it
does not execute the acquisition or analysis code stored in the HDF5 file.

Work locally as usual. ``save_fig()`` saves the figure and analysis checkpoint
locally first, then queues the updated HDF5 file and figure for experiment 123.
``aqm.experiment`` is available immediately after opening the analysis.
If you later reopen that working copy with ``filepath=...``, the saved association
keeps the same remote destination when using the same server configuration.
Associations belong to one server; they are not reused for another server.

Use ``download_timeout=300`` on ``analysis_cell()`` for a longer download wait
(the default is 120 seconds). A missing local ``filepath`` still raises an error:
eLabLink does not guess which remote attachment you meant.

Changing variables in memory is not a save. Call ``save_fig()`` to create the
analysis checkpoint; ``close()`` only waits for work already queued.

``aqm.upload_status()`` inspects progress without waiting or contacting the
server. See :doc:`background_uploads` for timeouts and failures.
