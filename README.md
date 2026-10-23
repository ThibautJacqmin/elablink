# elabmate

Python helpers to work with eLabFTW from Python and (optionally) sync Labmate
acquisitions.

## Features
- `ElabClient`: high-level wrapper around the eLabFTW API client.
- `ElabExperiment`: convenience helpers for tags, steps, comments, and uploads.
- `ElabResource`: the same conveniences for eLabFTW resources/items.
- `ElabBridge`: optional Labmate backend to sync acquisitions with eLabFTW.

## Installation

Editable install for local development:

```bash
python -m pip install -e .
```

## Configuration

Create a configuration file (default: `elabmate.conf`) with one `KEY=VALUE`
per line:

```ini
API_HOST_URL=https://elabftw_server_ip_address/api/v2
API_KEY= you API key, generated from the eLabFTW interface: Settings/API Key
UNIQUE_EXPERIMENTS_TITLES=true
VERIFY_SSL=false
TEAM_ID= check the team id (integer 1, 2, 3...) on eLabFTW interface: SysAdmin panel/TEAMS
LABMATE_DATA_DIR=optional, only if you use labmate
```

Notes:
- `TEAM_ID` is optional; when absent, the client tries to resolve it from the API.
- `VERIFY_SSL` accepts `true` or `false`, depending on how you configured the server

## Quickstart

```python
from elabmate import ElabClient

client = ElabClient()
exp = client.create_experiment(title="My experiment")

exp.main_text = "Experiment notes..."
exp.add_tag("demo")
exp.upload_file("path/to/file.dat")
```

The editable date shown as **Started on** is separate from eLabFTW's immutable
record creation timestamp:

```python
exp.started_on = "2021-06-18"
exp.status = "Finished"
exp.set_extra_fields({"Author": "Ada", "Sample location": "Freezer A"})
exp.ensure_step("Prepare the sample")
```

Resources and links are supported too:

```python
sample = client.create_resource("Sample A", category=3)
exp.link_resource(sample.ID)
child = client.create_experiment("Analysis of Sample A")
exp.link_experiment(child.ID)
```

## Labmate integration

`ElabBridge` implements a Labmate acquisition backend interface. You can
register it where Labmate expects a backend:

```python
from elabmate import ElabBridge

backend = ElabBridge(client)
# Attach `backend` to your Labmate acquisition manager.
```

## Tests

Run integration tests (requires a valid eLabFTW server/config):

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## References
- eLabFTW documentation: https://doc.elabftw.net/
- eLabFTW API docs: https://doc.elabftw.net/api/
- Labmate docs: https://kyrylo-gr.github.io/labmate/
- Labmate GitHub: https://github.com/kyrylo-gr/labmate
