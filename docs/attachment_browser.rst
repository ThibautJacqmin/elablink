Experimental attachment browser
===============================

This guide installs the **Python wrapper + Attachment Browser** together.
For the stable **Python wrapper only**, see :doc:`installation`.

Use the desktop window to find experiments and resources and manage their files. It is **experimental** in eLabLink
0.3.0a3 and has less testing than the core Python API. The browser has been
exercised on Windows against a live eLabFTW server and with offline
experiment and resource records on Ubuntu 24.04 under WSLg, using both
Wayland and X11. The installed launcher and single-click navigation were
also tested on a macOS runner using native Qt windows. The Finder launcher
below still needs a manual check on a Mac.

.. contents:: On this page
   :local:
   :depth: 1

Before installing
-----------------

* Start with a graphical desktop and Python 3.10 or newer. Browser setup
  installs the eLabLink core library, Qt and PDF preview support together
  in a dedicated environment. You can start on a computer that has never
  had eLabLink installed. The optional Labmate acquisition dependencies are
  a separate extra.
* Windows x64 users can use the setup ZIP described below. For manual
  installation on any platform, download the `v0.3.0a3 browser wheel
  <https://github.com/ThibautJacqmin/elablink/releases/download/v0.3.0a3/elablink-0.3.0a3-py3-none-any.whl>`_. Save it in your
  **Downloads** folder. Windows, macOS and Linux use the same eLabLink wheel.
  During installation pip downloads the matching Qt and PDF libraries for
  your operating system, Python version and processor.
* Sign in to your team's eLabFTW in a web browser. If your server requires
  the lab network or VPN, use that connection to access attachments. In your
  personal **Settings → API keys**, generate a key for this computer. A
  read-only key is sufficient for searching, previewing and downloading.
  Upload, rename, replace and delete require a write-enabled key and
  permission to edit the entry. See the `eLabFTW API key guide
  <https://doc.elabftw.net/docs/usage/api/>`_.

Your configuration file
-----------------------

The commands below create an ``eLabLink Browser`` folder in your home
directory. Save a plain-text file named ``elablink.conf`` in that folder
using the following format. Put your own server address and personal key
after the equals signs.

.. code-block:: ini

   API_HOST_URL=https://eln.example.org/api/v2
   API_KEY=replace-with-your-own-key
   UNIQUE_EXPERIMENTS_TITLES=true
   VERIFY_SSL=true

If you reach eLabFTW under a subpath such as ``https://lab.example.org/eln/``,
use ``https://lab.example.org/eln/api/v2``. The software uses the API key's
current team for filter choices by default. You can add ``TEAM_ID=3`` if
your administrator has given you a specific team ID.

Keep this file in your personal account. The desktop launchers below store
only its **path**. On a shared computer, each person should create a
configuration and shortcut under their own login.

Windows double-click installer
------------------------------

1. Download the `Windows x64 setup ZIP
   <https://github.com/ThibautJacqmin/elablink/releases/download/v0.3.0a3/eLabLink-Attachment-Browser-Setup-0.3.0a3-Windows-x64.zip>`_.
   The installer needs internet access and 64-bit Python 3.10
   or newer for Windows x64. It also supports Anaconda and Miniconda.
2. In File Explorer, right-click the ZIP, choose **Extract All**, then
   double-click **Install-eLabLink-Attachment-Browser.cmd** in the extracted folder. Keep the
   helper files and wheel together. With a custom Conda installation, you
   can also run the same file from **Anaconda Prompt**.
3. Follow the prompts to use your existing configuration or enter your
   HTTPS server address and personal API key. Setup installs eLabLink and
   its Qt and PDF dependencies in a private environment and checks that Qt
   6.12.1 actually loads before creating the desktop shortcut.
4. Open **eLabLink Attachment Browser** using the shortcut reported by setup.
   For a later release, extract its ZIP and run its launcher. Setup
   checks the new installation before replacing a managed shortcut.
   Setup can reuse the configuration from an older **eLabLink Attachments**
   shortcut. The new shortcut is named **eLabLink Attachment Browser**.
   Previously installed environments are kept for recovery.

