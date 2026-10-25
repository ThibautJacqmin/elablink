Installation and configuration
==============================

You need Python 3.10 or newer, an eLabFTW server, and an API key that can edit
your experiments and resources. Ask your laboratory's server administrator
for an API key if needed.

Install eLabLink
----------------

From the eLabLink project folder, in the Python environment you use:

.. code-block:: console

   python -m pip install --use-pep517 -e .

``-e`` selects editable, or developer, mode: changes to this checkout are used
without reinstalling the package. Do not use ``python setup.py develop``.
Labmate is not required to work directly with experiments and resources.

After updating an existing checkout to eLabLink, run this editable installation
again, use ``from elablink import ...`` in your code and restart the kernel.
The default configuration filename is now ``elablink.conf``; rename your private
configuration accordingly or pass its path explicitly. Public classes are
``Experiment``, ``Resource``, ``Client`` and ``AsyncExperiment``.

Create your configuration
-------------------------

Copy ``elablink.conf.example`` to ``elablink.conf`` in the folder from which
you run Python, and fill in your server address and API key:

.. code-block:: ini

   API_HOST_URL=https://your-elabftw-server.example/api/v2
   API_KEY=replace-with-your-api-key
   UNIQUE_EXPERIMENTS_TITLES=true
   VERIFY_SSL=true

Use one ``KEY=VALUE`` per line, without quotation marks. These four keys are
required. Optionally add ``TEAM_ID=1`` with your real team ID if the current
team cannot be resolved automatically.

Keep this file private and out of Git. Leave ``VERIFY_SSL=true`` enabled;
ask your administrator to resolve server certificate problems.

For a different configuration path, make a client explicitly:

.. code-block:: python

   from elablink import Client

   client = Client("path/to/my.conf")
   experiment = client.create_experiment("Laser calibration")

The acquisition manager accepts ``config_path="path/to/my.conf"`` too.
Relative paths start at Python's current working directory, not automatically
at the script's location.

Add Labmate for acquisition
---------------------------

Use the current Labmate implementation, including the
``analysis_figure_saved`` lifecycle hook. If the Labmate and eLabLink checkouts
are neighbouring folders, run from the eLabLink folder:

.. code-block:: console

   python -m pip install --use-pep517 -e ../labmate_kyrylo -e . numpy matplotlib ipykernel

Adjust ``../labmate_kyrylo`` to your actual Labmate checkout.

In a Jupyter notebook
---------------------

Use ``%pip`` so installation targets the notebook's own environment. From a
notebook whose working directory is the eLabLink project root:

.. code-block:: python

   %pip install --use-pep517 -e "../labmate_kyrylo" -e "." numpy matplotlib

If its working directory is ``examples/`` instead, use:

.. code-block:: python

   %pip install --use-pep517 -e "../../labmate_kyrylo" -e ".." numpy matplotlib

``from pathlib import Path; print(Path.cwd())`` shows the working directory.
You can always use absolute checkout paths instead of these relative paths.

Restart the kernel after installation or after changing an imported class.
Select that same environment as the notebook kernel in VS Code.

If installation fails, first update the packaging tools in this environment:

.. code-block:: python

   %pip install --upgrade pip setuptools wheel

Then repeat the editable install. ``import sys; print(sys.executable)`` shows
which Python your notebook is using. A separate terminal may use another one.

Experiment titles
-----------------

With ``UNIQUE_EXPERIMENTS_TITLES=true``, creating another experiment with the
same exact title raises ``DuplicateTitle``. Load the existing record instead
when working directly with the API.

The Labmate integration reuses that existing experiment. Within one manager,
repeated acquisition names also reuse its cached experiment. With title
uniqueness disabled, a fresh manager can create a separate experiment with
the same title. Use a numeric experiment ID if a title is ambiguous.
