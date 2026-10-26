eLabLink
========

`eLabLink <https://github.com/ThibautJacqmin/elablink>__`` provides an intuitive, object-oriented Python interface to eLabFTW. 
Experiments, resources, and their familiar GUI features are represented by 
Python objects, properties, and methods, allowing you to interact with your 
electronic lab notebook directly from Python.eLabLink lets you use your online 
scientific lab notebook directly in your Python experimental sequences.
You can easily create an experiment or a resource, then edit its title, main text,
steps, tags, custom fields, and attachments.
.. code-block:: python
   experiment = Experiment(title="Laser calibration")
   experiment.main_text = "<p>Measure the laser power.</p>"
   experiment.add_step("Align the beam")
   experiment.add_comment("The signal is stable.")




Labmate integration
========

eLabLink also integrates with `Labmate <https://kyrylo-gr.github.io/labmate/>__`, a Python framework for saving and analyzing experimental data.

Labmate saves measurement data together with the Python code used for acquisition and analysis in HDF5 (.h5) files. This allows you to reopen previous measurements and continue analyzing them without relying on variables still present in memory. It also comes with a very useful h5 file viewer: `h5viewer <https://github.com/kyrylo-gr/h5viewer>__`, that enables fast visualisation of h5 file content, and quickly rerun an old experimental sequence.

Labmate saves your measurement/analysis data and code locally. eLabLink extends
this workflow by automatically uploading the saved data files and
generated figures to an eLabFTW experiment, while retaining the local copies. Uploads run asynchronously in the background, so you can continue acquiring data without waiting for network transfers. It provides the experiment object, such that you can madify the experiment attributes within your Python experimental sequence.

Previously saved data/code can also be retrieved from eLabFTW and reopened for further analysis. New analysis results and figures can then be saved back to the same experiment.

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Get started

   installation
   experiments_and_resources

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Acquisition with Labmate

   notebooks
   scripts
   background_uploads

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Reference

   features
   api
   contributing
   about
