Feature overview
================

Direct eLabFTW interface
------------------------

``Experiment`` represents an experiment. ``Resource`` represents a resource
or inventory item. Both can be created directly, or obtained through ``Client``.
They share these operations:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - eLabFTW concept
     - Python interface
   * - Title and main text
     - Read/write ``title`` and ``main_text`` (also ``body``).
   * - Category and status
     - Read/write ``category`` and ``status`` using existing choices.
   * - Dates
     - Read/write the started-on ``date``; read creation/modification timestamps.
   * - Custom fields
     - Read/write ``metadata``; create/update typed fields with ``set_extra_fields``.
   * - Steps
     - List, append and ensure steps; set completion through ``ensure_step``.
   * - Comments
     - List and append comments.
   * - Tags
     - List, check and add tags. No tag-removal helper is provided.
   * - Attached files
     - Upload, replace by filename, list, find by filename and download.
   * - Inline images
     - Upload an image and obtain HTML for inserting into the main text.
   * - Related records
     - Link experiments/resources, skipping links already present.

``Client`` creates/loads experiments and resources, creates experiments from
existing named templates, and exposes title/category/status dictionaries.
Loads accept a numeric ID or exact title. Properties are cached; ``refresh()``
fetches the current record.

Resource categories take numeric IDs; experiment categories and status setters
accept existing names or IDs. There is no resource-template helper.
Attachment hashes can avoid reuploading unchanged contents when supported by
the server. See :doc:`background_uploads` for limits.

Labmate integration
-------------------

``AcquisitionAnalysisManager`` keeps the current Labmate acquisition/analysis
workflow and adds background checkpoints, experiment routing, upload-state
inspection, waiting/closing and context-manager support.

Acquisition and analysis code from notebook cells or scripts is stored with
the data in HDF5; figures and matching sidecar files are also uploaded.
Open local HDF5 files with ``analysis_cell(filepath=...)``, or select one through
eLabFTW with ``analysis_cell(experiment_id=123, filename="run.h5")``. The manager
reuses the associated local file when available and downloads it otherwise.
It preserves local changes, without automatically refreshing from the server,
and retains the original experiment as the destination for saved checkpoints.
Associations are specific to the configured server. A required download waits
before analysis; uploads remain asynchronous. Stored code is not executed
automatically, and the complete notebook document is not archived.

Its ``experiment`` property returns an ``AsyncExperiment`` with queued writes:

.. code-block:: python

   experiment.add_step("...")
   experiment.add_comment("...")
   experiment.add_tag("...")
   experiment.main_text = "..."
   experiment.set_extra_fields({"Temperature (K)": 4.2})

``body`` is an alias for ``main_text``. The handle stays bound to its experiment.
Use the direct interface for remote reads and other operations. No asynchronous
resource handle is provided.

Not every GUI operation
-----------------------

There are no high-level helpers for deleting records or tags, unlinking records,
permissions/locks, advanced search/pagination, or administration of templates,
categories and statuses. The generated eLabFTW API client is broader; that does
not make every endpoint a supported eLabLink convenience method.
