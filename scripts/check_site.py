#!/usr/bin/env python3
"""Validate OINK search metadata, content hygiene, and index size."""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path


REQUIRED_SEARCH_FIELDS = {
    "root",
    "section",
    "type",
    "keywords",
    "boost",
    "breadcrumb",
    "icon",
}
RAW_INDEX_LIMIT = 512 * 1024
GZIP_INDEX_LIMIT = 128 * 1024
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMPTY_IMAGE_ALT = re.compile(r"^\s*!\[\]\(", re.MULTILINE)


def check_content(content_root: Path) -> list[str]:
    failures: list[str] = []
    for path in sorted(content_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if CONTROL_CHARACTERS.search(text):
            failures.append(f"control character: {path}")
        if EMPTY_IMAGE_ALT.search(text):
            failures.append(f"empty image alt text: {path}")
    return failures


def check_search_index(public_root: Path) -> tuple[list[str], str]:
    failures: list[str] = []
    indexes = sorted(public_root.glob("offline-search-index*.json"))
    if len(indexes) != 1:
        return [f"expected one search index, found {len(indexes)}"], ""

    path = indexes[0]
    raw = path.read_bytes()
    records = json.loads(raw)
    if not isinstance(records, list) or not records:
        failures.append(f"search index is empty or invalid: {path}")
        records = []

    for record in records:
        missing = sorted(REQUIRED_SEARCH_FIELDS.difference(record))
        if missing:
            failures.append(
                f"search metadata missing {', '.join(missing)}: "
                f"{record.get('ref', '<unknown>')}"
            )

    raw_size = len(raw)
    gzip_size = len(gzip.compress(raw, mtime=0))
    if raw_size > RAW_INDEX_LIMIT:
        failures.append(
            f"search index exceeds raw limit: {raw_size} > {RAW_INDEX_LIMIT} bytes"
        )
    if gzip_size > GZIP_INDEX_LIMIT:
        failures.append(
            f"search index exceeds gzip limit: {gzip_size} > {GZIP_INDEX_LIMIT} bytes"
        )

    summary = (
        f"Search index check: {len(records)} records; {raw_size} bytes raw; "
        f"{gzip_size} bytes gzip; OINK metadata present."
    )
    return failures, summary


def main() -> int:
    public_root = Path(sys.argv[1] if len(sys.argv) > 1 else "public")
    content_failures = check_content(Path("content"))
    failures = list(content_failures)
    index_failures, summary = check_search_index(public_root)
    failures.extend(index_failures)

    if summary:
        print(summary)
    if not content_failures:
        print("Content check: no control characters or empty image alt text.")
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
