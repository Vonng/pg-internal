#!/usr/bin/env python3
"""Reproducibly mirror interdb.jp/pg into Hugo-friendly Markdown.

Only public book pages are fetched. The importer is deliberately low-concurrency
(one request at a time), caches source HTML, records hashes, and never pushes or
deploys anything.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree

import yaml

try:
    from PIL import Image
except ImportError:  # pragma: no cover - validation reports the missing library.
    Image = None


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_URL = "https://www.interdb.jp/pg/"
SITEMAP_URL = urljoin(BASE_URL, "sitemap.xml")
SEARCH_INDEX_URL = urljoin(BASE_URL, "index.search.js")
USER_AGENT = "Mozilla/5.0 (compatible; pg-internal-local-mirror/1.0)"

CACHE_DIR = ROOT / ".cache" / "interdb-pg"
PAGE_CACHE_DIR = CACHE_DIR / "pages"
HEADER_CACHE_DIR = CACHE_DIR / "headers"
ASSET_CACHE_DIR = CACHE_DIR / "assets"
MANIFEST_PATH = ROOT / "sources" / "interdb-pg" / "manifest.yaml"
LUA_FILTER = SCRIPT_DIR / "cleanup.lua"

CONTENT_EN = ROOT / "en"
STATIC_EN = ROOT / "static" / "images" / "en"

IMAGE_EXTENSIONS = {
    ".avif",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_source_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc == "www.interdb.jp" and parsed.path.startswith("/pg/"):
        return parsed._replace(scheme="https", fragment="").geturl()
    return url


def page_identity(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    path = parsed.path

    if path in {"/pg", "/pg/"}:
        return {
            "id": "home",
            "chapter": None,
            "section": None,
            "subsection": None,
            "target": "en/_index.md",
            "permalink": "/",
            "weight": None,
        }

    match = re.fullmatch(r"/pg/pgsql(\d{2})/index\.html", path)
    if match:
        chapter = int(match.group(1))
        return {
            "id": f"ch{chapter:02d}-index",
            "chapter": chapter,
            "section": 0,
            "subsection": 0,
            "target": f"en/docs/ch{chapter:02d}/_index.md",
            "permalink": f"/docs/ch{chapter:02d}/",
            "weight": chapter * 10,
        }

    match = re.fullmatch(r"/pg/pgsql(\d{2})/(\d{2})\.html", path)
    if match:
        chapter, section = map(int, match.groups())
        return {
            "id": f"ch{chapter:02d}-{section:02d}",
            "chapter": chapter,
            "section": section,
            "subsection": 0,
            "target": f"en/docs/ch{chapter:02d}/{section:02d}.md",
            "permalink": f"/docs/ch{chapter:02d}/{section:02d}/",
            "weight": section * 10,
        }

    match = re.fullmatch(r"/pg/pgsql(\d{2})/(\d{2})/index\.html", path)
    if match:
        chapter, section = map(int, match.groups())
        return {
            "id": f"ch{chapter:02d}-{section:02d}",
            "chapter": chapter,
            "section": section,
            "subsection": 0,
            "target": f"en/docs/ch{chapter:02d}/{section:02d}.md",
            "permalink": f"/docs/ch{chapter:02d}/{section:02d}/",
            "weight": section * 10,
        }

    match = re.fullmatch(r"/pg/pgsql(\d{2})/(\d{2})/(\d{2})\.html", path)
    if match:
        chapter, section, subsection = map(int, match.groups())
        return {
            "id": f"ch{chapter:02d}-{section:02d}-{subsection:02d}",
            "chapter": chapter,
            "section": section,
            "subsection": subsection,
            "target": (
                f"en/docs/ch{chapter:02d}/"
                f"{section:02d}-{subsection:02d}.md"
            ),
            "permalink": (
                f"/docs/ch{chapter:02d}/{section:02d}-{subsection:02d}/"
            ),
            "weight": section * 10 + subsection,
        }

    raise ValueError(f"Unsupported source page URL: {url}")


def page_sort_key(page: dict[str, Any]) -> tuple[int, int, int]:
    if page["id"] == "home":
        return (-1, -1, -1)
    return (
        int(page["chapter"]),
        int(page["section"]),
        int(page["subsection"]),
    )


def cache_name_for_page(page: dict[str, Any]) -> str:
    return f"{page['id']}.html"


def parse_last_headers(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="iso-8859-1", errors="replace")
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    for block in reversed(blocks):
        if not block.startswith("HTTP/"):
            continue
        values: dict[str, str] = {}
        for line in block.splitlines()[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip().lower()] = value.strip()
        return values
    return {}


def curl_fetch(
    url: str,
    destination: Path,
    headers_path: Path,
    *,
    refresh: bool,
    prefer_http: bool = False,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers_path.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and destination.stat().st_size > 0 and not refresh:
        headers = parse_last_headers(headers_path)
        return {
            "cached": True,
            "http_code": 200,
            "url_effective": url,
            "content_type": headers.get("content-type", ""),
            "etag": headers.get("etag"),
            "last_modified": headers.get("last-modified"),
        }

    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)
    candidates = [url]
    parsed_url = urlparse(url)
    if parsed_url.scheme == "https" and parsed_url.netloc == "www.interdb.jp":
        http_url = parsed_url._replace(scheme="http").geturl()
        candidates = [http_url, url] if prefer_http else [url, http_url]

    completed = None
    failures = []
    fetched_url = url
    for candidate in candidates:
        command = [
            "curl",
            "--http1.1",
            "--connect-timeout",
            "10",
            "--max-time",
            "60",
            "--retry",
            "1",
            "--retry-all-errors",
            "--retry-delay",
            "2",
            "--retry-max-time",
            "90",
            "--user-agent",
            USER_AGENT,
            "--silent",
            "--show-error",
            "--location",
            "--fail-with-body",
            "--dump-header",
            str(headers_path),
            "--output",
            str(temporary),
            "--write-out",
            "%{json}",
            candidate,
        ]
        try:
            attempt = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
                timeout=100,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{candidate}: process timeout")
            temporary.unlink(missing_ok=True)
            continue
        if attempt.returncode == 0:
            completed = attempt
            fetched_url = candidate
            break
        failures.append(
            f"{candidate}: {attempt.stderr.strip() or attempt.stdout.strip()}"
        )
        temporary.unlink(missing_ok=True)

    if completed is None:
        raise RuntimeError(f"curl failed for {url}: {'; '.join(failures)}")

    try:
        metrics = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Cannot decode curl metrics for {url}: {exc}") from exc

    if int(metrics.get("http_code") or 0) != 200:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Unexpected HTTP status for {url}: {metrics.get('http_code')}")

    temporary.replace(destination)
    headers = parse_last_headers(headers_path)
    metrics.update(
        {
            "cached": False,
            "requested_url": url,
            "fetched_url": fetched_url,
            "etag": headers.get("etag"),
            "last_modified": headers.get("last-modified"),
        }
    )
    return metrics


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {}
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    return value or {}


def save_manifest(manifest: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            manifest,
            handle,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )


def fetch_sitemap(*, refresh: bool) -> Path:
    destination = CACHE_DIR / "sitemap.xml"
    headers = HEADER_CACHE_DIR / "sitemap.headers"
    curl_fetch(SITEMAP_URL, destination, headers, refresh=refresh)
    return destination


def discover_pages(*, refresh_sitemap: bool) -> dict[str, Any]:
    sitemap_path = fetch_sitemap(refresh=refresh_sitemap)
    root = ElementTree.parse(sitemap_path).getroot()
    discovered = []
    for element in root.iter():
        if not element.tag.endswith("loc") or not element.text:
            continue
        url = normalize_source_url(element.text.strip())
        if url.endswith("/tags/index.html"):
            continue
        discovered.append(url)

    discovered = [BASE_URL, *dict.fromkeys(discovered)]
    pages = []
    for source_url in discovered:
        identity = page_identity(source_url)
        pages.append(
            {
                **identity,
                "source_url": source_url,
                "title": None,
                "status": "pending",
                "warnings": [],
                "assets": [],
            }
        )
    pages.sort(key=page_sort_key)

    previous = load_manifest()
    previous_pages = {
        item["source_url"]: item for item in previous.get("pages", []) if item.get("source_url")
    }
    for page in pages:
        old = previous_pages.get(page["source_url"], {})
        for key in (
            "title",
            "status",
            "fetched_at",
            "source_sha256",
            "source_bytes",
            "content_type",
            "fetched_url",
            "etag",
            "last_modified",
            "cache",
            "warnings",
            "assets",
            "target_sha256",
            "converted_at",
        ):
            if key in old:
                page[key] = old[key]

    manifest = {
        "version": 1,
        "source": {
            "base_url": BASE_URL,
            "sitemap_url": SITEMAP_URL,
            "sitemap_sha256": sha256_path(sitemap_path),
            "inventory_checked_at": now_iso(),
            "excluded": [
                {
                    "url": urljoin(BASE_URL, "tags/index.html"),
                    "reason": "Hugo taxonomy aggregation, not book content",
                }
            ],
        },
        "expected_source_pages": 83,
        "expected_assets": 209,
        "pages": pages,
        "assets": previous.get("assets", []),
    }
    if len(pages) != manifest["expected_source_pages"]:
        manifest["source"]["inventory_warning"] = (
            f"Expected 83 source pages from the planning baseline, discovered {len(pages)}"
        )
    save_manifest(manifest)
    return manifest


def extract_article(document: str) -> str:
    match = re.search(
        r"<article\b[^>]*class=[\"'][^\"']*\b(?:default|home)\b[^\"']*[\"'][^>]*>"
        r"(.*?)</article>",
        document,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        match = re.search(
            r"<article\b[^>]*>(.*?)</article>",
            document,
            re.IGNORECASE | re.DOTALL,
        )
    if not match:
        raise ValueError("Cannot locate article element")
    return match.group(1)


def extract_title(article: str) -> tuple[str, str | None]:
    match = re.search(
        r"<h1\b([^>]*)>(.*?)</h1>",
        article,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ValueError("Cannot locate page h1")
    attributes, body = match.groups()
    title = strip_html(body)
    id_match = re.search(r"\bid=[\"']([^\"']+)[\"']", attributes, re.IGNORECASE)
    return title, html.unescape(id_match.group(1)) if id_match else None


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str) -> str:
    parser = TextExtractor()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())


def description_from_article(article: str) -> str:
    h1_end = re.search(r"</h1>", article, re.IGNORECASE)
    remaining = article[h1_end.end() :] if h1_end else article
    paragraph = re.search(r"<p\b[^>]*>(.*?)</p>", remaining, re.IGNORECASE | re.DOTALL)
    if not paragraph:
        return ""
    value = strip_html(paragraph.group(1))
    if len(value) <= 220:
        return value
    candidate = value[:220]
    sentence = max(candidate.rfind(". "), candidate.rfind("! "), candidate.rfind("? "))
    if sentence >= 100:
        return candidate[: sentence + 1]
    return candidate.rstrip() + "…"


class AssetParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.anchors: list[str | None] = []
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a":
            self.anchors.append(values.get("href"))
            return
        if tag == "img":
            source = values.get("src") or values.get("data-src")
            if source:
                source_url = urljoin(self.page_url, source)
                linked = next((item for item in reversed(self.anchors) if item), None)
                if linked:
                    linked_url = urljoin(self.page_url, linked)
                    if Path(urlparse(linked_url).path).suffix.lower() in IMAGE_EXTENSIONS:
                        source_url = linked_url
                self.urls.append(normalize_source_url(source_url))
            return
        if tag == "source" and values.get("srcset"):
            for candidate in values["srcset"].split(","):
                source = candidate.strip().split()[0]
                if source:
                    self.urls.append(normalize_source_url(urljoin(self.page_url, source)))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchors:
            self.anchors.pop()


def asset_target(page: dict[str, Any], source_url: str) -> str:
    chapter = f"ch{page['chapter']:02d}" if page["chapter"] else "home"
    filename = Path(urlparse(source_url).path).name
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", filename)
    if not filename:
        filename = hashlib.sha256(source_url.encode()).hexdigest()[:16] + ".bin"
    return f"static/images/en/{chapter}/{filename}"


def discover_assets(manifest: dict[str, Any]) -> None:
    global_assets: dict[str, dict[str, Any]] = {
        item["source_url"]: item for item in manifest.get("assets", [])
    }
    for page in manifest["pages"]:
        cache = ROOT / page.get("cache", "")
        if not cache.exists():
            page["warnings"] = sorted(
                set(page.get("warnings", [])) | {"source cache missing during asset discovery"}
            )
            continue
        document = cache.read_text(encoding="utf-8")
        article = extract_article(document)
        parser = AssetParser(page["source_url"])
        parser.feed(article)

        page_assets = []
        for source_url in dict.fromkeys(parser.urls):
            target = asset_target(page, source_url)
            existing = global_assets.get(source_url)
            if existing and existing.get("target") != target:
                target = existing["target"]
            item = existing or {
                "source_url": source_url,
                "target": target,
                "status": "pending",
            }
            global_assets[source_url] = item
            page_assets.append(source_url)
        page["assets"] = page_assets

    manifest["assets"] = sorted(
        global_assets.values(),
        key=lambda item: (item["target"], item["source_url"]),
    )
    save_manifest(manifest)


def fetch_pages(manifest: dict[str, Any], *, refresh: bool) -> None:
    PAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    failures = []
    total = len(manifest["pages"])
    for index, page in enumerate(manifest["pages"], 1):
        destination = PAGE_CACHE_DIR / cache_name_for_page(page)
        headers_path = HEADER_CACHE_DIR / f"{page['id']}.headers"
        print(f"[page {index:02d}/{total}] {page['source_url']}", flush=True)
        try:
            metrics = curl_fetch(
                page["source_url"],
                destination,
                headers_path,
                refresh=refresh,
            )
            document = destination.read_text(encoding="utf-8")
            article = extract_article(document)
            title, _ = extract_title(article)
            if not title and page["id"] == "home":
                title = "The Internals of PostgreSQL"
            page.update(
                {
                    "title": title,
                    "status": "fetched",
                    "fetched_at": now_iso(),
                    "source_sha256": sha256_path(destination),
                    "source_bytes": destination.stat().st_size,
                    "content_type": metrics.get("content_type", ""),
                    "fetched_url": metrics.get("fetched_url", page["source_url"]),
                    "etag": metrics.get("etag"),
                    "last_modified": metrics.get("last_modified"),
                    "cache": str(destination.relative_to(ROOT)),
                    "warnings": [],
                }
            )
        except Exception as exc:  # Continue to record every failed source.
            page["status"] = "fetch_failed"
            page["warnings"] = [str(exc)]
            failures.append(page["source_url"])
        save_manifest(manifest)
        if index != total:
            time.sleep(0.25)

    discover_assets(manifest)
    if failures:
        raise RuntimeError(f"{len(failures)} page fetches failed; rerun to retry cached gaps")


def validate_image(path: Path) -> tuple[bool, str | None]:
    suffix = path.suffix.lower()
    if suffix == ".svg":
        try:
            ElementTree.parse(path)
            return True, None
        except ElementTree.ParseError as exc:
            return False, f"invalid SVG XML: {exc}"
    if Image is None:
        return False, "Pillow is unavailable"
    try:
        with Image.open(path) as image:
            image.verify()
        return True, None
    except Exception as exc:
        return False, f"image decode failed: {exc}"


def fetch_assets(
    manifest: dict[str, Any],
    *,
    refresh: bool,
    chapters: set[int] | None = None,
    jobs: int = 3,
) -> None:
    if jobs < 1 or jobs > 4:
        raise ValueError("asset download jobs must be between 1 and 4")
    failures = []
    selected = []
    for asset in manifest.get("assets", []):
        match = re.search(r"/ch(\d{2})/", asset["target"])
        chapter = int(match.group(1)) if match else None
        if chapters is None or chapter in chapters:
            selected.append(asset)
    total = len(selected)

    def fetch_one(index: int, asset: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        destination = ROOT / asset["target"]
        cache_destination = ASSET_CACHE_DIR / Path(asset["target"]).relative_to(
            "static/images/en"
        )
        headers_path = HEADER_CACHE_DIR / "assets" / (
            hashlib.sha256(asset["source_url"].encode()).hexdigest() + ".headers"
        )
        try:
            metrics = curl_fetch(
                asset["source_url"],
                cache_destination,
                headers_path,
                refresh=refresh,
                prefer_http=True,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cache_destination, destination)
            verified, warning = validate_image(destination)
            result = {
                "status": "fetched" if verified else "invalid",
                "fetched_at": now_iso(),
                "sha256": sha256_path(destination),
                "bytes": destination.stat().st_size,
                "content_type": metrics.get("content_type", ""),
                "fetched_url": (
                    metrics.get("fetched_url")
                    or asset.get("fetched_url")
                    or asset["source_url"]
                ),
                "etag": metrics.get("etag"),
                "last_modified": metrics.get("last_modified"),
                "verified": verified,
            }
            if warning:
                result["warning"] = warning
            else:
                result["warning"] = None
            return index, result
        except Exception as exc:
            return index, {
                "status": "fetch_failed",
                "warning": str(exc),
                "verified": False,
            }

    indexed = list(enumerate(selected, 1))
    completed_count = 0
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(fetch_one, index, asset): (index, asset)
            for index, asset in indexed
        }
        for future in as_completed(futures):
            index, asset = futures[future]
            _, result = future.result()
            warning = result.pop("warning", None)
            asset.update(result)
            if warning:
                asset["warning"] = warning
            else:
                asset.pop("warning", None)
            if asset.get("status") != "fetched" or not asset.get("verified"):
                failures.append(asset["source_url"])
            completed_count += 1
            print(
                f"[asset {completed_count:03d}/{total}] "
                f"source #{index:03d} {asset['source_url']}",
                flush=True,
            )
            save_manifest(manifest)
    if failures:
        raise RuntimeError(f"{len(failures)} asset fetches failed validation")


def strip_page_h1(article: str) -> str:
    return re.sub(
        r"<h1\b[^>]*>.*?</h1>",
        "",
        article,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )


def wrap_inline_math(article: str) -> str:
    """Mark TeX delimiters as math without touching code or existing spans."""
    parts = re.split(
        r"(<!--.*?-->|</?[A-Za-z][^>]*>)",
        article,
        flags=re.DOTALL,
    )
    excluded_depth = 0
    math_depth = 0
    excluded_tags = {"code", "pre", "script", "style"}
    display_math = re.compile(
        r"(?<!\$)\$\$(.+?)\$\$(?!\$)",
        flags=re.DOTALL,
    )
    inline_math = re.compile(
        r"(?<![\\$])\$(?!\$)(.+?)(?<![\\$])\$(?!\$)",
        flags=re.DOTALL,
    )

    def normalize_math_tex(value: str) -> str:
        return re.sub(
            r"\\text\{([^{}]*)\}",
            lambda match: (
                r"\text{"
                + re.sub(r"(?<!\\)_", r"\\_", match.group(1))
                + "}"
            ),
            value,
        )

    for index, part in enumerate(parts):
        if not part:
            continue
        if part.startswith("<"):
            closing = re.match(r"</\s*([A-Za-z0-9:-]+)", part)
            opening = re.match(r"<\s*([A-Za-z0-9:-]+)", part)
            if (
                closing
                and closing.group(1).lower() == "span"
                and math_depth > 0
            ):
                math_depth -= 1
            if closing and closing.group(1).lower() in excluded_tags:
                excluded_depth = max(0, excluded_depth - 1)
            elif (
                opening
                and opening.group(1).lower() in excluded_tags
                and not part.rstrip().endswith("/>")
            ):
                excluded_depth += 1
            elif (
                opening
                and opening.group(1).lower() == "span"
                and re.search(
                    r'class=["\'][^"\']*\bmath\b',
                    part,
                    flags=re.IGNORECASE,
                )
            ):
                math_depth += 1
            continue
        if excluded_depth == 0 and math_depth > 0:
            parts[index] = normalize_math_tex(part)
        elif excluded_depth == 0:
            part = display_math.sub(
                lambda match: (
                    '<span class="math display-math">'
                    f"$${normalize_math_tex(match.group(1))}$$"
                    "</span>"
                ),
                part,
            )
            parts[index] = inline_math.sub(
                lambda match: (
                    '<span class="math inline-math">'
                    f"${normalize_math_tex(match.group(1))}$"
                    "</span>"
                ),
                part,
            )
    return "".join(parts)


def internal_page_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping = {}
    for page in manifest["pages"]:
        url, _ = urldefrag(page["source_url"])
        mapping[normalize_source_url(url).rstrip("/")] = page
        parsed = urlparse(url)
        http_url = parsed._replace(scheme="http").geturl()
        mapping[http_url.rstrip("/")] = page
        if (
            parsed.path.endswith("/index.html")
            and (page.get("section") or 0) > 0
        ):
            short_path = parsed.path.removesuffix("/index.html") + ".html"
            for scheme in ("https", "http"):
                alias = parsed._replace(scheme=scheme, path=short_path).geturl()
                mapping[alias.rstrip("/")] = page
    return mapping


def source_asset_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        normalize_source_url(asset["source_url"]): asset
        for asset in manifest.get("assets", [])
    }


def relref_target(page: dict[str, Any], fragment: str) -> str:
    if page["id"] == "home":
        path = "/"
    else:
        target = page["target"].removeprefix("en/")
        path = "/" + target
    if fragment:
        path += "#" + fragment
    return '{{< relref "' + path + '" >}}'


def rewrite_article_urls(
    article: str,
    page: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[str, dict[str, str]]:
    page_map = internal_page_map(manifest)
    assets = source_asset_map(manifest)
    placeholders: dict[str, str] = {}
    article = wrap_inline_math(article)
    article = re.sub(
        r"<i\b[^>]*\bclass=[\"'][^\"']*\bfa(?:s|r|b)?\b[^\"']*[\"'][^>]*>"
        r"\s*</i>",
        "",
        article,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Chroma renders line-numbered examples as a two-column layout table.
    # Retain only the semantic code pane; line numbers are presentation data
    # and otherwise force Pandoc to emit a large raw-HTML table.
    article = re.sub(
        r'<div\s+class=["\']wrap-code\s+highlight["\'][^>]*>\s*'
        r"<div\b[^>]*>\s*<table\b[^>]*>\s*<tr>\s*"
        r"<td\b[^>]*>.*?</td>\s*<td\b[^>]*>\s*"
        r"(<pre\b.*?</pre>)\s*</td>\s*</tr>\s*</table>\s*</div>\s*</div>",
        r'<div class="wrap-code highlight">\1</div>',
        article,
        flags=re.IGNORECASE | re.DOTALL,
    )

    def rewrite_src(match: re.Match[str]) -> str:
        prefix, quote, raw_value = match.groups()
        source_url = normalize_source_url(urljoin(page["source_url"], html.unescape(raw_value)))
        asset = assets.get(source_url)
        if not asset:
            return match.group(0)
        web_path = "/" + asset["target"].removeprefix("static/")
        return f"{prefix}{quote}{html.escape(web_path, quote=True)}{quote}"

    article = re.sub(
        r"(\b(?:src|data-src)\s*=\s*)([\"'])(.*?)(?:\2)",
        rewrite_src,
        article,
        flags=re.IGNORECASE | re.DOTALL,
    )

    def rewrite_href(match: re.Match[str]) -> str:
        prefix, quote, raw_value = match.groups()
        raw_value = html.unescape(raw_value)
        # Let Pandoc see same-page anchors unchanged. In particular, its HTML
        # reader needs the original #fn / #fnref pairs to build Markdown notes.
        if raw_value.startswith("#"):
            return match.group(0)
        # Normalize two upstream Chapter 12 link typos: a missing percent
        # before URL-encoded "#" and an encoded fragment marker in a GitHub
        # source link.
        raw_value = re.sub(r"\.html23(?=[A-Za-z0-9])", ".html#", raw_value)
        raw_value = raw_value.replace("%23", "#")
        absolute = urljoin(page["source_url"], raw_value)
        base, fragment = urldefrag(absolute)
        normalized = normalize_source_url(base).rstrip("/")
        destination_page = page_map.get(normalized)
        if not destination_page:
            # A small number of upstream links contain an extra directory
            # prefix (for example ./pgsql07/02.html from Chapter 3).
            # Recover the canonical book-relative suffix deterministically.
            suffix = re.search(
                r"(pgsql\d{2}/(?:\d{2}(?:/\d{2})?|index)\.html)$",
                urlparse(base).path,
            )
            if suffix:
                canonical = urljoin(BASE_URL, suffix.group(1)).rstrip("/")
                destination_page = page_map.get(canonical)
        if destination_page:
            token = f"ref-{len(placeholders):04d}"
            placeholders[f"https://local.invalid/{token}"] = relref_target(
                destination_page, fragment
            )
            return f"{prefix}{quote}https://local.invalid/{token}{quote}"

        asset = assets.get(normalize_source_url(base))
        if asset:
            web_path = "/" + asset["target"].removeprefix("static/")
            if fragment:
                web_path += "#" + fragment
            return f"{prefix}{quote}{html.escape(web_path, quote=True)}{quote}"
        return match.group(0)

    article = re.sub(
        r"(\bhref\s*=\s*)([\"'])(.*?)(?:\2)",
        rewrite_href,
        article,
        flags=re.IGNORECASE | re.DOTALL,
    )
    article = re.sub(
        r"\s+(?:target|rel)\s*=\s*([\"']).*?\1",
        "",
        article,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Pandoc reads the language from <pre>, while Chroma puts it only on the
    # nested <code>. Copy it upward so fenced Markdown keeps syntax metadata.
    def copy_code_language(match: re.Match[str]) -> str:
        pre_open, language, code_open = match.groups()
        if re.search(r"\bclass\s*=", pre_open, flags=re.IGNORECASE):
            return match.group(0)
        return f'{pre_open[:-1]} class="language-{language}">{code_open}'

    article = re.sub(
        r"(<pre\b[^>]*>)(?=\s*<code\b[^>]*\bclass=[\"'][^\"']*\blanguage-([A-Za-z0-9_+-]+)\b[^\"']*[\"'][^>]*>)(\s*<code\b[^>]*>)",
        copy_code_language,
        article,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return article, placeholders


def run_pandoc(article: str) -> str:
    command = [
        "pandoc",
        "--from=html",
        "--to=gfm+attributes",
        "--wrap=none",
        f"--lua-filter={LUA_FILTER}",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        input=article,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pandoc failed: {completed.stderr.strip()}")
    return completed.stdout


def postprocess_markdown(markdown: str, placeholders: dict[str, str]) -> str:
    for placeholder, replacement in placeholders.items():
        markdown = markdown.replace(placeholder, replacement)
    markdown = re.sub(r"``` \{\.([A-Za-z0-9_+-]+)\}", r"```\1", markdown)
    markdown = re.sub(r"``` ([A-Za-z0-9_+-]+)", r"```\1", markdown)
    markdown = markdown.replace(
        "![](/images/en/home/title3.png)",
        "![The Internals of PostgreSQL](/images/en/home/title3.png)",
    )
    markdown = re.sub(r"\n{4,}", "\n\n\n", markdown)
    return markdown.strip() + "\n"


def front_matter(page: dict[str, Any], description: str) -> str:
    metadata: dict[str, Any] = {
        "title": page["title"],
        "linkTitle": page["title"],
    }
    if page["id"] == "home":
        metadata["cascade"] = {"type": "docs"}
        metadata["breadcrumbs"] = False
    if page.get("weight") is not None:
        metadata["weight"] = page["weight"]
    if description:
        metadata["description"] = description
    metadata["params"] = {
        "source": {
            "url": page["source_url"],
            "fetchedAt": page["fetched_at"],
            "sha256": page["source_sha256"],
        }
    }
    rendered = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    )
    return f"---\n{rendered}---\n\n"


def write_docs_landing(manifest: dict[str, Any]) -> None:
    chapters = [
        page
        for page in manifest["pages"]
        if page.get("chapter") and page.get("section") == 0
    ]
    lines = [
        "---",
        'title: "The Internals of PostgreSQL"',
        'linkTitle: "Contents"',
        "weight: 1",
        "cascade:",
        "  type: docs",
        "---",
        "",
        "This section contains the locally converted English edition.",
        "",
        "## Contents",
        "",
    ]
    for page in chapters:
        lines.append(
            f"- [{page['title']}]({{{{< relref \"/docs/ch{page['chapter']:02d}/\" >}}}})"
        )
    lines.extend(
        [
            "",
            "> **Source and copyright:** The original work is by Hironobu Suzuki and is "
            "[published at InterDB](https://www.interdb.jp/pg/). See the source page for "
            "the applicable copyright and usage terms.",
            "",
        ]
    )
    destination = CONTENT_EN / "docs" / "_index.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")


def convert_pages(
    manifest: dict[str, Any],
    *,
    chapters: set[int] | None = None,
) -> None:
    failures = []
    selected = [
        page
        for page in manifest["pages"]
        if chapters is None or page.get("chapter") in chapters
    ]
    for index, page in enumerate(selected, 1):
        print(f"[convert {index:02d}/{len(selected)}] {page['target']}", flush=True)
        try:
            cache = ROOT / page["cache"]
            document = cache.read_text(encoding="utf-8")
            article = extract_article(document)
            page["title"], _ = extract_title(article)
            if not page["title"] and page["id"] == "home":
                page["title"] = "The Internals of PostgreSQL"
            description = description_from_article(article)
            if page["id"] == "home":
                description = (
                    "A guide to PostgreSQL internals that explains how its "
                    "major subsystems work together."
                )
            article = strip_page_h1(article)
            article, placeholders = rewrite_article_urls(article, page, manifest)
            markdown = run_pandoc(article)
            markdown = postprocess_markdown(markdown, placeholders)
            destination = ROOT / page["target"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                front_matter(page, description) + markdown,
                encoding="utf-8",
            )
            page["status"] = "converted"
            page["target_sha256"] = sha256_path(destination)
            page["converted_at"] = now_iso()
            page["warnings"] = []
        except Exception as exc:
            page["status"] = "conversion_failed"
            page["warnings"] = [str(exc)]
            failures.append(page["source_url"])
        save_manifest(manifest)
    write_docs_landing(manifest)
    if failures:
        raise RuntimeError(f"{len(failures)} page conversions failed")


def validate(manifest: dict[str, Any]) -> None:
    errors = []
    warnings = []
    pages = manifest.get("pages", [])
    assets = manifest.get("assets", [])
    expected_chapter_pages = {
        1: 5,
        2: 3,
        3: 12,
        4: 3,
        5: 11,
        6: 7,
        7: 3,
        8: 7,
        9: 11,
        10: 6,
        11: 5,
        12: 9,
    }
    if len(pages) != 83:
        errors.append(f"expected 83 pages, found {len(pages)}")
    if len(assets) != 209:
        errors.append(f"expected 209 active image assets, found {len(assets)}")
    asset_targets = [asset["target"] for asset in assets]
    if len(set(asset_targets)) != len(asset_targets):
        errors.append("duplicate asset target paths found in manifest")

    chapter_counts = {
        chapter: sum(page.get("chapter") == chapter for page in pages)
        for chapter in expected_chapter_pages
    }
    if chapter_counts != expected_chapter_pages:
        errors.append(
            f"chapter page distribution mismatch: {chapter_counts}"
        )
    if sum(page.get("id") == "home" for page in pages) != 1:
        errors.append("expected exactly one source home page")

    expected_markdown = {
        page["target"] for page in pages
    } | {"en/docs/_index.md"}
    actual_markdown = {
        str(path.relative_to(ROOT))
        for path in CONTENT_EN.rglob("*.md")
    }
    for missing in sorted(expected_markdown - actual_markdown):
        errors.append(f"missing generated Markdown: {missing}")
    for extra in sorted(actual_markdown - expected_markdown):
        errors.append(f"unexpected generated Markdown: {extra}")

    for path in (CONTENT_EN / "docs").glob("ch*/*/*.md"):
        errors.append(f"nested section directory is not allowed: {path.relative_to(ROOT)}")

    targets = set()
    titles = set()
    target_image_references: list[str] = []
    source_heading_total = 0
    target_heading_total = 0
    source_notice_total = 0
    target_notice_total = 0
    source_footnote_total = 0
    target_footnote_total = 0
    source_image_total = 0
    for page in pages:
        target = ROOT / page["target"]
        if page.get("status") != "converted":
            errors.append(f"page not converted: {page['source_url']} ({page.get('status')})")
        if page["target"] in targets:
            errors.append(f"duplicate target: {page['target']}")
        targets.add(page["target"])
        if not target.exists() or target.stat().st_size < 100:
            errors.append(f"missing or empty target: {page['target']}")
            continue
        text = target.read_text(encoding="utf-8")
        body_parts = text.split("---", 2)
        body = body_parts[2] if len(body_parts) == 3 else text
        if page["source_url"] not in text:
            errors.append(f"source URL missing from front matter: {page['target']}")
        if page.get("target_sha256") != sha256_path(target):
            errors.append(f"page hash mismatch: {page['target']}")
        if "https://local.invalid/" in text:
            errors.append(f"unresolved internal-link placeholder: {page['target']}")
        if re.search(r"<(?:script|nav)\b", text, re.IGNORECASE):
            errors.append(f"theme/navigation HTML leaked into: {page['target']}")
        if "wrap-code" in text or "box notices" in text:
            errors.append(f"Relearn theme class leaked into: {page['target']}")
        if r"\$" in text:
            errors.append(f"escaped math delimiter remains in: {page['target']}")

        fence_count = sum(
            line.lstrip("> ").startswith("```")
            for line in body.splitlines()
        )
        if fence_count % 2:
            errors.append(f"unbalanced fenced code blocks: {page['target']}")

        for match in re.finditer(
            r"!?\[[^\]]*\]\(([^)\s]+)",
            body,
        ):
            destination = match.group(1)
            if not destination.startswith(
                ("http://", "https://", "mailto:", "#", "/", "{{<")
            ):
                errors.append(
                    f"unresolved relative link in {page['target']}: {destination}"
                )

        target_image_references.extend(
            re.findall(
                r"!\[[^\]]*\]\((/images/en/[^)\s]+)\)",
                body,
            )
        )

        cache = ROOT / page.get("cache", "")
        if not cache.exists():
            errors.append(f"source cache missing: {page['source_url']}")
        else:
            article = extract_article(cache.read_text(encoding="utf-8"))
            visible = re.sub(
                r"<!--.*?-->",
                "",
                article,
                flags=re.DOTALL,
            )
            without_figures = re.sub(
                r"<figure\b.*?</figure>",
                "",
                visible,
                flags=re.IGNORECASE | re.DOTALL,
            )
            source_headings = len(
                re.findall(r"<h[2-6]\b", without_figures, re.IGNORECASE)
            )
            target_headings = len(
                re.findall(
                    r"^(?:>\s*)*#{2,6}\s+",
                    body,
                    flags=re.MULTILINE,
                )
            )
            if source_headings != target_headings:
                errors.append(
                    f"heading count mismatch in {page['target']}: "
                    f"source={source_headings}, target={target_headings}"
                )
            source_heading_total += source_headings
            target_heading_total += target_headings

            source_notices = len(
                re.findall(
                    r'<div\b[^>]*class=["\'][^"\']*\bnotices\b',
                    visible,
                    flags=re.IGNORECASE,
                )
            )
            target_notices = len(
                re.findall(
                    r"^> \*\*[^*\n]+:\*\*\s*$",
                    body,
                    flags=re.MULTILINE,
                )
            )
            if source_notices != target_notices:
                errors.append(
                    f"notice count mismatch in {page['target']}: "
                    f"source={source_notices}, target={target_notices}"
                )
            source_notice_total += source_notices
            target_notice_total += target_notices

            source_footnotes = len(
                re.findall(
                    r'<div\b[^>]*class=["\'][^"\']*\bfootnotes\b',
                    visible,
                    flags=re.IGNORECASE,
                )
            )
            target_footnotes = len(
                re.findall(r"^\[\^[^\]]+\]:", body, flags=re.MULTILINE)
            )
            if source_footnotes != target_footnotes:
                errors.append(
                    f"footnote count mismatch in {page['target']}: "
                    f"source={source_footnotes}, target={target_footnotes}"
                )
            source_footnote_total += source_footnotes
            target_footnote_total += target_footnotes

            parser = AssetParser(page["source_url"])
            parser.feed(article)
            source_images = len(parser.urls)
            target_images = len(
                re.findall(
                    r"!\[[^\]]*\]\(/images/en/[^)\s]+\)",
                    body,
                )
            )
            if source_images != target_images:
                errors.append(
                    f"image occurrence mismatch in {page['target']}: "
                    f"source={source_images}, target={target_images}"
                )
            source_image_total += source_images

        if page.get("title") in titles:
            warnings.append(f"duplicate title: {page.get('title')}")
        titles.add(page.get("title"))

    for target in sorted(expected_markdown):
        path = ROOT / target
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for relref in re.findall(r'\{\{< relref "([^"]+)" >\}\}', text):
            path_part = relref.split("#", 1)[0]
            if path_part == "/":
                destination = CONTENT_EN / "_index.md"
            elif path_part.endswith("/"):
                destination = CONTENT_EN / path_part.lstrip("/") / "_index.md"
            else:
                destination = CONTENT_EN / path_part.lstrip("/")
            if not destination.exists():
                errors.append(
                    f"relref target missing in {target}: {relref}"
                )

    referenced_targets = set()
    for page in pages:
        for source_url in page.get("assets", []):
            asset = next(
                (
                    item
                    for item in assets
                    if item["source_url"] == source_url
                ),
                None,
            )
            if asset:
                referenced_targets.add(asset["target"])

    asset_web_paths = {
        "/" + asset["target"].removeprefix("static/")
        for asset in assets
    }
    referenced_image_paths = set(target_image_references)
    for missing in sorted(asset_web_paths - referenced_image_paths):
        errors.append(f"asset is not referenced by Markdown: {missing}")
    for unknown in sorted(referenced_image_paths - asset_web_paths):
        errors.append(f"Markdown references an unknown asset: {unknown}")

    for asset in assets:
        target = ROOT / asset["target"]
        if asset.get("status") != "fetched" or not asset.get("verified"):
            errors.append(
                f"asset not fetched/verified: {asset['source_url']} ({asset.get('status')})"
            )
        if not target.exists():
            errors.append(f"missing asset target: {asset['target']}")
        elif asset.get("sha256") != sha256_path(target):
            errors.append(f"asset hash mismatch: {asset['target']}")
        if asset["target"] not in referenced_targets:
            warnings.append(f"unreferenced asset: {asset['target']}")

    report = {
        "checked_at": now_iso(),
        "pages": len(pages),
        "converted_pages": sum(page.get("status") == "converted" for page in pages),
        "markdown_files": len(actual_markdown),
        "chapter_page_counts": chapter_counts,
        "assets": len(assets),
        "verified_assets": sum(
            bool(asset.get("verified")) for asset in assets
        ),
        "source_image_occurrences": source_image_total,
        "target_image_occurrences": len(target_image_references),
        "source_headings": source_heading_total,
        "target_headings": target_heading_total,
        "source_notices": source_notice_total,
        "target_notices": target_notice_total,
        "source_footnotes": source_footnote_total,
        "target_footnotes": target_footnote_total,
        "errors": errors,
        "warnings": warnings,
    }
    report_path = ROOT / "sources" / "interdb-pg" / "validation.yaml"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(report, handle, allow_unicode=True, sort_keys=False, width=120)
    print(yaml.safe_dump(report, allow_unicode=True, sort_keys=False, width=120))
    if errors:
        raise RuntimeError(f"validation failed with {len(errors)} errors")


def command_inventory(args: argparse.Namespace) -> None:
    manifest = discover_pages(refresh_sitemap=args.refresh)
    print(
        f"Discovered {len(manifest['pages'])} book pages; "
        f"sitemap SHA-256 {manifest['source']['sitemap_sha256']}"
    )


def command_fetch_pages(args: argparse.Namespace) -> None:
    manifest = discover_pages(refresh_sitemap=args.refresh_sitemap)
    fetch_pages(manifest, refresh=args.refresh)
    print(
        f"Fetched {len(manifest['pages'])} pages and discovered "
        f"{len(manifest.get('assets', []))} assets"
    )


def parse_chapter_selection(value: str | None) -> set[int] | None:
    if not value:
        return None
    chapters = {int(item.strip()) for item in value.split(",") if item.strip()}
    invalid = sorted(chapter for chapter in chapters if chapter < 1 or chapter > 12)
    if invalid:
        raise ValueError(f"chapter numbers must be between 1 and 12: {invalid}")
    return chapters


def command_fetch_assets(args: argparse.Namespace) -> None:
    manifest = load_manifest()
    if not manifest:
        raise RuntimeError("manifest missing; run inventory and fetch-pages first")
    chapters = parse_chapter_selection(args.chapters)
    fetch_assets(
        manifest,
        refresh=args.refresh,
        chapters=chapters,
        jobs=args.jobs,
    )
    selected = "all chapters" if chapters is None else f"chapters {sorted(chapters)}"
    print(f"Fetched and verified assets for {selected}")


def command_convert(args: argparse.Namespace) -> None:
    manifest = load_manifest()
    if not manifest:
        raise RuntimeError("manifest missing; run inventory and fetch-pages first")
    chapters = parse_chapter_selection(args.chapters)
    convert_pages(manifest, chapters=chapters)
    selected = "all source pages" if chapters is None else f"chapters {sorted(chapters)}"
    print(f"Converted {selected}")


def command_validate(_: argparse.Namespace) -> None:
    manifest = load_manifest()
    if not manifest:
        raise RuntimeError("manifest missing")
    validate(manifest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--refresh", action="store_true")
    inventory.set_defaults(func=command_inventory)

    pages = subparsers.add_parser("fetch-pages")
    pages.add_argument("--refresh", action="store_true")
    pages.add_argument("--refresh-sitemap", action="store_true")
    pages.set_defaults(func=command_fetch_pages)

    assets = subparsers.add_parser("fetch-assets")
    assets.add_argument("--refresh", action="store_true")
    assets.add_argument(
        "--chapters",
        help="comma-separated chapter numbers; omit to fetch every asset",
    )
    assets.add_argument(
        "--jobs",
        type=int,
        default=3,
        help="bounded parallel downloads (1-4, default: 3)",
    )
    assets.set_defaults(func=command_fetch_assets)

    convert = subparsers.add_parser("convert")
    convert.add_argument(
        "--chapters",
        help="comma-separated chapter numbers; omit to convert every page",
    )
    convert.set_defaults(func=command_convert)

    check = subparsers.add_parser("validate")
    check.set_defaults(func=command_validate)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
