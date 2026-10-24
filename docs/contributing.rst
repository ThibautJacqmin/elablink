Development and documentation
=============================

eLabmate is authored and maintained by Thibaut Jacqmin. Keep contributions
small, readable and appropriate for laboratory users; preserve attribution
and existing third-party notices.

Run offline checks
------------------

From the repository root:

.. code-block:: console

   python -m unittest tests.test_async_bridge tests.test_acquisition_analysis_manager tests.test_async_experiment tests.test_entity_helpers

The Labmate-related tests need the compatible Labmate installation described
in :doc:`installation`. Tests for the direct entity helpers do not need a live
server. Review a live integration test before running it: such tests can create
or change real records. Do not run unrestricted test discovery against a
configured production server.

Build this documentation
-------------------------

Use Python 3.12 for documentation, matching the Read the Docs configuration.
The package itself also supports Python 3.10, but the documentation toolchain
has a newer Python requirement.

.. code-block:: console

   python -m pip install --use-pep517 -e . -r docs/requirements.txt
   python -m sphinx -W --keep-going -b html docs docs/_build/html

Open ``docs/_build/html/index.html``. This build imports the library for API
documentation, but does not instantiate a client or execute example code.
Labmate is mocked for autodoc, so a core-only installation can build the docs.

Publish on Read the Docs
------------------------

The repository includes ``.readthedocs.yaml`` using the
`Read the Docs v2 configuration format
<https://docs.readthedocs.com/platform/stable/config-file/v2.html>`_. Import
the GitHub repository into your own Read the Docs account and select the branch
you want to publish. The service installs the package and
``docs/requirements.txt``, then builds ``docs/conf.py``.

Never add an API key or a production configuration to the documentation build.
The guides display examples; they do not run them. Publishing requires the
repository owner's account and is separate from preparing these source files.

The API pages use Sphinx's
`autodoc extension <https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html>`_
with Napoleon for Google-style docstrings. A warnings-as-errors build catches
broken internal references and missing included example files before publishing.
