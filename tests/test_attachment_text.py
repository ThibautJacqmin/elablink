"""Offline checks of bounded attachment source-text previews."""

import codecs
import json
from pathlib import Path
import tempfile
import unittest

from elablink._attachment_text import _MAX_BYTES, _MAX_LINE_LENGTH, _MAX_LINES, _MAX_NOTEBOOK_BYTES, read_text_preview


class AttachmentTextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def source(self, name, content):
        path = self.directory / name
        path.write_bytes(content)
        return path

    def test_supported_formats_preserve_source_instead_of_parsing(self):
        sources = {
            "settings.yaml": "!!python/object:some.Class {}\n",
            "settings.yml": "laser: 780\n",
            "data.json": '{"laser":780,"name":"signal"}',
            "data.xml": '<!DOCTYPE data [<!ENTITY x SYSTEM "file:///private">]><data>&x;</data>',
            "measurements.csv": 'time,signal\r\n0,"1,2"\r\n',
            "measurements.tsv": "time\tsignal\n0\t1\n",
            "notes.TXT": "<b>plain source text</b>\n",
            "README.md": "# Calibration\n\nRead **carefully**.\n",
            "analysis.py": "raise RuntimeError('This source must not execute')\n",
        }
        for name, content in sources.items():
            with self.subTest(name=name):
                result = read_text_preview(self.source(name, content.encode("utf-8")))
                self.assertEqual(result["text"], content)
                self.assertFalse(result["truncated"])
                self.assertEqual(result["name"], name)

    def test_unicode_boms_and_windows_1252_are_decoded_and_identified(self):
        source = "Fréquence: 780\n"
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-32"):
            with self.subTest(encoding=encoding):
                result = read_text_preview(self.source("notes.txt", source.encode(encoding)))
                self.assertEqual(result["text"], source)
                self.assertEqual(result["encoding"], encoding)
        result = read_text_preview(self.source("notes.txt", source.encode("cp1252")))
        self.assertEqual(result["text"], source)
        self.assertEqual(result["encoding"], "windows-1252")
        for encoding, bom in (("utf-16-be", codecs.BOM_UTF16_BE), ("utf-32-be", codecs.BOM_UTF32_BE)):
            with self.subTest(encoding=encoding):
                result = read_text_preview(self.source("notes.txt", bom + source.encode(encoding)))
                self.assertEqual(result["text"], source)

    def test_byte_limit_omits_partial_utf8_character_without_changing_encoding(self):
        prefix = b"x\n" * ((_MAX_BYTES - 2) // 2) + b"x"
        self.assertEqual(len(prefix), _MAX_BYTES - 1)
        result = read_text_preview(self.source("notes.txt", prefix + "é".encode("utf-8") + b"tail"))
        self.assertTrue(result["truncated"])
        self.assertEqual(result["encoding"], "utf-8")
        self.assertNotIn("\ufffd", result["text"])
        self.assertNotIn("tail", result["text"])

    def test_line_count_and_long_line_limits_keep_the_beginning(self):
        many = "".join(f"line {index}\n" for index in range(_MAX_LINES + 1))
        result = read_text_preview(self.source("notes.txt", many.encode("utf-8")))
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["text"].splitlines()), _MAX_LINES)
        self.assertTrue(result["text"].startswith("line 0\n"))
        self.assertNotIn(f"line {_MAX_LINES}\n", result["text"])
        result = read_text_preview(self.source("data.json", b"x" * (_MAX_LINE_LENGTH + 1)))
        self.assertTrue(result["truncated"])
        self.assertEqual(result["text"], "x" * _MAX_LINE_LENGTH)

    def test_empty_file_and_binary_data_have_clear_behavior(self):
        result = read_text_preview(self.source("empty.txt", b""))
        self.assertEqual(result["text"], "")
        self.assertFalse(result["truncated"])
        with self.assertRaisesRegex(ValueError, "binary data"):
            read_text_preview(self.source("binary.txt", b"binary\x00data"))
        with self.assertRaisesRegex(ValueError, "file type"):
            read_text_preview(self.source("data.bin", b"source"))

    def notebook(self, cells):
        content = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells}
        return self.source("analysis.ipynb", json.dumps(content).encode("utf-8"))

    def test_notebook_displays_ordered_sources_without_outputs_or_execution(self):
        path = self.notebook([
            {"cell_type": "markdown", "source": ["# Measurement\n", "<b>plain source</b>"],
             "attachments": {"secret.png": {"image/png": "OMIT_ATTACHMENT"}}},
            {"cell_type": "code", "source": "raise RuntimeError('do not execute')",
             "outputs": [{"output_type": "stream", "name": "stdout", "text": "OMIT_OUTPUT"}]},
            {"cell_type": "raw", "source": "raw notes"},
        ])
        result = read_text_preview(path)
        self.assertIn("# Cell 1 (Markdown)\n# Measurement\n<b>plain source</b>", result["text"])
        self.assertIn("# Cell 2 (Code)\nraise RuntimeError('do not execute')", result["text"])
        self.assertIn("# Cell 3 (Raw)\nraw notes", result["text"])
        self.assertLess(result["text"].index("# Cell 1"), result["text"].index("# Cell 2"))
        self.assertNotIn("OMIT_ATTACHMENT", result["text"])
        self.assertNotIn("OMIT_OUTPUT", result["text"])
        self.assertFalse(result["truncated"])

    def test_notebook_rejects_invalid_json_shapes_and_source_types(self):
        invalid = [b"{not valid JSON}", b"[]", b'{"nbformat":3,"cells":[]}',
                   b'{"nbformat":4,"cells":{}}',
                   b'{"nbformat":4,"cells":[{"cell_type":"code","source":42}]}',
                   b'{"nbformat":4,"cells":[{"cell_type":"code","source":[42]}]}',
                   b'{"nbformat":4,"cells":[{"cell_type":"other","source":""}]}',
                   b'{"nbformat":4,"cells":[42]}']
        for content in invalid:
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "[Nn]otebook"):
                read_text_preview(self.source("analysis.ipynb", content))
        result = read_text_preview(self.notebook([]))
        self.assertEqual(result["text"], "")

    def test_notebook_input_and_output_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "maximum 8 MiB"):
            read_text_preview(self.source("analysis.ipynb", b" " * (_MAX_NOTEBOOK_BYTES + 1)))
        path = self.notebook([{"cell_type": "code", "source": "x" * (_MAX_LINE_LENGTH + 1)}])
        result = read_text_preview(path)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["text"].splitlines()[-1]), _MAX_LINE_LENGTH)


if __name__ == "__main__":
    unittest.main()
