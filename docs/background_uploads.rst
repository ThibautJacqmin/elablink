Background uploads and failures
===============================

What runs in the background?
----------------------------

The Labmate integration uses this sequence:

1. Labmate saves the data or figure locally.
2. eLabLink makes a stable local copy of the run's files.
3. One background worker uploads that copy to eLabFTW, in submission order.

The first two steps happen before the save call returns. They are not
zero-cost, but network delays occur on the worker, not in the acquisition cell.
The copy stops a later save changing files that the worker is still uploading.
Keep the data directory and upload queue on a local disk if you want to avoid
network waits during acquisition. Do not modify the files from another process
while their snapshot is being captured.

The integration uses the current Labmate lifecycle hooks for acquisition saves
and analysis-figure saves. No alternative Labmate storage backend is required.

Opening remote data for analysis
--------------------------------

``aqm.analysis_cell(experiment_id=123, filename="run.h5")`` first checks the
local association for that server, experiment ID and filename. It reuses the
known local HDF5 file if present. A successful acquisition upload records its
original local path, so a later analysis can reuse that file too. Files saved
before this association existed are not found by guessing their folder names.

If no associated local file is available, the worker downloads the HDF5 file
to a local working folder. Data must arrive before Labmate can open it, so the
call waits. Earlier queued jobs run first. Pass ``download_timeout=300`` to wait
longer than the default 120 seconds; HTTP request timeouts also apply.

The existing local copy takes precedence over the server version. Local changes
are preserved, and there is no automatic remote refresh or conflict resolution.
The local association is server-specific, requires no remote custom field,
and remains available when you reopen the file in another manager.
Keep one Python process responsible for a data directory: the local index is
not a multi-process database.

This operation does not run stored code or retrieve all attachments. Normal
``filepath=...`` calls still use local files, without remote guesses.
When you save a figure for the selected run, the updated HDF5 file and figure
are saved locally first and uploaded asynchronously to the original experiment.

What gets uploaded?
-------------------

The selected run's HDF5 file and matching saved sidecars, including figures
and separate code files when available. Labmate stores acquisition and analysis
code in the HDF5 file; its ``save_files=True`` option also writes separate
code/configuration files.

The integration does not upload arbitrary files throughout your data directory.
Local data remains the primary copy, and eLabFTW synchronisation is not a
replacement for your laboratory's backups.

``aqm.experiment`` edits use the same worker. Each handle is tied to a saved
checkpoint or successful remote analysis open, and to one experiment. If the
prerequisite operation fails, dependent edits fail instead of silently being
applied elsewhere.

Check, wait and finish
----------------------

.. code-block:: python

   aqm.upload_status()          # Read local job states, without waiting.
   information = aqm.flush()   # Wait; keep the manager open.
   aqm.close()                 # Stop new jobs and finish pending ones.

``flush()`` waits for jobs submitted before the call. It returns the last
submitted job's experiment ID, title and URL, or an empty dictionary if no jobs
exist. Leaving a manager's ``with`` block also closes it. Closing only waits:
it does not create a new save. For a remote reanalysis, ``save_fig()`` is what
queues the updated analysis checkpoint and figure.

Both wait calls default to 120 seconds; choose a longer timeout if needed:

.. code-block:: python

   information = aqm.flush(timeout=300)

The default HTTP connection/read timeout pair is ``(5, 30)`` seconds. Pass
``request_timeout=(5, 60)`` to the manager to change it. A read timeout is not
a guaranteed maximum total upload duration.

A wait timeout does not cancel an in-flight request. Check ``upload_status()``
and call ``flush()`` or ``close()`` again later. After a close was requested, no
new saves or edits are accepted; create a new manager for subsequent work.
Use these methods on the thread that created the manager, normally the notebook
execution thread.

If an upload fails
------------------

Your local Labmate save has already happened. Failed jobs retain their stable
snapshots under ``data/.upload_queue/snapshot-...`` by default. ``flush()`` or
``close()`` raises ``RuntimeError`` chained from the original error;
``upload_status()`` includes the failure message. Successful snapshots are
removed automatically.

There is no automatic retry or restart recovery. Keep failed snapshots until
you have inspected the remote experiment and recovered the files you need.
Do not delete your original data to clear an upload error.

A request may partly succeed before its connection fails. Check the server
before repeating it manually: adding comments and steps can produce duplicates.
Queued experiment edits are held in memory, not a durable on-disk journal.

Before stopping Python
----------------------

Always finish with ``aqm.close()`` or use a ``with`` block. A kernel shutdown,
power loss or process crash can interrupt transfers; no cleanup handler
guarantees delivery in those circumstances. Preserve local backups.

Other boundaries
----------------

* The asynchronous handle supports a small set of experiment edits, not
  resource edits or fresh server reads.
* Remote analysis requires an explicit experiment ID and HDF5 attachment filename;
  a missing local ``filepath`` is not silently replaced by a download.
* Attachment replacement is best-effort. If listing existing attachments fails,
  the uploader can fall back to creating another file. Reliable skipping of
  unchanged contents needs a compatible server-side hash, not just equal sizes.
* Record permissions, available statuses and category IDs depend on your server.
* Filename generation and local acquisition semantics remain Labmate's
  responsibility; see :doc:`scripts` for repeated acquisitions.

Advanced: attach to a manager you already have
----------------------------------------------

The eLabLink manager needs no manual setup. If another integration creates your
Labmate manager, attach an ``AsyncBridge`` directly instead:

.. code-block:: python

   from elablink import AsyncBridge

   uploads = AsyncBridge("My experiment")
   uploads.attach(existing_labmate_manager)
   # Use the existing manager normally.
   uploads.close()

Do not attach another bridge to eLabLink's manager: it already has one.
Callbacks remain registered after closing a bridge, so use a fresh manager
when replacing it. ``Bridge`` is the synchronous integration engine;
``AsyncBridge`` schedules its server operations on the background worker.
