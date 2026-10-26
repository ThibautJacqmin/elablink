API reference
=============

Start with :doc:`experiments_and_resources` for the direct eLabFTW interface,
then :doc:`notebooks` or :doc:`scripts` for optional Labmate synchronisation.
These reference pages import classes but never create a client or contact a
server during a documentation build.

Experiments
-----------

.. autoclass:: elablink.experiment.Experiment
   :members:

Resources
---------

Resources share the experiment helpers and route them to eLabFTW's
resources/items API.

.. autoclass:: elablink.resource.Resource
   :members:

Client
------

Use a client to load existing records, reuse a connection, choose a
configuration file or create an experiment from a template.

.. autoclass:: elablink.client.Client
   :members:
   :exclude-members: experiment_creation_wrapper

Notebook and script manager
---------------------------

Normal Labmate arguments, including ``save_files``, ``save_on_edit`` and
``shell``, are forwarded to Labmate. Its acquisition/analysis methods remain
available; consult the `Labmate documentation
<https://kyrylo-gr.github.io/labmate/>`_ for their full reference.

.. autoclass:: elablink.acquisition_analysis_manager.AcquisitionAnalysisManager
   :members: analysis_cell, experiment, upload_status, flush, close

Queued experiment edits
-----------------------

The manager supplies this write-only handle after saving an acquisition or a
figure, or after opening a remote file for analysis. It does not expose a
synchronous experiment object to the notebook.

.. autoclass:: elablink.async_experiment.AsyncExperiment
   :members:

Advanced integration
--------------------

``AsyncBridge`` integrates through the current Labmate lifecycle hooks and
runs server work on a background worker. ``Bridge`` performs the underlying
synchronous experiment synchronisation; using it directly waits for the server.

.. autoclass:: elablink.async_bridge.AsyncBridge
   :members:

.. autoclass:: elablink.bridge.Bridge
   :members:

Exceptions
----------

.. automodule:: elablink.exceptions
   :members:
