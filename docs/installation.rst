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
The default configuration filename is now ``elablink.conf``. Public classes are
``Experiment``, ``Resource``, ``Client`` and ``AsyncExperiment``.

Create your configuration
-------------------------

Copy ``elablink.conf.example`` to ``elablink.conf`` in the project root, next to
``pyproject.toml``, and fill in your server address and API key:

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

The connection is configured once for the project. Its location is declared in
the root ``pyproject.toml``:

.. code-block:: toml

   [tool.elablink]
   config_path = "elablink.conf"

This path is relative to ``pyproject.toml``, not to the notebook or script.
You may change it here to point to a private file elsewhere. The setting
contains only a path; the API key stays in the private configuration file.

When you run Python from the project or one of its subfolders, eLabLink finds
this setting automatically. The same examples work from the root or from
``examples/``: there is no configuration path to add to measurement code.
For advanced use outside this project, ``Client(config_path="...")`` and the
acquisition manager's ``config_path`` argument remain available.

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

If Anaconda fails during build isolation with a ``certifi_win32`` startup error
mentioning ``wrapt`` or ``truststore``, use the already installed build tools:

.. code-block:: python

   %pip install --no-build-isolation --use-pep517 -e "."

Use your checkout path instead of ``"."`` if needed. This keeps editable mode
and does not disable certificate verification.

Experiment titles
-----------------

With ``UNIQUE_EXPERIMENTS_TITLES=true``, creating another experiment with the
same exact title raises ``DuplicateTitle``. Load the existing record instead
when working directly with the API.

The Labmate integration reuses that existing experiment. Within one manager,
repeated acquisition names also reuse its cached experiment. With title
uniqueness disabled, a fresh manager can create a separate experiment with
the same title. Use a numeric experiment ID if a title is ambiguous.
