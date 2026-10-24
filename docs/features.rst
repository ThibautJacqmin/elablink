Feature overview
================

Direct eLabFTW interface
------------------------

``ElabClient`` creates experiments, creates experiments from named templates,
loads experiments by ID or exact title, and provides experiment title,
category and status dictionaries. It also creates and loads resources/items
and provides resource title and status dictionaries.

``ElabExperiment`` and ``ElabResource`` share these helpers:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Area
     - Implemented operations
   * - Properties
     - Read/change title, main text, category, status and started-on date;
       read creation/modification timestamps; refresh cached values.
   * - Custom fields
     - Read/write metadata and create/update typed extra fields.
   * - Steps
     - List, append and ensure steps; set completion through ``ensure_step``;
       select repeated labels by occurrence.
   * - Comments
     - List and append comments.
   * - Tags
     - List, check and add tags. Tag removal is intentionally not provided.
   * - Attachments
     - Upload, replace by filename, list, locate by filename and download;
       skip unchanged contents when compatible server hashes are available.
   * - Images
     - Upload images, obtain download URLs and generate inline HTML.
   * - Links
     - Link experiments/resources and skip links already present.

Resource categories require numeric IDs; experiment categories accept names
or IDs. There is no resource-template helper. Inline-image HTML must be
inserted into the main text explicitly.

Acquisition interface
---------------------

``AcquisitionAnalysisManager`` preserves the usual Labmate workflow while
adding background checkpoints, experiment routing, upload-state inspection,
explicit waiting/closing and context-manager support.

Its ``experiment`` handle supports the following **queued writes only**:

.. code-block:: python

   experiment.add_step("...")
   experiment.add_comment("...")
   experiment.add_tag("...")
   experiment.main_text = "..."
   experiment.body = "..."  # Same property, not additional text.
   experiment.set_extra_fields({"Temperature (K)": 4})

Other operations require the direct, synchronous interface. No asynchronous
resource handle is provided. See :doc:`background_uploads` for the reliability
boundaries.

Not implemented
---------------

There are no high-level helpers for deleting records/tags, unlinking records,
permission or lock management, advanced search/pagination, or administering
templates, categories and statuses. The underlying generated API is broader,
but it is not the same as eLabmate providing a supported convenience helper.
