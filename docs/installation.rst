Installation and configuration
==============================

eLabmate requires Python 3.10 or newer, an accessible eLabFTW server, and an API
key with permission to edit the relevant records. Ask your laboratory's server
administrator if you do not have an API key yet.

eLabFTW only
------------

From an eLabmate checkout:

.. code-block:: console

   python -m pip install --use-pep517 -e .

``-e`` means **editable** or developer mode: Python imports this checkout, so
source-code changes do not usually require a new installation. Do not use the
deprecated ``python setup.py develop`` command.

Labmate acquisition and analysis
--------------------------------

Install Labmate as well. This integration requires a Labmate checkout that
provides the ``analysis_figure_saved`` lifecycle hook. A version number alone
does not establish that the hook is available. If you use the two neighbouring
local checkouts discussed here, run from the eLabmate project folder:

.. code-block:: console

   python -m pip install --use-pep517 -e ../labmate_kyrylo -e . numpy matplotlib ipykernel

In a **Jupyter notebook**, use ``%pip`` so installation targets the notebook's
own Python environment:

.. code-block:: python

   %pip install --use-pep517 -e "../labmate_kyrylo" -e "." numpy matplotlib

Adjust the paths for your machine. For example, an absolute Windows path is
valid inside quotes. Restart the notebook kernel after installation or after
changing an imported class.

If an older environment still invokes ``setup.py develop`` or fails while
preparing an editable install, first update its packaging tools:

.. code-block:: python

   %pip install --upgrade pip setuptools wheel

For an environment that must use its existing build tools, the editable command
can also take ``--no-build-isolation``. The environment must already contain
``setuptools`` and ``wheel``. A deprecation warning is not the underlying error:
inspect the final lines of a failed installation's output.

.. tip::

   ``import sys; print(sys.executable)`` shows which Python runs the current
   notebook. Installing from a separate terminal can target a different
   environment.

Create your configuration
-------------------------

Create ``elabmate.conf`` in the folder from which you run the notebook or script.
Use one ``KEY=VALUE`` per line, without quotation marks or leading spaces:

.. code-block:: ini

   API_HOST_URL=https://your-elabftw-server.example/api/v2
   API_KEY=replace-with-your-api-key
   UNIQUE_EXPERIMENTS_TITLES=true
   VERIFY_SSL=true

These four keys are required. Optionally add ``TEAM_ID=1`` using your real team
ID if the client cannot resolve the current team. Names, categories, statuses
and permissions depend on your server.

``VERIFY_SSL=true`` verifies the server's TLS certificate. Keep this enabled for
normal use; resolve certificate problems with your administrator. Do not copy
an example API key, commit your real key, or share a notebook containing it.

To use another configuration file:

.. code-block:: python

   client = ElabClient("another.conf")
   aqm = AcquisitionAnalysisManager("data", config_path="another.conf")

Use the matching import from the relevant guide before these lines. Relative
paths are resolved from the current working directory, not automatically from
the Python file's location. ``LABMATE_DATA_DIR`` remains a compatibility setting
read by ``client.get_labmate_data_dir()``; the acquisition manager uses its
explicit ``data_directory`` argument instead.

What does title uniqueness mean?
--------------------------------

With ``UNIQUE_EXPERIMENTS_TITLES=true``, creating a second experiment with an
existing exact title raises ``DuplicateTitle`` in the direct client. The
acquisition integration catches that case and reuses the experiment.

With ``false``, the direct client can create duplicate titles, and a new
acquisition manager can create another experiment with the same title. Within
one manager, repeated acquisition names still reuse its cached experiment.
Prefer a numeric experiment ID when duplicate titles make selection ambiguous.
