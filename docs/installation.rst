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
   UNIQUE_EXPERIMENTS_TITLES=true
   TEAM_ID=1
   LABMATE_DATA_DIR=your_labmate_data_dir_path

With ``UNIQUE_EXPERIMENTS_TITLES=true``, creating another experiment with the same exact title raises ``DuplicateTitle``. TEAM_ID is optional and can be used to restrict the experiments to a specific team. If not set, the default team is used.  

LABMATE_DATA_DIR=your_labmate_data_dir_path is optional and can be used to specify the path to the Labmate data directory, if Labmate is used. If not set, the default Labmate data directory is used. 

The connection is configured once for the project. Its location is declared in the root ``pyproject.toml``:

.. code-block:: toml

   [tool.elablink]
   config_path = "path_of_conf_fileelablink.conf"

For advanced use outside this project, ``Client(config_path="...")`` and the acquisition manager's ``config_path`` argument remain available.

Add Labmate for acquisition/analysis
------------------------------------

Install `Labmate <https://kyrylo-gr.github.io/labmate/>__` in the same environment as eLabLink (check the Labmate documentation for installation instructions).              