The installer is for the attachment browser. KLayout and the optional
H5viewer application are separate installations. Keep an existing browser
environment if you use its H5viewer integration.

Windows manual installation and first launch
--------------------------------------------

1. Open **PowerShell**. These steps use standard Python from python.org.
   A clean Windows installation was tested with 64-bit CPython 3.12.10.
   Run the following commands there. The version line
   should show Python 3.10 or newer. If ``py`` is unavailable, install Python
   from `python.org <https://www.python.org/downloads/>`_ or substitute
   another installed Python 3.10+ interpreter.

   .. code-block:: powershell

      py -3 --version
      $browserDir = Join-Path $HOME 'eLabLink Browser'
      py -3 -m venv "$browserDir\venv"
      Set-Location -LiteralPath "$HOME\Downloads"
      & "$browserDir\venv\Scripts\python.exe" -m pip install ".\elablink-0.3.0a3-py3-none-any.whl[browser]" "PyQt6==6.12.1" "PyQt6-Qt6==6.12.1"

2. Run ``notepad "$browserDir\elablink.conf"``. Paste the configuration
   above, fill in your values and save. If **Save As** opens, choose
   **All files** and the filename ``elablink.conf``. In Explorer you can
   turn on **View → Show → File name extensions** to verify the name.

3. Run the browser once from the same PowerShell session.

   .. code-block:: powershell

      & "$browserDir\venv\Scripts\elablink-attachments.exe" --config-path "$browserDir\elablink.conf"

   The **Experiments** tab on the left should display your experiments.
   Choose **Resources** to browse resources. Close the window after this first check.

Windows desktop shortcut (.lnk)
-------------------------------

A Windows shortcut remembers which executable to start, its arguments and a
working directory. These paths belong to the computer where you installed the
browser.

1. In PowerShell, print the two values for **this computer**. The first
   output line is **Target**. The second is **Start in**.

   .. code-block:: powershell

      $browserDir = Join-Path $HOME 'eLabLink Browser'
      $launcher = Join-Path $browserDir 'venv\Scripts\elablink-attachments.exe'
      $config = Join-Path $browserDir 'elablink.conf'
      '"{0}" --config-path "{1}"' -f $launcher, $config
      $browserDir

2. Right-click a blank area of the desktop, choose **New → Shortcut**,
   paste **Target** into **Type the location of the item**, choose **Next**,
   name it **eLabLink Attachment Browser**, and choose **Finish**.
3. Right-click the new or copied shortcut and open **Properties → Shortcut**.
   Set **Target** and **Start in** from step 1. Keep the executable path and
   the config path individually quoted in **Target**. **Start in** contains
   just the folder. For example, a user named Alex would see:

   .. code-block:: text

      Target
      "C:\Users\Alex\eLabLink Browser\venv\Scripts\elablink-attachments.exe" --config-path "C:\Users\Alex\eLabLink Browser\elablink.conf"

      Start in
      C:\Users\Alex\eLabLink Browser

4. Choose **Apply → OK**. Double-click the shortcut and single-click a
   record to check that its files appear. To use another server, create a
   second configuration, copy this shortcut, rename it and change the
   path after ``--config-path`` in **Target**.

**Adapting an existing Anaconda shortcut.** If you used the virtual
environment above, replace the old shortcut's entire **Target** with the one
from step 1. If you use Anaconda's Qt libraries, install the
browser into an activated environment with a matching Conda Qt and PyQt6
installation. Use that environment's ``python -m pip`` to install the wheel
with the ``[browser]`` extra. Test
``elablink-attachments --config-path "C:\full\path\elablink.conf"`` in its
activated Anaconda Prompt. For an installation containing Anaconda's
``cwp.py`` wrapper, the following is **one Target line**:

.. code-block:: text

   "C:\Miniconda\pythonw.exe" -I "C:\Miniconda\cwp.py" --no-console "C:\Miniconda\envs\lab" "C:\Miniconda\envs\lab\Scripts\elablink-attachments.exe" --config-path "C:\Users\Alex\eLabLink Browser\elablink.conf"

