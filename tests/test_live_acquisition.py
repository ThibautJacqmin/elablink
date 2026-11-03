"""Opt-in real-server checks of Labmate acquisitions and background uploads.

Only uniquely named test experiments and files inside the live report directory
are modified. The upload gate and injected connection failure are test controls;
successful transfers and all downloaded-content checks use the real server.
"""

from contextlib import redirect_stderr, redirect_stdout, suppress
import io
import os
from pathlib import Path
import threading
import time
import unittest

from tests.live_support import LiveServerTestCase

try:
    import h5py
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from PIL import Image
    from IPython.core.interactiveshell import InteractiveShell
    from IPython.core.profiledir import ProfileDir
    from traitlets.config import Config
    from elablink import AcquisitionAnalysisManager
    HAS_ACQUISITION = True
except ImportError:
    HAS_ACQUISITION = False


@unittest.skipUnless(
    os.environ.get("ELABLINK_LIVE_TESTS") == "1",
    "Real eLabFTW tests require ELABLINK_LIVE_TESTS=1",
)
@unittest.skipUnless(HAS_ACQUISITION, "Requires current Labmate, plotting and IPython")
class TestLiveAcquisition(LiveServerTestCase):
    def local_directory(self, label):
        directory = (
            self.report_dir / "acq" / self._testMethodName[5:29] / label
        ).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def manager(self, directory, *, factory=None, **kwargs):
        manager = AcquisitionAnalysisManager(
            str(directory),
            shell=None,
            use_magic=False,
            save_files=True,
            client_factory=factory or self.make_client,
            **kwargs,
        )

        def close():
            # A failure is asserted at flush; keep its snapshot available.
            with suppress(RuntimeError, TimeoutError):
                manager.close(timeout=120)

        self.addCleanup(close)
        return manager

    @staticmethod
    def filepath(manager):
        path = Path(str(manager.data.filepath))
        return path if path.suffix == ".h5" else Path(str(path) + ".h5")

    @staticmethod
    def acquire(manager, label="signal"):
        manager.acquisition_cell(
            label,
            cell="t = np.linspace(0, 1, 500)\ny = np.sin(2 * np.pi * 5 * t)",
        )
        t = np.linspace(0, 1, 500)
        manager.save_acquisition(t=t, y=np.sin(2 * np.pi * 5 * t))

    @staticmethod
    def plot(manager, name="sinusoid.png"):
        fig, ax = plt.subplots()
        try:
            ax.plot(manager.data.t, manager.data.y)
            manager.save_fig(
                fig=fig, name=name, cell="plt.plot(aqm.data.t, aqm.data.y)"
            )
        finally:
            plt.close(fig)

    def read_attachment(self, remote, name):
        remote.refresh()
        attachment = remote.get_file(name)
        self.assertIsNotNone(attachment, f"Missing real server attachment {name}")
        destination = self.local_directory("download_checks") / name
        remote.download_file(attachment["id"], destination)
        return destination

    def verify_hdf5(self, remote, name, *, expected_analysis=True):
        downloaded = self.read_attachment(remote, name)
        with h5py.File(downloaded, "r") as saved:
            np.testing.assert_allclose(
                saved["y"][()], np.sin(2 * np.pi * 5 * saved["t"][()])
            )
            acquisition = saved["acquisition_cell"]["1"][()].decode()
            self.assertIn("np.sin", acquisition)
            if expected_analysis:
                self.assertTrue(list(saved["analysis_cells"].keys()))
        return downloaded

    def verify_plot(self, path):
        """Check actual PNG decoding, dimensions and complete pixel data."""
        with Image.open(path) as image:
            self.assertEqual(image.format, "PNG")
            image.verify()
        with Image.open(path) as image:
            image.load()
            self.assertGreater(min(image.size), 10)

    def test_local_acquisition_and_figure_continue_while_real_upload_waits(self):
        remote = self.new_experiment("ASYNC GATED SINUSOID")
        started, release = threading.Event(), threading.Event()
        owner = threading.get_ident()
        upload_threads = []

        def factory():
            client = self.make_client()
            actual_upload = client.uploads.post_upload

            def upload(*args, **kwargs):
                upload_threads.append(threading.get_ident())
                if not started.is_set():
                    started.set()
                    if not release.wait(60):
                        raise TimeoutError("Live test did not release its upload gate")
                return actual_upload(*args, **kwargs)

            client.uploads.post_upload = upload
            return client

        manager = self.manager(
            self.local_directory("data"),
            factory=factory,
            experiment_id=remote.ID,
        )
        self.addCleanup(release.set)
        self.acquire(manager)
        self.assertTrue(started.wait(45), "The background worker never reached upload")
        manager.analysis_cell(cell="plt.plot(aqm.data.t, aqm.data.y)")
        self.plot(manager)
        path = self.filepath(manager)
        self.assertTrue(path.is_file())
        self.assertTrue(list(path.parent.glob("*sinusoid.png")))
        self.assertFalse(release.is_set())
        self.assertTrue(any(row["state"] == "pending" for row in manager.upload_status()))
        with h5py.File(path, "r") as local:
            self.assertEqual(len(local["t"]), 500)
        release.set()
        information = manager.flush(timeout=180)
        self.assertEqual(information["id"], remote.ID)
        self.assertTrue(upload_threads)
        self.assertTrue(all(thread != owner for thread in upload_threads))
        # Deliberately verify the server before close(), not after shutdown.
        remote = self.client.load_experiment(ID=remote.ID)
        self.verify_hdf5(remote, path.name)
        png = next(row for row in remote.list_files()
                   if (row.get("real_name") or row.get("name", "")).endswith("sinusoid.png"))
        downloaded = self.read_attachment(remote, png.get("real_name") or png["name"])
        self.verify_plot(downloaded)

        experiment = manager.experiment
        categories = experiment.categories
        statuses = experiment.statuses
        available_tags = experiment.available_tags
        self.assertIsInstance(experiment.tags, list)
        if categories:
            category = next(iter(categories))
            experiment.category = category
        if statuses:
            status = next(iter(statuses))
            experiment.status = status
        if available_tags:
            tag = next(iter(available_tags))
            experiment.add_tag(tag)
        experiment.main_text = "<p>Live asynchronous sinusoid test.</p>"
        experiment.add_step("Acquired", checked=True)
        experiment.add_step("Analysed")
        experiment.add_comment("Real server checkpoint verified before close().")
        experiment.set_extra_fields({"Frequency (Hz)": 5})
        manager.flush(timeout=180)
        experiment.steps["Analysed"].status = True
        manager.flush(timeout=180)
        self.assertTrue(experiment.steps["Acquired"].status)
        self.assertTrue(experiment.steps["Analysed"].status)
        if categories:
            self.assertEqual(experiment.category, category)
        if statuses:
            self.assertEqual(experiment.status, status)
        if available_tags:
            self.assertIn(tag, experiment.tags)
        # Use a fresh synchronous wrapper so an async snapshot cannot hide failures.
        confirmed = self.client.load_experiment(ID=remote.ID)
        self.assertIn("Live asynchronous sinusoid test", confirmed.main_text)
        self.assertIn("Real server checkpoint verified before close().", confirmed.comments)
        self.assertEqual(confirmed.metadata["extra_fields"]["Frequency (Hz)"]["value"], 5)

        # Saving a changed plot under the same name must replace its current version.
        previous_png = confirmed.get_file(png.get("real_name") or png["name"])
        fig, ax = plt.subplots()
        try:
            ax.plot(manager.data.t, 2 * manager.data.y)
            manager.save_fig(fig=fig, name="sinusoid.png",
                             cell="plt.plot(aqm.data.t, 2 * aqm.data.y)")
        finally:
            plt.close(fig)
        manager.flush(timeout=180)
        confirmed.refresh()
        current_png = confirmed.get_file(previous_png["real_name"])
        self.assertNotEqual(current_png["id"], previous_png["id"])
        self.assertEqual(len([row for row in confirmed.list_files()
                              if row["real_name"] == previous_png["real_name"]]), 1)
        updated = self.verify_hdf5(confirmed, path.name)
        with h5py.File(updated, "r") as saved:
            self.assertIn("2 *", saved["analysis_cells"]["sinusoid.png"][()].decode())
        manager.close(timeout=180)

    def test_invalid_async_choices_report_available_lists(self):
        remote = self.new_experiment("INVALID ASYNC CHOICES")
        manager = self.manager(self.local_directory("data"), experiment_id=remote.ID)
        self.acquire(manager)
        manager.flush(timeout=180)
        handle = manager.experiment
        handle.category = f"missing-category-{self.run_id}"
        handle.status = f"missing-status-{self.run_id}"
        handle.add_tag(f"missing-tag-{self.run_id}")
        with self.assertRaisesRegex(RuntimeError, "Available categories:"):
            manager.flush(timeout=180)
        errors = [row["error"] for row in manager.upload_status() if row["state"] == "failed"]
        self.assertEqual(len(errors), 3)
        for label in ("categories", "statuses", "tags"):
            self.assertTrue(any(f"Available {label}:" in error for error in errors))
        confirmed = self.client.load_experiment(ID=remote.ID)
        self.assertNotIn(f"missing-tag-{self.run_id}", confirmed.tags)

    def test_remote_analysis_reuses_local_then_recreates_missing_working_directory(self):
        remote = self.new_experiment("REMOTE REANALYSIS")
        directory = self.local_directory("data")
        manager = self.manager(directory, experiment_id=remote.ID)
        self.acquire(manager)
        manager.analysis_cell(cell="plt.plot(aqm.data.t, aqm.data.y)")
        self.plot(manager)
        original = self.filepath(manager).resolve()
        manager.flush(timeout=180)
        manager.close(timeout=180)

        with h5py.File(original, "a") as local:
            local["local_only_note"] = "Preserve the local version"
        before = original.read_bytes()
        downloads = []

        def counted_client():
            client = self.make_client()
            actual_read = client.uploads.read_upload

            def download(*args, **kwargs):
                downloads.append(threading.get_ident())
                return actual_read(*args, **kwargs)

            client.uploads.read_upload = download
            return client

        reopened = self.manager(directory, factory=counted_client)
        reopened.analysis_cell(
            experiment_id=remote.ID, filename=original.name, cell="local reuse"
        )
        self.assertEqual(self.filepath(reopened).resolve(), original)
        self.assertEqual(downloads, [])
        # Opening saves analysis code; the scientific local-only value must remain.
        self.assertIn("local_only_note", reopened.data)
        with h5py.File(original, "r") as local:
            self.assertEqual(local["local_only_note"][()].decode(), "Preserve the local version")
        self.assertTrue(before)
        reopened.close(timeout=180)

        # Simulate a missing acquisition directory by moving only this test's files.
        backup = original.parent.with_name(original.parent.name + "_saved_by_live_test")
        self.assertTrue(original.parent.is_relative_to(directory))
        self.assertTrue(backup.is_relative_to(directory))
        self.assertFalse(backup.exists())
        original.parent.rename(backup)
        self.assertFalse(original.exists())
        remote.title = remote.title + " RENAMED ON SERVER"

        downloaded = self.manager(directory, factory=counted_client)
        downloaded.analysis_cell(
            experiment_id=remote.ID, filename=original.name, cell="remote reanalysis"
        )
        path = self.filepath(downloaded).resolve()
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(directory / ".elabftw"))
        self.assertNotEqual(path, original)
        self.assertTrue(downloads)
        self.assertNotIn("local_only_note", downloaded.data)
        downloaded.data["live_reanalysis"] = 25
        self.plot(downloaded, "reanalysis.png")
        downloaded.experiment.add_comment("Reanalysed after the local directory disappeared.")
        information = downloaded.flush(timeout=180)
        self.assertEqual(information["id"], remote.ID)
        downloaded.close(timeout=180)
        fresh = self.client.load_experiment(ID=remote.ID)
        self.assertTrue(fresh.title.endswith("RENAMED ON SERVER"))
        h5 = self.read_attachment(fresh, original.name)
        with h5py.File(h5, "r") as saved:
            self.assertEqual(saved["live_reanalysis"][()], 25)
        self.assertTrue(any((row.get("real_name") or row.get("name", "")).endswith("reanalysis.png")
                            for row in fresh.list_files()))

        # A third manager opens the downloaded path directly and keeps the same ID.
        later = self.manager(directory)
        later.analysis_cell(filepath=path, cell="analysis using a local path")
        later.experiment.add_comment("Reopened the downloaded file by local path.")
        self.plot(later, "later.png")
        self.assertEqual(later.flush(timeout=180)["id"], remote.ID)
        later.close(timeout=180)

    def test_repeated_acquisition_name_uploads_both_checkpoints_to_one_experiment(self):
        title = self.unique_title("REPEATED ACQUISITION")
        manager = self.manager(self.local_directory("data"), experiment_title=title)
        self.acquire(manager, "repeat")
        first = Path(str(manager.current_filepath) + ".h5").resolve()
        first_info = manager.flush(timeout=180)
        self.track_record(first_info)
        # Labmate intentionally has second-resolution acquisition names.
        time.sleep(1.1)
        self.acquire(manager, "repeat")
        second = Path(str(manager.current_filepath) + ".h5").resolve()
        second_info = manager.flush(timeout=180)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, second.parent)
        self.assertEqual(first_info["id"], second_info["id"])
        remote = self.client.load_experiment(ID=first_info["id"])
        self.verify_hdf5(remote, first.name, expected_analysis=False)
        self.verify_hdf5(remote, second.name, expected_analysis=False)
        manager.close(timeout=180)

    def test_injected_transport_failure_preserves_local_data_and_snapshot(self):
        remote = self.new_experiment("INJECTED NETWORK FAILURE")
        entered = threading.Event()

        def factory():
            client = self.make_client()

            def interrupted(*args, **kwargs):
                entered.set()
                raise ConnectionError("Live test injected a connection interruption")

            client.uploads.post_upload = interrupted
            return client

        directory = self.local_directory("data")
        manager = self.manager(directory, factory=factory, experiment_id=remote.ID)
        self.acquire(manager)
        self.assertTrue(entered.wait(45))
        with self.assertRaisesRegex(RuntimeError, "injected a connection interruption"):
            manager.flush(timeout=120)
        self.assertTrue(Path(str(manager.current_filepath) + ".h5").is_file())
        retained = list((directory / ".upload_queue").rglob("*.h5"))
        self.assertTrue(retained)
        with h5py.File(retained[0], "r") as saved:
            self.assertEqual(len(saved["y"]), 500)
        self.assertTrue(any(row["state"] == "failed" for row in manager.upload_status()))
        with self.assertRaisesRegex(RuntimeError, "injected a connection interruption"):
            manager.close(timeout=120)

    def test_jupyter_cells_capture_code_and_upload_real_data_and_plot(self):
        remote = self.new_experiment("JUPYTER SINUSOID")
        directory = self.local_directory("data")

        class LiveNotebookShell(InteractiveShell):
            def get_parent(self):
                return {"content": {"code": self.history_manager.input_hist_raw[-1]}}

        shell = LiveNotebookShell.instance(
            ipython_dir=str(self.local_directory("ipython")),
            profile_dir=ProfileDir.create_profile_dir(str(self.local_directory("profile"))),
            config=Config({"HistoryManager": {"hist_file": ":memory:"}}),
        )
        shell.user_ns.update(
            AcquisitionAnalysisManager=AcquisitionAnalysisManager,
            live_directory=str(directory),
            live_experiment_id=remote.ID,
            live_client_factory=self.make_client,
            np=np,
            plt=plt,
        )
        cells = [
            "aqm = AcquisitionAnalysisManager(live_directory, experiment_id=live_experiment_id, "
            "client_factory=live_client_factory)",
            'aqm.acquisition_cell("notebook_signal")\n'
            "t = np.linspace(0, 1, 500)\n"
            "y = np.sin(2 * np.pi * 5 * t)\n"
            "aqm.save_acquisition(t=t, y=y)",
            "aqm.analysis_cell()\n"
            "fig, ax = plt.subplots()\n"
            "ax.plot(aqm.data.t, aqm.data.y)\n"
            'aqm.save_fig(fig=fig, name="notebook_sinusoid.png")\n'
            "plt.close(fig)",
            'aqm.experiment.add_step("Notebook completed", checked=True)\n'
            'aqm.experiment.add_comment("Real IPython cells completed.")\n'
            "information = aqm.flush(timeout=180)",
        ]
        try:
            for source in cells:
                # IPython formats its own traceback before returning an error.
                # Let the live runner report it without raw response headers.
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = shell.run_cell(source, store_history=True)
                if result.error_before_exec:
                    raise result.error_before_exec
                if result.error_in_exec:
                    raise result.error_in_exec
            manager = shell.user_ns["aqm"]
            self.assertIs(manager.shell, shell)
            self.assertEqual(shell.user_ns["information"]["id"], remote.ID)
            h5 = self.verify_hdf5(
                self.client.load_experiment(ID=remote.ID), self.filepath(manager).name
            )
            with h5py.File(h5, "r") as saved:
                self.assertIn("np.sin", saved["acquisition_cell"]["1"][()].decode())
                self.assertIn(
                    "ax.plot", saved["analysis_cells"]["notebook_sinusoid.png"][()].decode()
                )
            confirmed = self.client.load_experiment(ID=remote.ID)
            png = next(row for row in confirmed.list_files()
                       if (row.get("real_name") or row.get("name", "")).endswith(
                           "notebook_sinusoid.png"))
            self.verify_plot(self.read_attachment(
                confirmed, png.get("real_name") or png["name"]))
            self.assertTrue(confirmed.steps["Notebook completed"].status)
            self.assertIn("Real IPython cells completed.", confirmed.comments)
        finally:
            try:
                if "aqm" in shell.user_ns:
                    shell.user_ns["aqm"].close(timeout=180)
            finally:
                try:
                    shell.history_manager.end_session()
                finally:
                    LiveNotebookShell.clear_instance()
