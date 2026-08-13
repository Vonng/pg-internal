#!/usr/bin/env python3
"""Validate internal links, assets, fragments, and duplicate IDs in rendered HTML."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


@dataclass(frozen=True)
class Reference:
    source: Path
    attribute: str
    value: str


class DocumentParser(HTMLParser):
    def __init__(self, source: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.ids: list[str] = []
        self.references: list[Reference] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if tag == "a" and values.get("name"):
            self.ids.append(values["name"] or "")

        if "data-proofer-ignore" in values:
            return

        attribute = {
            "a": "href",
            "img": "src",
            "script": "src",
            "link": "href",
            "source": "src",
            "video": "src",
            "audio": "src",
        }.get(tag)
        if attribute and values.get(attribute):
            self.references.append(
                Reference(self.source, attribute, values[attribute] or "")
            )


def parse_document(path: Path) -> DocumentParser:
    parser = DocumentParser(path)
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def page_url(root: Path, source: Path) -> str:
    relative = source.relative_to(root).as_posix()
    if relative == "index.html":
        return "/"
    if relative.endswith("/index.html"):
        return f"/{relative[:-10]}"
    return f"/{relative}"


def target_file(root: Path, url_path: str) -> Path | None:
    decoded = unquote(url_path)
    candidate = (root / decoded.lstrip("/")).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None

    if candidate.is_file():
        return candidate
    index = candidate / "index.html"
    if index.is_file():
        return index
    if not candidate.suffix:
        html = candidate.with_suffix(".html")
        if html.is_file():
            return html
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("public_root", nargs="?", default="public")
    parser.add_argument(
        "--origin",
        default="https://pgint.vonng.com",
        help="absolute URLs on this origin are checked as internal",
    )
    args = parser.parse_args()

    root = Path(args.public_root).resolve()
    if not root.is_dir():
        print(f"rendered site does not exist: {root}", file=sys.stderr)
        return 2

    html_files = sorted(root.rglob("*.html"))
    documents = {path: parse_document(path) for path in html_files}
    known_ids = {path: set(doc.ids) for path, doc in documents.items()}
    failures: list[str] = []
    checked = 0
    fragments = 0

    origin = urlsplit(args.origin)
    base_path = origin.path.rstrip("/")

    for path, document in documents.items():
        duplicates = sorted(
            value for value, count in Counter(document.ids).items() if count > 1
        )
        for value in duplicates:
            failures.append(
                f"duplicate id: #{value} (in {path.relative_to(root)})"
            )

        source_url = urljoin(
            args.origin.rstrip("/") + "/", page_url(root, path).lstrip("/")
        )
        for reference in document.references:
            raw = reference.value.strip()
            if (
                not raw
                or raw == "#"
                or "link-check=no" in raw
                or raw.startswith(("mailto:", "tel:", "javascript:", "data:"))
            ):
                continue

            target = urlsplit(urljoin(source_url, raw))
            if target.scheme not in ("http", "https"):
                continue
            if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                continue

            checked += 1
            lookup_path = target.path
            if base_path and (
                lookup_path == base_path or lookup_path.startswith(base_path + "/")
            ):
                lookup_path = lookup_path[len(base_path) :] or "/"
            resolved = target_file(root, lookup_path)
            source_name = path.relative_to(root).as_posix()
            display = f"{target.path}{('#' + target.fragment) if target.fragment else ''}"
            if resolved is None:
                failures.append(
                    f"missing target: {display} (from {source_name})"
                )
                continue

            if target.fragment and resolved.suffix == ".html":
                fragments += 1
                fragment = unquote(target.fragment)
                if fragment not in known_ids.get(resolved, set()):
                    failures.append(
                        f"missing fragment: {display} (from {source_name})"
                    )

    print(
        f"Rendered link check: {len(html_files)} pages; "
        f"{checked} internal references; {fragments} fragments."
    )
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
