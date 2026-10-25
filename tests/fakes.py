"""In-memory eLabFTW clients for offline tests."""

import threading
from pathlib import Path
from types import SimpleNamespace


class OfflineExperiment:
    ID = 1

    def __init__(self):
        self.files = {}
        self.steps = []
        self.thread_ids = []

    def upload_file(self, file_path):
        self.thread_ids.append(threading.get_ident())
        path = Path(file_path)
        self.files[path.name] = path.read_bytes()

    def add_step(self, text):
        self.thread_ids.append(threading.get_ident())
        self.steps.append(text)


class OfflineClient:
    def __init__(self):
        self.configuration = SimpleNamespace(host="https://offline.invalid/api/v2")
        self.experiment = OfflineExperiment()
        self.created = 0

    def create_experiment(self, *, title):
        self.created += 1
        return self.experiment

    def load_experiment(self, *, ID):  # noqa: N803 - Client API
        if ID != self.experiment.ID:
            raise ValueError("The offline client only provides experiment 1")
        return self.experiment
