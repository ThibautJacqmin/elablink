"""Build the Windows setup ZIP from one eLabLink release wheel."""

import argparse
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile


def build(wheel: Path, destination: Path) -> Path:
    if not wheel.is_file() or not wheel.match("elablink-*-py3-none-any.whl"):
        raise ValueError("Choose the released elablink-<version>-py3-none-any.whl file.")
    source = Path(__file__).resolve().parent
    destination.mkdir(parents=True, exist_ok=True)
    version = wheel.name.split("-")[1]
    target = destination / f"eLabLink-Attachment-Browser-Setup-{version}-Windows-x64.zip"
    with NamedTemporaryFile(dir=destination, suffix=".zip", delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        with ZipFile(staged, "w", ZIP_DEFLATED) as bundle:
            # Ship only installer code and the supplied wheel. A checkout can contain private configs.
            for name in ("Install-eLabLink-Attachment-Browser.cmd", "install_browser.py", "browser_env.py"):
                data = (source / name).read_bytes()
                if name.endswith(".cmd"):
                    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                bundle.writestr(name, data)
            bundle.write(wheel, wheel.name)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(build(args.wheel, args.destination))
