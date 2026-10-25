Acquisition in a Python script
==============================

Use the same local-save, background-upload workflow outside Jupyter. The
example ``examples/labmate_acquisition.py`` generates a sinusoid, saves the data
and a PNG, adds a step and comment to the eLabFTW experiment, and prints its link.

After :doc:`installation`, run it from the project root:

.. code-block:: console

   python examples/labmate_acquisition.py

This is a linear script: edit ``"data"`` or the signal parameters directly
when adapting it to your measurement. There is no command-line parser or
``main()`` function. Running or importing it starts the acquisition and writes
to your configured server; read it first.

.. literalinclude:: ../examples/labmate_acquisition.py
   :language: python

What changes outside a notebook?
--------------------------------

``shell=None`` and ``use_magic=False`` disable notebook-specific behaviour.
A plain Python process has no current notebook cell, so the example reads its
own source and passes it with ``cell=source``. This keeps the actual
acquisition and analysis code with the saved data.

The ``with`` block closes the manager on exit and waits for pending uploads.
A normal exit reports an upload failure. If the acquisition already raised an
exception, that original exception is preserved and an additional upload failure
is logged.

Access the experiment
---------------------

After ``save_acquisition()`` or ``save_fig()``, use the same handle as in a
notebook:

.. code-block:: python

   experiment = aqm.experiment
   experiment.add_step("Measurement complete")
   experiment.add_comment("Laser power checked after the measurement.")
   experiment.set_extra_fields({"Power (mW)": 2.5})

These edits are queued and do not wait for the network. Use ``aqm.flush()``
inside the ``with`` block when you need confirmation or the experiment link:

.. code-block:: python

   information = aqm.flush()
   print(information["url"])

For synchronous reads or further operations after the manager closes, load the
record through a separate client using the returned ID:

.. code-block:: python

   from elablink import Client

   experiment = Client().load_experiment(ID=information["id"])
   print(experiment.steps)

This last read waits for the server, like the direct API examples.

Timing and repeated acquisitions
--------------------------------

Network transfers are asynchronous, but local Labmate saves and snapshot
copies still take time. Keep the data directory on a local disk.

Labmate's notebook-style ``acquisition_cell()`` names runs with second-resolution
timestamps. Repeating the same name within one second is not a way to create
independent files. This integration preserves Labmate's filename behaviour;
check its `acquisition documentation <https://kyrylo-gr.github.io/labmate/>`_
before adapting this simple example into a rapid acquisition loop.
