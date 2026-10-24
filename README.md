# eLabmate

<img src="docs/_static/elabmate-logo.png" alt="eLabmate: a physics laboratory notebook" width="180">

Python measurements, lab notebook records.

**Written and maintained by Thibaut Jacqmin.** eLabmate provides convenient
Python access to eLabFTW experiments and resources, with an optional Labmate
manager that saves locally and uploads checkpoints in the background.

## Choose your workflow

- [eLabFTW only](docs/elabftw_only.rst): create experiments/resources, edit notes
  and custom fields, add steps/comments/tags, attach files and link records.
- [Jupyter acquisition](docs/notebooks.rst): keep Labmate's familiar acquisition
  and analysis cells, with automatic background uploads.
- [Python script acquisition](docs/scripts.rst): the same saves without a
  notebook, including an executable sinusoid example.

## Installation

Python 3.10 or newer is required. From this checkout, install in editable
(developer) mode:

```console
python -m pip install --use-pep517 -e .
```

Labmate is optional for direct eLabFTW use. Acquisition integration requires
Labmate with the `analysis_figure_saved` lifecycle hook. For the neighbouring
local checkouts, run this **in your notebook's environment**:

```python
%pip install --use-pep517 -e "../labmate_kyrylo" -e "." numpy matplotlib
```

Then restart the kernel. See [installation and configuration](docs/installation.rst)
for paths, packaging-tool troubleshooting and the required `elabmate.conf`.
Keep your API key private and TLS certificate verification enabled.

## A notebook starts with

```python
from elabmate import AcquisitionAnalysisManager

aqm = AcquisitionAnalysisManager("data")
```

Use the usual `acquisition_cell()`, `save_acquisition()`, `analysis_cell()` and
`save_fig()` calls. Finish with `aqm.close()` to wait for uploads and report
failures. The [four-cell sinusoid notebook](example_labmate_elabFTW_bridge.ipynb)
is ready to try, as is the [VS Code cell script](example_labmate_acquisition_analysis.py).

Local writes and stable snapshot copies happen before background networking.
There is no automatic retry or restart recovery; read the
[failure and timeout guide](docs/background_uploads.rst) before an acquisition
campaign. The existing synchronous `ElabBridge` remains available for advanced
callers, but the manager above is the recommended notebook interface.

## Documentation and tests

The Sphinx documentation is ready for Read the Docs through `.readthedocs.yaml`.
To build it locally with Python 3.12:

```console
python -m pip install --use-pep517 -e . -r docs/requirements.txt
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html`. Documentation builds do not run the examples
or connect to a server. See [development](docs/contributing.rst) for offline
tests; run live-server tests only deliberately.

## Author and acknowledgements

Thibaut Jacqmin is the original author and maintainer. Improvements, tests and
documentation have also received AI-assisted coding support under his direction.
The repository history and existing licence notices are preserved.

eLabmate uses [eLabFTW](https://www.elabftw.net/),
[elabapi-python](https://github.com/elabftw/elabapi-python) and, optionally,
[Labmate](https://github.com/kyrylo-gr/labmate). These are independent projects
with their own authors and licences; eLabmate does not imply their endorsement.
See [about and credits](docs/about.rst), [citation metadata](CITATION.cff),
[authorship](AUTHORS.rst), and the existing [licence](LICENSE).
