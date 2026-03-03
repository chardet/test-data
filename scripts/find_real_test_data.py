#!/usr/bin/env python3
"""Search public archives for authentic encoded text files.

Best-effort script to supplement generated test data with real-world
examples from archive.org's Wayback Machine CDX API.

Usage:
    python3 scripts/find_real_test_data.py --dry-run
    python3 scripts/find_real_test_data.py --encodings utf-7 hp-roman8
    python3 scripts/find_real_test_data.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.encoding_gaps import find_gaps, get_codec  # noqa: E402

# ---------------------------------------------------------------------------
# Archive.org CDX API helpers
# ---------------------------------------------------------------------------

CDX_API = "https://web.archive.org/cdx/search/cdx"

# Mapping from encoding prefix to charset strings found in HTTP headers
# and meta tags.  Used to search archive.org's CDX index.
ENCODING_CHARSET_QUERIES: dict[str, list[str]] = {
    "utf-7": ["utf-7"],
    "hp-roman8": ["hp-roman8", "roman8"],
    "cp273": ["cp273", "ibm273"],
    "cp1140": ["cp1140", "ibm1140"],
    "cp864": ["cp864", "ibm864"],
    "cp1006": ["cp1006", "ibm1006"],
    "iso-2022-jp-2004": ["iso-2022-jp-2004"],
    "iso-2022-jp-ext": ["iso-2022-jp-ext"],
    "iso-8859-6": ["iso-8859-6"],
    "cp720": ["cp720"],
    "windows-1256": ["windows-1256"],
}

# Domains likely to have pages in specific encodings
ENCODING_DOMAINS: dict[str, list[str]] = {
    "utf-7": ["*.gov", "*.edu"],
    "hp-roman8": ["docs.hp.com", "*.hp.com"],
    "cp273": ["*.de"],
    "cp864": ["*.sa", "*.ae"],
    "iso-2022-jp-2004": ["*.jp"],
    "iso-2022-jp-ext": ["*.jp"],
}

# Rate limit: seconds between requests to archive.org
RATE_LIMIT = 2.0


def _cdx_search(
    charset: str,
    domain: str = "*",
    limit: int = 10,
) -> list[dict]:
    """Query archive.org CDX API for pages with a specific charset.

    Returns list of {url, timestamp, mimetype} dicts.
    """
    params = (
        f"?url={domain}"
        f"&output=json"
        f"&fl=url,timestamp,mimetype"
        f"&filter=mimetype:text/html"
        f"&limit={limit}"
    )
    url = CDX_API + params
    req = Request(url, headers={"User-Agent": "chardet-test-data/1.0"})

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"    CDX query failed for charset={charset}: {exc}")
        return []

    if not data or len(data) < 2:
        return []

    # First row is headers
    headers = data[0]
    results = []
    for row in data[1:]:
        entry = dict(zip(headers, row))
        results.append(entry)
    return results


def _fetch_wayback_page(url: str, timestamp: str) -> bytes | None:
    """Fetch a page from the Wayback Machine."""
    wayback_url = f"https://web.archive.org/web/{timestamp}id_/{url}"
    req = Request(wayback_url, headers={"User-Agent": "chardet-test-data/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"    Fetch failed for {wayback_url}: {exc}")
        return None


def _validate_encoding(raw: bytes, codec: str) -> bool:
    """Check if raw bytes decode cleanly in the expected encoding."""
    try:
        raw.decode(codec)
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def _strip_html(raw: bytes, codec: str) -> bytes:
    """Naively strip HTML tags and decode to get text, then re-encode."""
    import re

    try:
        text = raw.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return raw

    # Strip tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    try:
        return text.encode(codec)
    except (UnicodeEncodeError, LookupError):
        return raw


# ---------------------------------------------------------------------------
# Main search logic
# ---------------------------------------------------------------------------


def search_for_encoding(
    encoding: str,
    language: str,
    base_dir: Path,
    dry_run: bool,
    manifest: list[dict],
) -> bool:
    """Try to find real-world files for an encoding-language pair."""
    codec = get_codec(encoding)
    dst_dir = base_dir / f"{encoding}-{language}"

    charsets = ENCODING_CHARSET_QUERIES.get(encoding, [encoding])
    domains = ENCODING_DOMAINS.get(encoding, ["*"])

    candidates: list[tuple[str, bytes]] = []

    for charset in charsets:
        for domain in domains:
            if len(candidates) >= 3:
                break

            if dry_run:
                print(
                    f"  DRY-RUN: would search CDX for "
                    f"charset={charset} domain={domain}"
                )
                continue

            print(f"  Searching CDX: charset={charset} domain={domain}")
            results = _cdx_search(charset, domain)
            time.sleep(RATE_LIMIT)

            for entry in results:
                if len(candidates) >= 3:
                    break

                raw = _fetch_wayback_page(entry["url"], entry["timestamp"])
                time.sleep(RATE_LIMIT)

                if raw is None:
                    continue

                if not _validate_encoding(raw, codec):
                    continue

                # Strip HTML to get plain text
                text_bytes = _strip_html(raw, codec)
                if len(text_bytes) < 50:
                    continue

                # Trim to reasonable size (max ~10KB)
                if len(text_bytes) > 10240:
                    text_bytes = text_bytes[:10240]

                candidates.append((entry["url"], text_bytes))
                print(
                    f"    Found: {entry['url']} "
                    f"({len(text_bytes)} bytes, {entry['timestamp']})"
                )

    if dry_run:
        return bool(charsets)

    if not candidates:
        return False

    dst_dir.mkdir(exist_ok=True)
    for i, (source_url, data) in enumerate(candidates):
        fname = f"archive_{i:03d}.txt"
        (dst_dir / fname).write_bytes(data)
        manifest.append({
            "path": f"{encoding}-{language}/{fname}",
            "source": source_url,
            "method": "archive.org",
            "retrieved": str(date.today()),
            "notes": "Real-world text from Wayback Machine",
        })

    print(f"  SAVED {encoding}-{language}/ ({len(candidates)} files)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search public archives for real-world encoded text files.",
    )
    parser.add_argument(
        "--base-dir",
        default=".",
        help="Root of the test-data repo (default: .)",
    )
    parser.add_argument(
        "--encodings",
        nargs="+",
        default=None,
        help="Filter to specific encoding prefixes (default: niche encodings)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be searched without downloading",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()

    # Default target: niche encodings that benefit most from real-world data
    niche_encodings = set(ENCODING_CHARSET_QUERIES.keys())

    all_gaps = find_gaps(base_dir)
    if not all_gaps:
        print("No gaps found.")
        return

    if args.encodings:
        target_encodings = set(args.encodings)
    else:
        target_encodings = niche_encodings

    gaps = [(e, l) for e, l in all_gaps if e in target_encodings]
    if not gaps:
        print(f"No gaps for target encodings: {sorted(target_encodings)}")
        return

    print(f"Searching archives for {len(gaps)} encoding-language pairs...")
    if args.dry_run:
        print("(DRY RUN)\n")
    else:
        print()

    # Load existing manifest
    manifest: list[dict] = []
    manifest_path = base_dir / "scripts" / "manifest.json"
    if manifest_path.is_file() and not args.dry_run:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

    found = 0
    for encoding, language in gaps:
        print(f"[{encoding}-{language}]")
        ok = search_for_encoding(
            encoding, language, base_dir, args.dry_run, manifest,
        )
        if ok:
            found += 1

    # Save manifest
    if not args.dry_run and manifest:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")

    print(f"\nDone: found real-world data for {found}/{len(gaps)} pairs")


if __name__ == "__main__":
    main()
