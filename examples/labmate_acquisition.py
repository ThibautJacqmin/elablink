"""Acquire and plot a sinusoid, saving locally and uploading to eLabFTW.

Run from the project folder:
    python examples/labmate_acquisition.py

The configured eLabFTW server receives a Sinusoid experiment with the data,
this script and the figure. Local files are kept in data/.

Author: Thibaut Jacqmin
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from elablink import AcquisitionAnalysisManager


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="elablink.conf")
    parser.add_argument("--data", default="data")
    args = parser.parse_args(argv)

    # In a script there are no notebook cells: save this file as the source code.
    source = Path(__file__).read_text(encoding="utf-8")
    with AcquisitionAnalysisManager(
        args.data, config_path=args.config, shell=None, use_magic=False
    ) as aqm:
        aqm.acquisition_cell("Sinusoid", cell=source)
        t = np.linspace(0, 1, 500)
        y = np.sin(2 * np.pi * 5 * t)
        aqm.save_acquisition(t=t, y=y)

        aqm.analysis_cell(cell=source)
        figure, axes = plt.subplots()
        try:
            axes.plot(aqm.data.t, aqm.data.y)
            axes.set(xlabel="Time (s)", ylabel="Signal (V)")
            aqm.save_fig(fig=figure, name="sinusoid.png", cell=source)
        finally:
            plt.close(figure)

        # This handle edits the experiment in the same background queue.
        experiment = aqm.experiment
        experiment.add_step("Sinusoid acquired and plotted.")
        experiment.add_comment("The signal is stable.")
        experiment.set_extra_fields({"Frequency (Hz)": 5})

        # Wait at the end, not during acquisition; also report any upload error.
        info = aqm.flush()
        print(info["url"])
    return info


if __name__ == "__main__":
    main()
