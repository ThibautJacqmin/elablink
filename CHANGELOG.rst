eLabLink release notes
=====================

0.3.0a2 (prerelease)
--------------------

* Record search stays open on the left, including when the browser opens
  directly on an experiment or resource. Drag the divider to resize it.
* The **Clear cache** button shows the total size of local cached attachments
  and can clear downloads across all records. **Cached downloads…** still
  clears a single record. Both actions preserve attachments on eLabFTW and
  files saved to a working folder or with **Save as…**.

0.3.0a1 (prerelease)
--------------------

The optional desktop attachment browser is experimental and has received less
testing than the core eLabLink Python interface.

* Find experiments and resources using the server's categories, statuses and
  tags. Single-click a result to view its attachments.
* Preview, download, upload and manage files, or open the browser for an
  existing record with ``browse_files()``.
* Install the ``browser`` extra to enable the Qt desktop interface and PDF
  previews. See ``docs/attachment_browser.rst`` for Windows, macOS and Linux
  installation and shortcut instructions.

0.2.0 (2026-11-25)
------------------

* Transfer experiments and resources to an existing eLabFTW user and verify the
  new owner and team. Experiment creation, including creation from templates,
  accepts an optional owner. If a transfer fails after creation, its exception
  includes the created experiment ID.
* Report all matching record IDs when a title lookup is ambiguous.
* Fetch current metadata before updating extra fields and retain existing field
  settings, including units and choices. Verify checkbox updates against the
  server and report conflicting edits.
* Remember the original experiment for an H5 acquisition opened from another
  analysis project. New uploads write a portable association beside the H5 file.
  Older files can use the original data directory's index. A captured checkpoint
  can finish uploading when Labmate replaces the original local H5 file.
* Configure background file upload timeouts independently of other API requests.
  Protect acquisition snapshots against linked files and keep sensitive HTTP
  response details out of background error reports. On macOS, acquisitions can
  use the standard ``/tmp`` and ``/var`` directories while checks still reject
  linked acquisition files and directories.
* Stream attachment downloads to a temporary file and replace the destination
  only when the transfer completes.
* Accept both naming conventions for category and status APIs in supported
  elabapi-python 6 versions. Read the original metadata and team from the server
  when loading a record so SDK conversion preserves custom-field settings and
  ownership transfers can be verified.
* Require HTTPS unless the configuration explicitly enables plain HTTP with
  ``ALLOW_INSECURE_HTTP=true``. Certificate verification settings accept
  ``true`` or ``false``.

0.1.1
-----

* Fix compatibility with elabapi-python 6.x, including renamed API classes and
  the argument order used to update experiments and resources.
* Align API-key authentication with the generated client's Authorization settings.
* Require ``elabapi-python>=6.0.1,<7`` and add regression tests exercising the
  installed SDK's request handling.

0.1.0
-----

* Create and edit eLabFTW experiments and resources: notes, steps, comments,
  tags, custom fields, categories, statuses, links and attachments.
* Save Labmate acquisitions and figures locally, then upload data and code in
  the background. Explicit metadata reads fetch current server values.
* Reopen eLabFTW measurements for analysis, reuse existing local files and
  download missing copies. Later saves return to the original experiment.
* Replace changed attachments while archiving the previous version; skip
  unchanged contents when the server supplies a compatible hash.
* Include three examples for the direct interface, Python acquisition scripts
  and Jupyter notebooks. Resource categories and statuses are optional.
* Describe the optional h5viewer application for browsing local Labmate HDF5
  files and rerunning saved analyses.

Compatibility and limits
~~~~~~~~~~~~~~~~~~~~~~~~

Requires Python 3.10 or newer and Labmate 0.10.10 or newer for acquisition and
analysis. Live tests used
eLabFTW 6.0.9 and elabapi-python 5.1.0. Creation from an experiment template was
tested offline; no accessible template was available for the live test.

Call ``aqm.close()`` before stopping the kernel to finish pending work. Failed
upload snapshots are retained locally, but there is no automatic retry or
recovery after a process crash. Explicit remote reads wait for the server.