Substitute the installed base ``pythonw.exe``, base ``cwp.py``, environment
folder, environment launcher and configuration. The wrapper prepares the
Conda environment before starting Qt. If your Conda installation does not
supply this wrapper, the standard Python installation above gives you a
shortcut that directly targets the launcher. `Windows Shell Links
<https://learn.microsoft.com/en-us/windows/win32/shell/links>`_ describes
the shortcut format.

macOS and Linux installation and first launch
---------------------------------------------

Open **Terminal** and install the release wheel with its ``browser`` extra.
The commands below select the same Qt 6.12.1 runtime as the Windows setup
ZIP. Its prebuilt wheels support macOS 14+ on Intel and Apple Silicon, and
Linux with glibc 2.34+ on x86-64 or 2.39+ on ARM64. On Linux, your
distribution may package ``venv`` separately (for example,
``python3-venv`` on Debian and Ubuntu). See the
`PyQt6 wheels <https://pypi.org/project/PyQt6/6.12.1/#files>`_ and
`Qt runtime wheels <https://pypi.org/project/PyQt6-Qt6/6.12.1/#files>`_.

.. code-block:: sh

   python3 --version
   browser_dir="$HOME/eLabLink Browser"
   python3 -m venv "$browser_dir/venv"
   cd "$HOME/Downloads"
   "$browser_dir/venv/bin/python" -m pip install "./elablink-0.3.0a3-py3-none-any.whl[browser]" \
       "PyQt6==6.12.1" "PyQt6-Qt6==6.12.1"
   "$browser_dir/venv/bin/python" -c "from PyQt6.QtCore import qVersion; print(qVersion())"

Create the private config in a plain-text editor. For example, run these
commands, paste the configuration shown above, and in ``nano`` press
**Ctrl+O**, **Enter**, then **Ctrl+X**.

.. code-block:: sh

   touch "$browser_dir/elablink.conf"
   chmod 600 "$browser_dir/elablink.conf"
   nano "$browser_dir/elablink.conf"

Test the exact command that the desktop launcher will run.

.. code-block:: sh

   "$HOME/eLabLink Browser/venv/bin/elablink-attachments" --config-path "$HOME/eLabLink Browser/elablink.conf"

When your entries appear, single-click one to see its attachments.
Close the window, then create the launcher for your desktop below.

macOS Finder and Dock launcher
------------------------------

1. Open **Automator**. Choose **File → New → Application**.
2. Search for **Run Shell Script**, drag that action into the workflow and
   replace its default contents with the following line.

   .. code-block:: sh

      exec "$HOME/eLabLink Browser/venv/bin/elablink-attachments" --config-path "$HOME/eLabLink Browser/elablink.conf"

3. Choose **File → Save**, name it **eLabLink Attachment Browser** and save it on
   your Desktop. Double-click the application in Finder. You can also drag
   it onto the applications side of your Dock.
4. If the environment or config moves, open the saved application in
   **Automator**, edit the two paths in **Run Shell Script**, then save.
   For a second Mac, install its own environment and configuration first.
   The ``$HOME`` paths in the action resolve to that Mac user's home folder.
   See `Apple's Automator guide
   <https://support.apple.com/guide/automator/aut4bb6b2b4f/mac>`_.

Linux application menu launcher
-------------------------------

For a desktop that supports freedesktop application entries, run this block
in Terminal under your normal user account. The shell writes your actual
home paths into the launcher. The quotes around both paths in ``Exec=``
allow spaces in directory names.

.. code-block:: sh

   menu_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
   mkdir -p "$menu_dir"
   cat > "$menu_dir/elablink-attachments.desktop" <<EOF
   [Desktop Entry]
   Type=Application
   Name=eLabLink Attachment Browser
   Comment=Browse eLabFTW experiment and resource files
   Exec="$HOME/eLabLink Browser/venv/bin/elablink-attachments" --config-path "$HOME/eLabLink Browser/elablink.conf"
   Terminal=false
   Categories=Science;
   EOF

