API reference
=============

The guides show the intended starting points. This reference documents the
actual wrapper classes without creating clients or contacting an eLabFTW
server during the documentation build.

Direct client
-------------

.. autoclass:: elabmate.client.ElabClient
   :members:
   :exclude-members: experiment_creation_wrapper

Experiments
-----------

.. autoclass:: elabmate.experiment.ElabExperiment
   :members:

Resources
---------

Resource objects inherit the experiment helpers above and route them to the
eLabFTW resources/items API.

.. autoclass:: elabmate.resource.ElabResource
   :members:

Notebook/script manager
-----------------------

Standard Labmate arguments, such as ``save_files``, ``save_on_edit`` and
``shell``, are forwarded to Labmate. Its acquisition/analysis methods remain
available; see the `Labmate documentation <https://kyrylo-gr.github.io/labmate/>`_
for their full reference.

.. autoclass:: elabmate.acquisition_analysis_manager.AcquisitionAnalysisManager
   :members: experiment, upload_status, flush, close

Queued experiment writes
-------------------------

.. autoclass:: elabmate.async_experiment.AsyncElabExperiment
   :members:

Advanced bridge interfaces
--------------------------

.. autoclass:: elabmate.async_bridge.AsyncElabBridge
   :members:

The synchronous bridge is retained for compatibility and for custom advanced
integrations. It is not the recommended setup for a new acquisition notebook.

.. autoclass:: elabmate.bridge.ElabBridge
   :members:
