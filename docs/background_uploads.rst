Background uploads and failures
===============================

What is asynchronous?
---------------------

An acquisition or figure save first writes locally with Labmate. eLabmate then
copies the relevant HDF5 file and matching sidecar files into a local upload
snapshot. That local copy takes some time; it is **not** a zero-cost operation.
Network requests run afterwards, in order, on a single background worker.

The snapshot prevents the worker from reading a half-updated file while your
next cell changes the original. Do not modify those files from another process
while a snapshot is being captured. Keep the data directory and upload queue on
a local disk if your aim is to avoid network delays during acquisition.

``aqm.experiment`` edits use the same worker. They wait for the saved checkpoint
associated with their handle to succeed. If that prerequisite fails, the edit
fails instead of being applied to an experiment with no confirmed checkpoint.

What is uploaded?
-----------------

The bridge captures the selected run's HDF5 file and matching saved sidecars,
including figures and separate code files when available. It does not
recursively upload arbitrary files in your data folder. Local files remain
the primary copy; uploading is a second destination, not a replacement for
your laboratory's backup policy.

When does the notebook wait?
----------------------------

* Saving waits for Labmate's local write and the local snapshot copy.
* ``upload_status()`` reads local job states without a server request.
* ``flush()`` waits for jobs submitted before that call, without closing.
* ``close()`` stops new submissions and waits for the submitted jobs.
* Leaving a ``with`` block closes the manager.

The default wait timeout is 120 seconds. To wait longer:

.. code-block:: python

   information = aqm.flush(timeout=300)

The default HTTP connection/read timeout pair is ``(5, 30)`` seconds. You can
choose a different pair with ``request_timeout=(5, 60)`` when constructing the
manager. An HTTP read timeout is not a guaranteed total upload duration.

A wait timeout does **not** cancel an in-flight request. Inspect
``aqm.upload_status()`` and call ``flush()`` or ``close()`` again later. Once a
close was requested, the manager no longer accepts new saves or edits; create
a fresh manager for subsequent work. These calls and submissions belong on
the thread that created the manager, normally the notebook's execution thread.

What if the server is unavailable?
----------------------------------

The local Labmate file is saved before uploading. A failed upload keeps its
stable snapshot in ``data/.upload_queue/snapshot-...`` by default, and
``flush()`` or ``close()`` raises a ``RuntimeError`` chained from the original
error. ``upload_status()`` identifies the failed job and its error message.

Successful snapshots are removed. Failed snapshots are not automatically
retried, replayed after restart, or silently claimed to be uploaded. Retain
them until you have checked the remote experiment and safely recovered the
files you need. Do not delete your original data to clear an upload error.

A remote operation can partly succeed before its connection fails. Inspect
the server before manually repeating writes: comments and steps can duplicate.
Queued experiment edits live only in memory; they have no disk-backed journal.

A stopped kernel, power loss or process crash can interrupt jobs. No cleanup
handler can guarantee delivery in those situations. Finish with ``aqm.close()``
and keep normal local backups.

Current boundaries
------------------

* There is no automatic retry, durable edit journal, or restart recovery.
* The asynchronous interface edits experiments, not resources.
* Fresh server reads use the separate synchronous client.
* Attachment replacement is best-effort. If listing existing attachments fails,
  the current synchronous uploader can fall back to creating another file.
  Server-side hashes are required for reliable unchanged-content skipping;
  equal file sizes alone are not a reliable content comparison.
* API responses, record permissions, category IDs and available statuses depend
  on the eLabFTW installation.
* Filename generation and local acquisition semantics remain Labmate's
  responsibility. See the rapid-loop note in :doc:`scripts`.

Advanced: attach to an existing Labmate manager
-----------------------------------------------

Normally, use eLabmate's ``AcquisitionAnalysisManager``. If another integration
already creates the Labmate manager, attach the bridge directly:

.. code-block:: python

   from elabmate import AsyncElabBridge

   sync = AsyncElabBridge("My experiment")
   sync.attach(existing_labmate_manager)
   # Use the existing manager as usual.
   sync.close()

Do not attach a second bridge to eLabmate's manager: it already has one. Hooks
remain registered after a bridge is closed, so use a fresh manager when
replacing it. The older synchronous ``ElabBridge`` remains available for
existing callers; it does not itself make network calls asynchronous.
