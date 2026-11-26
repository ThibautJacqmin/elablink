"""Interactive Windows setup for the eLabLink attachment browser (standard library only)."""

import argparse
import getpass
import itertools
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit, urlunsplit
import warnings

# The .cmd uses -I, which omits even the script's own directory from sys.path.
BUNDLE = Path(__file__).resolve().parent
sys.path.insert(0, str(BUNDLE))
import browser_env


SHORTCUT_NAME = "eLabLink Attachment Browser"
PREVIOUS_SHORTCUT_NAME = "eLabLink Attachments"
SHORTCUT_MARKER = f"{SHORTCUT_NAME} (per-user installer)"
PREVIOUS_SHORTCUT_MARKER = f"{PREVIOUS_SHORTCUT_NAME} (per-user installer)"


def normalize_api_url(value):
    """Accept a server or its API URL, including an optional deployment subpath."""
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Enter a single HTTPS server address.")
    value = value.strip()
    if not value or any(c.isspace() or c in "\\?#" for c in value):
        raise ValueError("Enter an HTTPS server address without spaces, a query, or a fragment.")
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
        if parts.scheme != "https" or not host or parts.username is not None or parts.password is not None:
            raise ValueError
        if ":" in host:
            host = "[" + host + "]"
        else:
            host = host.encode("idna").decode("ascii")
        if not re.fullmatch(r"[A-Za-z0-9._:\[\]-]+", host) or parts.netloc.endswith(":"):
            raise ValueError
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Enter an HTTPS server address with a valid host and no embedded credentials.") from exc
    path = quote(parts.path.rstrip("/"), safe="/%:@!$&'()*+,;=-._~")
    if not path.endswith("/api/v2"):
        path += "/api/v2"
    return urlunsplit(("https", host if port is None else f"{host}:{port}", path, "", ""))


