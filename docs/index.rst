eLabLink
========

Your eLabFTW notebook, from Python. eLabLink helps you to use programmatically the online scientific lab book
directly in your python experimental sequences.

First, eLabLink provides Python objects  that map the eLabFTW 
interface. Create an experiment or a resource,
then edit its notes, steps, tags, custom fields and attachments.

Second, it optionnaly provides tools to save and retrieve acquisition and analysis
ipynb cells, or scripts together with acquisition and analysis data,
from eLabFTW experiments

This is done by using the labmate Python module. Your data and acquisition, and analysis codes
are then gather in a .h5 file and attached to your eLabFTW experiment asynchronously, without
blocking your next acquisition cell. 


.. code-block:: python

   from elablink import Experiment

   experiment = Experiment(title="Laser calibration")
   experiment.main_text = "<p>Measure the laser power.</p>"
   experiment.add_step("Align the beam")
   experiment.add_comment("The signal is stable.")

These few lines create and edit a real server record. Prepare your private
project setup first: :doc:`installation`.

From the GUI to Python
----------------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - In eLabFTW
     - In eLabLink
   * - An experiment
     - ``Experiment(...)``
   * - A resource, such as a sample or instrument
     - ``Resource(...)``
   * - Title and main text
     - ``record.title`` and ``record.main_text``
   * - Add a step, comment or tag
     - ``record.add_step(...)``, ``add_comment(...)``, ``add_tag(...)``
   * - Custom fields, attached files and related records
     - ``set_extra_fields(...)``, ``upload_file(...)``, ``link_resource(...)``

The names follow the eLabFTW concepts. Not every GUI operation is implemented;
see :doc:`features` for the precise scope. Start with
:doc:`experiments_and_resources` for short examples.

Also synchronise acquisition data
---------------------------------

`Labmate <https://kyrylo-gr.github.io/labmate/>`_ separates **acquisition** from
**analysis** and keeps the saved data and code together. Its analysis workflow
reads the saved data instead of relying on variables left in notebook memory.

eLabLink's optional Labmate integration keeps that local workflow and sends
saved data and figures to eLabFTW in the background. A slow network upload does
not hold up your next acquisition cell. Only the manager import changes:

.. code-block:: python

   from elablink import AcquisitionAnalysisManager

   aqm = AcquisitionAnalysisManager("data")

Follow :doc:`notebooks` for Jupyter and VS Code, or :doc:`scripts` for a normal
Python script. Finish with ``aqm.close()`` to wait for uploads and report errors.
Local file writes and snapshot copies still take time; the network work is
asynchronous. See :doc:`background_uploads` for what happens if a transfer fails.

.. toctree::
   :maxdepth: 2
   :caption: Get started

   installation
   experiments_and_resources

.. toctree::
   :maxdepth: 2
   :caption: Acquisition with Labmate

   notebooks
   scripts
   background_uploads

.. toctree::
   :maxdepth: 2
   :caption: Reference

   features
   api
   contributing
   about
