Acquisition from a Python script
================================

A notebook is not required. Use the same manager, but explicitly disable the
IPython shell and magic commands:

.. code-block:: python

   with AcquisitionAnalysisManager("data", shell=None, use_magic=False) as aqm:
       # Acquire and analyse here.
       ...

The context manager waits for uploads when it exits. A normal exit reports an
upload failure. If your acquisition already raised an exception, that original
exception is preserved and an additional upload failure is logged.

Run the complete example
------------------------

From the repository root, with your configuration ready:

.. code-block:: console

   python examples/acquisition_script.py

Or choose another data directory and configuration:

.. code-block:: console

   python examples/acquisition_script.py --data measurements --config path/to/elabmate.conf

This example generates a sinusoid, saves the HDF5 data, saves a PNG, adds a note,
and prints the eLabFTW experiment link. It modifies your real server only when
run, not when imported or when building this documentation.

.. literalinclude:: ../examples/acquisition_script.py
   :language: python

Keep the source with the data
-----------------------------

In Jupyter, Labmate can retrieve the current cell. A plain Python process has
no current notebook cell, so the example reads its own source and passes it
explicitly with ``cell=script_source``. This preserves the actual acquisition
and analysis code rather than pretending automatic cell capture is available.

``shell=None`` and ``use_magic=False`` make the script independent of Jupyter.
No path injection, environment activation helper or example-specific bridge
setup is needed: install both projects into the Python environment you use to
run the script.

Fast acquisition loops
----------------------

Labmate's notebook-style ``acquisition_cell()`` uses second-resolution run
timestamps. It is not a safe way to create multiple independent same-name
runs within one second. This integration deliberately does not change that
upstream behaviour. For a rapid loop, use Labmate's documented collision-aware
``create_acquisition()`` workflow and check which lifecycle saves it emits for
your use case before relying on remote copies. A script example is not a
promise that every possible Labmate loop pattern is automatically integrated.
