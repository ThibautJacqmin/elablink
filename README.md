# eLabLink

<img src="docs/_static/elablink-logo.png" alt="An open physics laboratory notebook, an atom and a laser beam" width="180">

Work with your eLabFTW notebook from Python.

eLabLink turns familiar eLabFTW concepts into Python objects: an `Experiment`
for an experiment, a `Resource` for a sample or instrument, and properties and
methods for notes, steps, tags, files and links. It is a practical interface to
these parts of eLabFTW, not a replacement for every feature of its GUI.

```python
from elablink import Experiment, Resource

experiment = Experiment(title="Laser calibration")
experiment.main_text = "<p>Measure the laser power.</p>"
experiment.add_step("Align the beam")
experiment.add_comment("The signal is stable.")

laser = Resource(title="Blue laser", category=3)  # Your resource category ID.
laser.set_extra_fields({"Wavelength (nm)": 405})
experiment.link_resource(laser.ID)
```

These calls modify your server immediately. First install the project and
configure your connection once in the project root:
[installation](docs/installation.rst).
The [experiment and resource guide](docs/experiments_and_resources.rst) explains
loading existing records, editing properties and attaching files.

## Acquisition and analysis with Labmate

[Labmate](https://kyrylo-gr.github.io/labmate/) separates data acquisition from
analysis and keeps the saved data and code together. eLabLink adds a second
copy in eLabFTW: local saves happen first, then network uploads run in the
background, so a slow server does not hold up the next acquisition cell.

```python
from elablink import AcquisitionAnalysisManager

aqm = AcquisitionAnalysisManager("data")
```

Use Labmate's usual acquisition and analysis calls, then `aqm.close()` to finish
the uploads. After a save, `aqm.experiment.add_step("Measurement complete")`
also queues a note without waiting for the network. No configuration paths are
needed in measurement code. See the
[notebook guide](docs/notebooks.rst), [script guide](docs/scripts.rst) and
[upload behaviour](docs/background_uploads.rst).

## Three examples

- [Experiments and resources](examples/experiments_and_resources.py): create
  records, edit their properties and link them.
- [Labmate notebook](examples/labmate_notebook.ipynb): acquire a sinusoid and
  save its plot in Jupyter or VS Code.
- [Labmate script](examples/labmate_acquisition.py): the same workflow in Python,
  including access to the eLabFTW experiment.

## Installation and documentation

From this checkout, in the Python environment you use:

```console
python -m pip install --use-pep517 -e .
```

Python 3.10 or newer is required. Labmate is optional for the direct eLabFTW
interface; the acquisition guides explain its installation. In a notebook,
use `%pip` in the kernel's environment and restart the kernel after installing.

The documentation is configured for Read the Docs. Build it locally with
Python 3.12:

```console
python -m pip install --use-pep517 -e . -r docs/requirements.txt
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html`. Building the documentation does not run the
examples or contact a server. See [development](docs/contributing.rst) for tests.

## Author and acknowledgements

eLabLink is written and maintained by **Thibaut Jacqmin**. See
[authorship](AUTHORS.rst), [credits](docs/about.rst), [citation](CITATION.cff)
and the [MIT licence](LICENSE).

eLabFTW, elabapi-python and Labmate are independent projects with their own
authors and licences. eLabLink is not an official product of those projects.
