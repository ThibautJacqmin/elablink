# %% [markdown]
# # Save a sinusoid to eLabFTW
# Run the cells in order. Use your usual `elabmate.conf` in the elabmate folder.

# %% Setup
import numpy as np
import matplotlib.pyplot as plt
from elabmate import AcquisitionAnalysisManager

aqm = AcquisitionAnalysisManager("data")

# %% Acquisition
aqm.acquisition_cell("Sinusoid")
t = np.linspace(0, 1, 500)
y = np.sin(2 * np.pi * 5 * t)
aqm.save_acquisition(t=t, y=y)

# %% Analysis
aqm.analysis_cell()
plt.figure()
plt.plot(aqm.data.t, aqm.data.y)
plt.xlabel("Time (s)")
plt.ylabel("Signal (V)")
aqm.save_fig(name="sinusoid.png")
plt.show()

# %% Finish uploading
aqm.close()
