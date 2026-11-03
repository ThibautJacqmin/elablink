eLabLink release notes
=====================

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