def _powershell(script, **values):
    """Only fixed PowerShell code is executable; paths travel as environment data."""
    exe = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); " + script
    env = os.environ.copy()
    env["PSModulePath"] = str(exe.parent / "Modules")
    env.update({k: str(v) for k, v in values.items()})
    result = subprocess.run([str(exe), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                            env=env, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise OSError("Windows could not access the requested file or shortcut. Check your folder permissions.")
    return result.stdout.strip()


def create_config(path, server, api_key):
    """Publish a new config only after its permissions are private and the write finishes."""
    url = normalize_api_url(server)
    if not api_key or not api_key.isascii() or not all(32 < ord(c) < 127 for c in api_key):
        raise ValueError("Enter a nonempty API key on one line, without spaces.")
    path = Path(path)
    if os.path.lexists(path):
        raise FileExistsError(f"The configuration already exists: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".elablink-settings-", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            if os.name == "nt":
                _powershell(
                    "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;"
                    "$acl=[Security.AccessControl.FileSecurity]::new();"
                    "$acl.SetAccessRuleProtection($true,$false);"
                    "$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new("
                    "$sid,'FullControl','Allow')); [IO.File]::SetAccessControl($env:EL_INSTALL_TEMP,$acl)",
                    EL_INSTALL_TEMP=tmp)
            stream.write(f"API_HOST_URL={url}\nAPI_KEY={api_key}\nUNIQUE_EXPERIMENTS_TITLES=true\nVERIFY_SSL=true\n")
        if os.name == "nt":
            tmp.rename(path)  # On Windows rename fails if another process created the name.
        else:
            os.link(tmp, path)
        return path
    finally:
        tmp.unlink(missing_ok=True)


def _existing_config(value):
    path = Path(os.path.expandvars(str(value))).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    return path


def select_config(root, previous, *, supplied=None, interactive=True):
    root = Path(root)
    if supplied is not None:
        return _existing_config(supplied)
    default = root / "elablink.conf"
    candidate = next((p for p in (previous, default) if p is not None and Path(p).is_file()), None)
    if not interactive:
        if candidate is None:
            raise ValueError("Supply --config-path when no existing configuration is available.")
        return _existing_config(candidate)
    if candidate is not None:
        print(f"\nExisting configuration: {candidate}")
        while True:
            answer = input("Use this configuration? [Y/n/q] ").strip().casefold()
            if answer in ("", "y", "yes"):
                return _existing_config(candidate)
            if answer in ("n", "no"):
                break
            if answer == "q":
                raise KeyboardInterrupt
    while True:
        answer = input("Existing .conf file path, Enter to create one, or Q to cancel: ").strip()
        if not answer:
            break
        if answer.casefold() == "q":
            raise KeyboardInterrupt
        try:
            return _existing_config(answer.strip('"'))
        except (OSError, ValueError) as exc:
            print(exc)
    while True:
        try:
            url = normalize_api_url(input("eLabFTW server address (HTTPS): "))
            break
        except ValueError as exc:
            print(exc)
    if not sys.stdin.isatty():
        raise ValueError("Enter your API key from a terminal, or use --config-path with an existing configuration.")
    while True:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("Personal API key (hidden): ")
        path = default if not os.path.lexists(default) else next(
            p for i in itertools.count(2) if not os.path.lexists(p := root / f"elablink-{i}.conf"))
        try:
            created = create_config(path, url, key)
        except ValueError as exc:
            print(exc)
        else:
            print(f"Saved your configuration to {created}")
            return created


def _desktop():
    return Path(_powershell("(New-Object -ComObject WScript.Shell).SpecialFolders.Item('Desktop')"))


def read_shortcut(path):
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        return json.loads(_powershell(
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:EL_INSTALL_LINK);"
            "[pscustomobject]@{TargetPath=$s.TargetPath;Arguments=$s.Arguments;"
            "WorkingDirectory=$s.WorkingDirectory;Description=$s.Description}|ConvertTo-Json -Compress",
            EL_INSTALL_LINK=path))
    except (OSError, ValueError):
        # A shortcut we cannot identify belongs to the user.
        return {}


def _config_from_link(info):
    arguments = info.get("Arguments", "")
    # Windows paths cannot contain quotes. A trailing quote pair delimits a path with spaces.
    found = re.search(r'(?:^|\s)--config-path(?:=|\s+)(?:"([^"]+)"|(\S+))\s*$', arguments, re.I)
    if not found:
        return None
    path = Path(os.path.expandvars(found.group(1) or found.group(2)))
    return path if path.is_absolute() else Path(info.get("WorkingDirectory", "")) / path


def _our_shortcut(info):
    if not info:
        return False
    if info.get("Description") in (SHORTCUT_MARKER, PREVIOUS_SHORTCUT_MARKER):
        return True
    # Adopt the stock alpha launcher, retaining its external config. Leave custom
    # per-record/working-folder launchers and other applications at their original names.
    if _config_from_link(info) is None:
        return False
    args = info.get("Arguments", "")
    before_config = re.split(r"(?:^|\s)--config-path(?:=|\s+)", args, flags=re.I)[0].strip()
    target = PureWindowsPath(info.get("TargetPath", "")).name.lower()
    if target == "elablink-attachments.exe":
        return not before_config
    if target != "pythonw.exe":
        return False
    return re.fullmatch(
        r'(?:-I\s+)?"?[^"]*?\\cwp\.py"?\s+--no-console\s+(?:"[^"]+"|\S+)\s+'
        r'(?:"[^"]+\\elablink-attachments\.exe"|[^\s"]+\\elablink-attachments\.exe)',
        before_config, re.I) is not None


def _managed_shortcuts(desktop, name):
    # Fallback names persist when a user removes an unrelated shortcut that occupied
    # a lower-numbered slot. Look for all existing installer names, including gaps.
    pattern = re.compile(re.escape(name) + r"(?: \(Installed(?: [0-9]+)?\))?\.lnk", re.I)
    matches = (path for path in desktop.glob(name + "*.lnk") if pattern.fullmatch(path.name))
    for path in sorted(matches, key=lambda p: (p.name.casefold() != (name + ".lnk").casefold(), p.name.casefold())):
        info = read_shortcut(path)
        if _our_shortcut(info):
            yield path, _config_from_link(info)


def _shortcut_slot(desktop):
    current = list(_managed_shortcuts(desktop, SHORTCUT_NAME))
    if current:
        target = current[0][0]
    else:
        for i in itertools.count():
            suffix = "" if i == 0 else (" (Installed)" if i == 1 else f" (Installed {i})")
            target = desktop / (SHORTCUT_NAME + suffix + ".lnk")
            if not os.path.lexists(target):
                break
    previous = _managed_shortcuts(desktop, PREVIOUS_SHORTCUT_NAME)
    config = next((cfg for _, cfg in itertools.chain(current, previous) if cfg and cfg.is_file()), None)
    return target, config


def write_shortcut(path, launcher, env_dir, arguments):
    _powershell(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:EL_INSTALL_LINK);"
        "$s.TargetPath=$env:EL_INSTALL_TARGET;$s.Arguments=$env:EL_INSTALL_ARGS;"
        "$s.WorkingDirectory=$env:EL_INSTALL_CWD;$s.Description=$env:EL_INSTALL_DESCRIPTION;$s.Save()",
        EL_INSTALL_LINK=path, EL_INSTALL_TARGET=launcher, EL_INSTALL_ARGS=arguments,
        EL_INSTALL_CWD=env_dir, EL_INSTALL_DESCRIPTION=SHORTCUT_MARKER)


def publish_shortcut(path, launcher, env_dir, config):
    args = subprocess.list2cmdline([str(a) for a in browser_env.launcher_args(env_dir, config)])
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".elablink-setup-", dir=path.parent) as staging:
        new_link = Path(staging) / path.name
        write_shortcut(new_link, launcher, env_dir, args)
        if not new_link.is_file() or not new_link.stat().st_size:
            raise OSError("Windows did not create the new shortcut.")
        existing = read_shortcut(path)
        if existing is None:
            if os.name == "nt":
                new_link.rename(path)
            else:
                os.link(new_link, path)
        elif _our_shortcut(existing):
            new_link.replace(path)
        else:
            raise FileExistsError(f"Another application is using the shortcut name: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path)
    parser.add_argument("--desktop-dir", type=Path)
    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--non-interactive", action="store_true")
    options = parser.parse_args(argv)
    try:
        root = (options.install_dir or Path(os.environ["LOCALAPPDATA"]) / "eLabLink" / "AttachmentBrowser").resolve()
        desktop = (options.desktop_dir or _desktop()).resolve()
        wheels = [options.wheel] if options.wheel else sorted(BUNDLE.glob("elablink-*.whl"))
        if len(wheels) != 1 or not wheels[0].is_file():
            raise ValueError("Extract the full setup ZIP. It must contain one eLabLink wheel beside the installer.")
        link, previous = _shortcut_slot(desktop)
        print("eLabLink Attachment Browser setup")
        print("This installs the eLabLink core library and the desktop attachment browser dependencies "
              "in a private Python environment.")
        config = select_config(root, previous, supplied=options.config_path, interactive=not options.non_interactive)
        launcher, env_dir, _ = browser_env.prepare_environment(root, wheels[0].resolve())
        publish_shortcut(link, launcher, env_dir, config)
        print(f"\nReady. Open this desktop shortcut:\n{link}")
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.")
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, getpass.GetPassWarning) as exc:
        print(f"\nSetup could not finish: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
