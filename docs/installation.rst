Installation and configuration
==============================

Choose one installation for your workflow. Both support Python 3.10 or newer.

1. **Python wrapper only (stable v0.2.0).** Follow the instructions below
   to install the API for scripts and notebooks in your project environment.
2. **Python wrapper + Attachment Browser (experimental v0.3.0a3).** Go
   straight to :doc:`attachment_browser`. Its setup includes the wrapper,
   Qt and PDF preview support in a dedicated environment.

Install the Python wrapper only
-------------------------------

Use the Python environment that runs your scripts or notebooks. On Windows,
macOS and Linux, this command installs core v0.2.0 and its API dependencies
from the `stable GitHub release <https://github.com/ThibautJacqmin/elablink/releases/tag/v0.2.0>`_.

.. code-block:: console

   python -m pip install "https://github.com/ThibautJacqmin/elablink/releases/download/v0.2.0/elablink-0.2.0-py3-none-any.whl"

To check which version that environment uses:

.. code-block:: console

   python -c "from importlib.metadata import version; print(version('elablink'))"

For an editable development install, run this from the eLabLink project folder:

.. code-block:: console

   python -m pip install --use-pep517 -e .

``-e`` selects editable mode. Edits to the checkout take effect the next time Python loads the package.

Create your configuration
-------------------------

Download the `example configuration <https://github.com/ThibautJacqmin/elablink/blob/v0.2.0/elablink.conf.example>`_
(or copy it from your checkout) and save it as ``elablink.conf``.
Enter your server address and a personal API key from eLabFTW. A read-only
key is sufficient for reading records. Editing requires a write-enabled key.

.. code-block:: ini

   API_HOST_URL=https://your-elabftw-server.example/api/v2
   API_KEY=replace-with-your-api-key
   UNIQUE_EXPERIMENTS_TITLES=true
   VERIFY_SSL=true
   TEAM_ID=1

Use HTTPS. ``VERIFY_SSL`` accepts only ``true`` or ``false``. An HTTP server
requires an explicit ``ALLOW_INSECURE_HTTP=true`` setting.

With ``UNIQUE_EXPERIMENTS_TITLES=true``, creating another experiment with the same exact title raises ``DuplicateTitle``  

TEAM_ID is optional and selects the team used to retrieve categories, statuses and tags. If not set, eLabLink tries to identify the current team.

The configuration file location is declared in the root ``pyproject.toml``:

.. code-block:: toml

   [tool.elablink]
   config_path = "/path_of_conf_file/elablink.conf"

For advanced use outside this project, ``Client(config_path="...")`` and the acquisition manager's ``config_path`` argument remain available.

Add Labmate for acquisition/analysis
------------------------------------

The acquisition manager requires Labmate 0.10.10 or newer. Install it in the
same environment as eLabLink:

.. code-block:: console

   python -m pip install "labmate>=0.10.10" matplotlib

Set the Labmate data directory when creating the acquisition manager: ``AcquisitionAnalysisManager("data")``.
