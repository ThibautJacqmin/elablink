eLabmate
========

**Python measurements, lab notebook records.**

eLabmate, written and maintained by **Thibaut Jacqmin**, connects Python to
eLabFTW. Use it to manage experiments and resources directly, or to keep a remote
copy of your Labmate acquisition data without waiting for network uploads.

Choose your starting point
--------------------------

* **I only need eLabFTW:** :doc:`elabftw_only` shows experiments, resources,
  notes, custom fields, files and links. Labmate is not required.
* **I acquire data in Jupyter:** :doc:`notebooks` keeps the usual Labmate cells.
  Change the import, then finish with ``aqm.close()``.
* **I run a Python script:** :doc:`scripts` shows the same workflow without
  notebook-specific behaviour.

The notebook interface starts with just:

.. code-block:: python

   from elabmate import AcquisitionAnalysisManager

   aqm = AcquisitionAnalysisManager("data")

Set up your environment and ``elabmate.conf`` first: :doc:`installation`.
The configuration file contains your API key; keep it out of Git.

.. toctree::
   :maxdepth: 2
   :caption: Get started

   installation
   elabftw_only
   notebooks
   scripts

.. toctree::
   :maxdepth: 2
   :caption: Reference

   background_uploads
   features
   api
   contributing
   about