Open the applications menu, search for **eLabLink Attachment Browser**, and launch
it. To change the config or move the environment, edit the generated
``elablink-attachments.desktop`` in a plain-text editor. Use literal
absolute paths in the saved ``Exec=`` line, such as
``"/home/alex/eLabLink Browser/venv/bin/elablink-attachments"``.
The desktop entry reader expects fully resolved executable paths.

If your desktop supports icons on the desktop, you can also copy the
``.desktop`` file there. Mark that copy executable in its file properties
or with ``chmod +x``, then choose **Allow Launching** if your desktop offers
that action. See the `freedesktop Exec specification
<https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html>`_.

Search and open a record
------------------------

The search panel on the left stays open, including when you launch the
browser for a specific record. Drag the divider to adjust its width.
Choose **Experiments** or **Resources**, then search by text, category,
status or tag. Category and status choices belong to the
current tab. Selecting several tags requires every tag to match. Selecting
several categories or statuses accepts any selected value within that filter.
Single-click a result to show its files, or focus it and press **Enter**.

The panel reads categories, statuses and tags from eLabFTW and displays
the team's category and status colours. Switch tabs or click the panel's
refresh button to pick up team changes. The filename filter above the
attachment list applies to the open record.

From a project using the prerelease, you can also open a known record in
a separate browser window.

.. code-block:: python

   from elablink import Experiment

   experiment = Experiment(ID=123)
   experiment.browse_files()

Resources support the same ``resource.browse_files()`` method. When using a
shortcut you can open an entry directly by appending two arguments to the
launcher command, for example ``--entity-type experiments --entity-id 123``.
Use ``--entity-type items`` for a resource.

If installation or launch fails
-------------------------------

* **pip cannot find the wheel.** Check its exact filename and Downloads
  location. Use the wheel for this prerelease, including the ``[browser]``
  suffix in the pip command. If pip cannot find a compatible Qt wheel,
  check your Python version, operating system and CPU architecture.
* **A shortcut opens and disappears.** Run the first-launch command for your
  platform from a terminal. On Windows, to see Python startup errors, run
  ``& "$browserDir\venv\Scripts\python.exe" -m elablink._attachment_browser_qt --config-path "$browserDir\elablink.conf"``
  in PowerShell, with ``$browserDir`` set as above. Check both paths and
  ensure the configuration is a plain-text ``.conf`` file.
* **Windows reports “DLL load failed while importing QtCore”.** Check which
  Python created the environment. If it came from Conda, combining it with
  another Qt runtime can load incompatible DLLs. The Windows virtual
  environment instructions above use the Python Software Foundation
  distribution. Alternatively, use a matched Qt/PyQt6 installation in an
  activated Conda environment and its Conda-aware shortcut.
* **The window opens but entries do not load.** Verify that you can reach
  the server from this computer (including the VPN if applicable). Check
  the API URL, the user's key and the team. An empty result can also reflect
  the selected filters or the user's permissions. For a certificate error,
  confirm the hostname and your lab's certificate trust setup with your
  administrator.
* **Qt reports an xcb or display error on Linux.** Start the browser in a
  graphical desktop session. For a missing X11 runtime, install the packages
  identified in the error. For example, on a minimal Debian or Ubuntu X11
  desktop the following installs common Qt plugin dependencies:

  .. code-block:: sh

     sudo apt install libegl1 libgl1 libx11-xcb1 libxkbcommon-x11-0 \
       libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-randr0 \
       libxcb-render0 libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0

  On other distributions use the equivalent runtime packages. See
  `Qt's X11 requirements <https://doc.qt.io/qt-6/linux-requirements.html>`_.

