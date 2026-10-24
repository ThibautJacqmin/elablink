Notebook acquisition and analysis
=================================

Use eLabmate's manager in place of Labmate's manager. Your local Labmate
workflow stays the same; saved checkpoints are also queued for eLabFTW.

Complete :doc:`installation` first, then run these four cells in order in
Jupyter or VS Code. The notebook must run from a folder containing your
``elabmate.conf``. Running these cells writes to your real eLabFTW server.

1. Set up
---------

.. code-block:: python

   import numpy as np
   import matplotlib.pyplot as plt
   from elabmate import AcquisitionAnalysisManager

   aqm = AcquisitionAnalysisManager("data")

2. Acquire a sinusoid
---------------------

.. code-block:: python

   aqm.acquisition_cell("Sinusoid")
   t = np.linspace(0, 1, 500)
   y = np.sin(2 * np.pi * 5 * t)
   aqm.save_acquisition(t=t, y=y)

Labmate saves the data locally in the ``data/Sinusoid/`` folder. eLabmate
captures a stable local copy and uploads it in the background. You can continue
to the next cell while the server is busy.

3. Analyse and save a plot
--------------------------

.. code-block:: python

   aqm.analysis_cell()
   plt.figure()
   plt.plot(aqm.data.t, aqm.data.y)
   plt.xlabel("Time (s)")
   plt.ylabel("Signal (V)")
   aqm.save_fig(name="sinusoid.png")
   plt.show()

The figure and available saved code files are uploaded to the same experiment.
Labmate also stores its acquisition and analysis information in the HDF5 file.
Use the normal Labmate option ``save_files=True`` if you also want separate
code/configuration files alongside the data.

4. Finish
---------

.. code-block:: python

   aqm.close()

This last cell waits for pending uploads and experiment edits, and reports any
failure. Run it before restarting or closing the kernel. After closing, create
a **new** manager if you want to acquire more data.

The same four cells are provided in
``example_labmate_elabFTW_bridge.ipynb`` and, with VS Code cell markers, in
``example_labmate_acquisition_analysis.py`` at the repository root.

Which experiment receives the data?
-----------------------------------

``aqm.acquisition_cell("Sinusoid")`` selects the experiment titled
``Sinusoid``. Repeating this name in the same manager reuses the experiment;
different run files remain separate attachments. Reuse across fresh managers
follows ``UNIQUE_EXPERIMENTS_TITLES`` in your configuration.

To send every acquisition to one specific experiment instead:

.. code-block:: python

   aqm = AcquisitionAnalysisManager("data", experiment_id=123)

Use ``experiment_title="My experiment"`` to choose a fixed title instead of
following acquisition names. Both choices are optional; normally only the
import changes.

Reopen data for analysis
------------------------

.. code-block:: python

   aqm.analysis_cell(filepath="data/Sinusoid/your-run-file.h5")
   plt.figure()
   plt.plot(aqm.data.t, aqm.data.y)
   aqm.save_fig(name="reanalysis.png")

Use a real local file path. Saving this new figure queues a checkpoint even if
the acquisition happened in another notebook session. By default its data
folder name selects the experiment; a fixed title or ID still overrides this.
Keep the HDF5 file locally: automatic retrieval from eLabFTW is not part of
this asynchronous manager.

Optional experiment notes
-------------------------

After saving an acquisition or a figure, and **before closing**, access its
experiment without waiting for the server:

.. code-block:: python

   experiment = aqm.experiment
   experiment.add_step("Sample cooled to 4 K")
   experiment.add_comment("Signal looks stable.")
   experiment.add_tag("calibration")
   experiment.main_text = "<p>Sinusoidal signal measurement.</p>"
   experiment.set_extra_fields({"Temperature (K)": 4})

These writes are queued after that handle's saved checkpoint. The handle stays
bound to the selected experiment even if you start another acquisition.
``main_text`` replaces the experiment's main text; ``body`` is an alias.
``add_comment()`` appends a separate note.

This is a small **write-only** interface, not a complete synchronous
``ElabExperiment``. For remote reads or advanced operations, use a separate
``ElabClient`` as shown in :doc:`elabftw_only`.

Check progress or obtain a link
-------------------------------

.. code-block:: python

   aqm.upload_status()          # No server request and no wait.
   information = aqm.flush()   # Wait, but keep the manager open.
   print(information["url"])

Call ``flush()`` only after submitting a save or edit if you need an experiment
link. It returns the last submitted job's experiment information; with no jobs
it returns an empty dictionary. See :doc:`background_uploads` for failures and
timeouts.
