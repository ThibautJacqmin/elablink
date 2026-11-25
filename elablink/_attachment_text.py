"""Read bounded source-text previews without executing attachment contents.

@author Thibaut Jacqmin
"""

import codecs
import json
from pathlib import Path


TEXT_PREVIEW_SUFFIXES = {".yaml", ".yml", ".json", ".xml", ".csv", ".tsv", ".txt", ".md", ".py", ".ipynb"}
_MAX_BYTES = 256 * 1024
_MAX_NOTEBOOK_BYTES = 8 * 1024 * 1024
_MAX_LINES = 2000
_MAX_LINE_LENGTH = 4000


def read_text_preview(path):
    """Read the beginning of a text file, preserving its source formatting.

    Wide Unicode encodings require a BOM. Without a BOM, try UTF-8 before
    Windows-1252. Return the chosen encoding and whether any text was omitted.
    """
    path = Path(path)
    if path.suffix.lower() not in TEXT_PREVIEW_SUFFIXES:
        raise ValueError("No text preview is available for this file type.")
    if path.suffix.lower() == ".ipynb":
        text, encoding = _read_notebook(path)
        text, truncated = _limit_text(text)
        return {"text": text, "name": path.name, "truncated": truncated, "encoding": encoding}
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    truncated = len(raw) > _MAX_BYTES
    raw = raw[:_MAX_BYTES]
    wide_unicode = raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE,
                                   codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
    if b"\x00" in raw and not wide_unicode:
        raise ValueError("This file contains binary data and cannot be previewed as text.")

    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif raw.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict" if encoding == "utf-8" else "replace")
    try:
        # A byte-limited preview may finish in the middle of a Unicode character.
        text = decoder.decode(raw, final=not truncated)
    except UnicodeDecodeError:
        encoding = "windows-1252"
        text = raw.decode("cp1252", errors="replace")

    text, truncated = _limit_text(text, truncated=truncated)
    return {"text": text, "name": path.name, "truncated": truncated, "encoding": encoding}


def _read_notebook(path):
    with path.open("rb") as stream:
        raw = stream.read(_MAX_NOTEBOOK_BYTES + 1)
    if len(raw) > _MAX_NOTEBOOK_BYTES:
        raise ValueError("This notebook is too large to preview (maximum 8 MiB).")
    try:
        notebook = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Cannot preview notebook: invalid JSON.") from error
    if not isinstance(notebook, dict) or notebook.get("nbformat") != 4:
        raise ValueError("Notebook previews require notebook format 4.")
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError("The notebook must contain a cells list.")
    sections = []
    for index, cell in enumerate(cells, 1):
        if not isinstance(cell, dict) or cell.get("cell_type") not in ("markdown", "code", "raw"):
            raise ValueError(f"Notebook cell {index} has an unsupported type.")
        source = cell.get("source")
        if isinstance(source, list) and all(isinstance(line, str) for line in source):
            source = "".join(source)
        if not isinstance(source, str):
            raise ValueError(f"Notebook cell {index} has an invalid source.")
        sections.append(f"# Cell {index} ({cell['cell_type'].capitalize()})\n{source}")
    return "\n\n".join(sections), "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8"


def _limit_text(text, *, truncated=False):
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_BYTES:
        text = codecs.getincrementaldecoder("utf-8")().decode(encoded[:_MAX_BYTES], final=False)
        truncated = True
    lines = text.splitlines(keepends=True)
    truncated = truncated or len(lines) > _MAX_LINES
    preview = []
    for line in lines[:_MAX_LINES]:
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        if len(body) > _MAX_LINE_LENGTH:
            truncated = True
            line = body[:_MAX_LINE_LENGTH] + ending
        preview.append(line)
    return "".join(preview), truncated
