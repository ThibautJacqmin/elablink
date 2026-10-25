Development and documentation
=============================

eLabLink is authored and maintained by Thibaut Jacqmin. Keep changes small,
readable and appropriate for students using laboratory code. Preserve authorship
and third-party notices.

Run offline tests
-----------------

From the project root, with the current Labmate checkout and the plotting
dependencies installed:

.. code-block:: console

   python run_tests.py

The default suite uses fake clients and temporary files, not server credentials.
Live integration tests are skipped unless ``ELABLINK_LIVE_TESTS=1`` is explicitly
set. Leave that variable unset for offline testing.

To deliberately test a disposable server, first review
``tests/test_experiment.py``. It requires ``ELABLINK_CONFIG`` pointing to
a test configuration; the resource test also requires
``ELABLINK_RESOURCE_CATEGORY_ID``. These tests create real records and leave
them for inspection. Do not point them at a production configuration by accident.

Build the documentation
-----------------------

Use Python 3.12, matching the Read the Docs configuration:

.. code-block:: console

   python -m pip install --use-pep517 -e . -r docs/requirements.txt
   python -m sphinx -W --keep-going -b html docs docs/_build/html

Open ``docs/_build/html/index.html``. Sphinx imports the library for API
documentation but does not instantiate clients or run examples. Labmate is
mocked for autodoc, so the documentation can also build with only the core
package installed. ``-W`` reports documentation warnings as failures.

Keep three examples
-------------------

* ``examples/experiments_and_resources.py``: direct eLabFTW record operations.
* ``examples/labmate_notebook.ipynb``: acquisition and analysis in Jupyter.
* ``examples/labmate_acquisition.py``: the equivalent standalone Python workflow.

Keep their imports and explanations short. No example-specific helper module
is needed. The Python examples are linear, executable scripts: no ``main()``
function, main guard, argument parser or configuration handling. Test them
with fake clients rather than importing them against a real server.
Store user-generated data and private configuration outside the tracked examples.

Publish on Read the Docs
------------------------

The repository includes ``.readthedocs.yaml``. Import the GitHub repository into
your Read the Docs account, select the branch to build and let the service
install the package and ``docs/requirements.txt``. It then builds ``docs/conf.py``.

Never give the documentation build an API key or a production configuration.
Preparing these files does not itself publish a site; publication is controlled
through the repository owner's Read the Docs account.
