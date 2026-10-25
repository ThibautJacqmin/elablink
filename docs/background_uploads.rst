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
checkpoint and one experiment. If that checkpoint fails, dependent edits fail
instead of silently being applied elsewhere.

Check, wait and finish
----------------------

.. code-block:: python

   aqm.upload_status()          # Read local job states, without waiting.
   information = aqm.flush()   # Wait; keep the manager open.
   aqm.close()                 # Stop new jobs and finish pending ones.

``flush()`` waits for jobs submitted before the call. It returns the last
submitted job's experiment ID, title and URL, or an empty dictionary if no jobs
exist. Leaving a manager's ``with`` block also closes it.

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
* Missing local HDF5 files are not downloaded automatically before analysis.
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
