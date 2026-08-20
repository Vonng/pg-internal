#!/usr/bin/env python3
"""Regression tests for semantic HTML-to-Markdown cleanup."""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
LUA_FILTER = SCRIPT_DIR / "cleanup.lua"
IMPORTER_SPEC = importlib.util.spec_from_file_location(
    "interdb_import",
    SCRIPT_DIR / "import.py",
)
assert IMPORTER_SPEC and IMPORTER_SPEC.loader
IMPORTER = importlib.util.module_from_spec(IMPORTER_SPEC)
IMPORTER_SPEC.loader.exec_module(IMPORTER)


def convert_fixture(source: str) -> str:
    completed = subprocess.run(
        [
            "pandoc",
            "--from=html",
            "--to=gfm+attributes",
            "--wrap=none",
            f"--lua-filter={LUA_FILTER}",
        ],
        input=source,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


class CleanupFilterTests(unittest.TestCase):
    def test_math_text_identifiers_are_katex_safe(self) -> None:
        source = (
            r"<p>$\text{cpu_tuple_cost}$</p>"
            r'<span class="math align-center">'
            r"$$\text{max_IO_cost} < \text{vacuum_freeze_table_age}$$"
            r"</span>"
        )
        output = IMPORTER.wrap_inline_math(source)

        self.assertIn(r"\text{cpu\_tuple\_cost}", output)
        self.assertIn(r"\text{max\_IO\_cost}", output)
        self.assertIn(r"\text{vacuum\_freeze\_table\_age}", output)

    def test_expand_notice_footnote_math_and_code_are_preserved(self) -> None:
        source = """
<div class="expand">
  <input type="checkbox">
  <label class="expand-label"><i class="fas fa-chevron-down"></i>Details</label>
  <div class="expand-content">
    <p>First body paragraph.</p>
    <p>Second body paragraph.</p>
  </div>
</div>
<div class="box notices cstyle info">
  <div class="box-label">Info</div>
  <div class="box-content"><p>Notice body.</p></div>
</div>
<p>A statement with a note<sup id="fnref:1"><a href="#fn:1"
class="footnote-ref" role="doc-noteref">1</a></sup>.</p>
<div class="footnotes" role="doc-endnotes">
  <hr>
  <ol><li id="fn:1"><p>Footnote body.<a href="#fnref:1"
  class="footnote-backref" role="doc-backlink">back</a></p></li></ol>
</div>
<p><span class="math inline-math">$x_{i}$</span></p>
<div class="wrap-code highlight">
  <pre class="language-sql"><code>SELECT 1;</code></pre>
</div>
"""
        output = convert_fixture(source)

        self.assertIn("**Details**", output)
        self.assertIn("First body paragraph.", output)
        self.assertIn("Second body paragraph.", output)
        self.assertIn("> **Info:**", output)
        self.assertIn("> Notice body.", output)
        self.assertIn("note[^1]", output)
        self.assertIn("[^1]: Footnote body.", output)
        self.assertIn(r"\(x_{i}\)", output)
        self.assertRegex(output, r"``` ?sql\nSELECT 1;\n```")


if __name__ == "__main__":
    unittest.main()
