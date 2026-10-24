"""Acquire and plot a sinusoid, saving locally and uploading to eLabFTW.

Running this script writes to the server in your configuration. Importing it does
not acquire data or contact the server.

Author: Thibaut Jacqmin
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from elabmate import AcquisitionAnalysisManager


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="elabmate.conf")
    parser.add_argument("--data", default="data")
    args = parser.parse_args(argv)

    # Without a notebook cell, save this script as the acquisition/analysis code.
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

        aqm.experiment.add_step("Sinusoid acquired and plotted.")
        info = aqm.flush()
        print(info["url"])
    return info


if __name__ == "__main__":
    main()
