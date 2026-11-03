Installation and configuration
==============================

You need Python 3.10 or newer, an eLabFTW server, and an API key that can edit
your experiments and resources. You can generate an API key from the eLabFTW interface.

Install eLabLink
----------------

From the eLabLink project folder, in the Python environment you use:

.. code-block:: console

   python -m pip install --use-pep517 -e .

``-e`` selects editable, or developer, mode: changes to this checkout are used without reinstalling the package. 

Create your configuration
-------------------------

Copy ``elablink.conf.example`` to ``elablink.conf`` in the project root or in another folder of your choice, and fill in your server address and API key:

.. code-block:: ini

   API_HOST_URL=https://your-elabftw-server.example/api/v2
   API_KEY=replace-with-your-api-key
   UNIQUE_EXPERIMENTS_TITLES=true
   VERIFY_SSL=true
   TEAM_ID=1


With ``UNIQUE_EXPERIMENTS_TITLES=true``, creating another experiment with the same exact title raises ``DuplicateTitle``  

TEAM_ID is optional and selects the team used to retrieve categories and statuses. If not set, eLabLink tries to identify the current team.

The configuration file location is declared in the root ``pyproject.toml``:

.. code-block:: toml

   [tool.elablink]
   config_path = "/path_of_conf_file/elablink.conf"

For advanced use outside this project, ``Client(config_path="...")`` and the acquisition manager's ``config_path`` argument remain available.

Add Labmate for acquisition/analysis
------------------------------------

Install `Labmate <https://kyrylo-gr.github.io/labmate/>`__ in the same environment as eLabLink (check the Labmate documentation for installation instructions).

Set the Labmate data directory when creating the acquisition manager: ``AcquisitionAnalysisManager("data")``.