Manage files
------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Feature
     - How to use it
   * - Preview
     - Select a file to see its contents in the right panel.
   * - Open
     - Double-click a file, or choose **Open**.
   * - Upload / download
     - Use **Upload** to send files or **Save as…** to save local copies.
   * - Drag and drop
     - Drag files between the browser and your computer, in either direction.
   * - Copy / paste
     - Select files and use **Ctrl+C** / **Ctrl+V** on Windows and Linux, or
       **Command+C** / **Command+V** on macOS.
   * - Rename
     - Right-click → **Rename**, or press **F2**.
   * - Delete
     - Right-click → **Delete**, or press **Suppr/Delete**, then confirm.
   * - Find / sort
     - Filter by filename; click a column header to sort, including by extension.
   * - ZIP archives
     - Browse and extract contents, or use **Save selected as ZIP…**.
   * - Share links
     - Copy attachment or experiment/resource links from the second toolbar row.
   * - Keyboard shortcuts
     - Press **F1** to show the shortcut guide in the right panel.

File shortcuts apply when the attachment list has focus. Multiple selection is
supported. Transfers run in the background. Keep the mouse pressed while an
uncached file downloads during a drag. Deletion removes server attachments
and keeps local copies. Replacing attachments also asks for confirmation.

Preview and open
----------------

* **Images and PDFs:** preview PNG, JPG, BMP, SVG and other supported images.
  **Left/Right** arrows browse images and PDFs; page buttons navigate a PDF.
* **Text and code:** preview YAML, JSON, XML, CSV, TSV, TXT, Markdown and Python.
  Notebooks show Markdown and code cells without executing them or displaying
  saved outputs. Use **Ctrl+F** (macOS **Command+F**) to search preview text.
* **ZIP files:** select an archive to browse its contents. **Extract to local
  folder…** unpacks it without replacing existing files. Above the 20 MiB
  automatic preview limit, use **Open** or double-click.

For figures, drag a rectangle with **Zoom area**, use the **+/− buttons**, or
scroll the mouse wheel. **Pan** or the middle mouse button moves the image;
**Fit** resets the view and **Escape** cancels a zoom rectangle.

Double-click opens most files in a local application. ZIP and SVG files open
inside the browser. GDS files open in KLayout if installed, and highlighted H5
files use optional h5viewer (see below). Upload a file again to save local edits
on eLabFTW. Large files have automatic preview limits.

Working folder and cache
------------------------

To keep downloaded files in a working folder:

.. code-block:: python

   experiment.browse_files(folder=r"D:\Lab\My experiment")

You can also choose **Working folder…** in the toolbar. Local edits are
preserved. **Refresh local copy from server…** asks before replacing a file.
Uploading local edits remains an explicit action.

For a default folder, add this to the root ``pyproject.toml``:

.. code-block:: toml

   [tool.elablink]
   config_path = "elablink.conf"
   browser_folder = "attachments"

Relative folders are resolved from that project file. Without a working folder,
the browser caches files locally. On Windows, the attachment cache is in
``%TEMP%\elablink-attachments``. On macOS it is in
``~/Library/Caches/elablink/attachments``, and on Linux it is in
``$XDG_CACHE_HOME/elablink/attachments`` (default ``~/.cache/elablink/attachments``).

The size below **Clear cache** in the upper-right corner is the total for all
experiments and resources in this browser cache. Click the button and confirm
to clear those local downloads. To clear only the selected record, use
**Cached downloads…** in the second toolbar row. Both actions preserve working
files, **Save as…** copies and attachments on eLabFTW.

H5 files downloaded from experiments also get a small ``run.h5.elablink.json``
file. Keep it beside the H5 file: the notebook manager uses it to save later
analyses back to the same experiment.

File uploads use a five-minute socket timeout to accommodate larger files.
After a timeout, refresh the list before retrying: the server may have received
the attachment. Uploads are not automatically retried by the browser.

The window opens on the computer running Python, including a local Jupyter
kernel. This browser is intended for ordinary attachments. Renaming Labmate
acquisition files does not update their associated local files.

Optional h5viewer
-----------------

Install the optional ``h5viewer`` in the same environment if you want its
specialized H5 view. Selecting an H5 attachment provides **Open in
h5viewer**. Use a version compatible with your installed Labmate for
reanalysis. See the `h5viewer project <https://github.com/kyrylo-gr/h5viewer>`_.
