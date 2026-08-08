#!/usr/bin/env python3
"""Mine wild Mac OS text from classic HFS disk images.

The Mac codepages looked unreachable: Common Crawl's x-mac-* labels are
nearly always a misconfigured server serving windows-1251, and the Mac
items in archive.org's software library are raw HFS floppy images rather
than zips.  Modern macOS cannot even mount them -- Apple dropped classic
HFS -- so ``hdiutil attach`` reports "image not recognized".

``machfs`` reads them in pure Python, which reopens the whole family.  A
classic Mac disk marks each file with a four-character type code, so the
documentation can be picked out exactly: type ``TEXT``.  Those READMEs and
manuals are mac-roman, and the high bytes that prove it are the curly
quotes, bullets and ™/© signs Mac authors used constantly and which sit at
different code points from every other Latin codepage.

Candidates must decode under the target codec, contain real prose, carry
some high bytes, and not be valid UTF-8 or a binary that merely claims the
TEXT type -- Mac software ships plenty of both.

Dependencies: the standard library, plus ``machfs``.

Usage::

    uv run --with machfs python3 scripts/mine_mac_images.py --items 20
    uv run --with machfs python3 scripts/mine_mac_images.py --item maelstrom-68k
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_OUTPUT = Path("scripts/.cache/wild-mac")
COLLECTION = "softwarelibrary_mac"
IMAGE_SUFFIXES = (".dsk", ".img", ".image")

MIN_SIZE = 400
MAX_SIZE = 200 * 1024
MIN_LETTERS = 200
MIN_HIGH_BYTES = 12
MAX_HIGH_FRACTION = 0.25  # beyond this it is binary wearing a TEXT type

MANIFEST_COLUMNS = (
    "path", "item", "member", "codec", "language", "size", "letters",
    "high_bytes", "detected", "status",
)


def _load_chardet():  # noqa: ANN202
    try:
        import chardet  # noqa: PLC0415
    except ImportError:
        return None
    return chardet


def http_get(url: str, timeout: int = 240) -> bytes:
    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "chardet-test-data-miner/0.1"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def collection_items(collection: str, rows: int) -> list[str]:
    query = urllib.parse.quote(f"collection:{collection}")
    url = (
        f"https://archive.org/advancedsearch.php?q={query}"
        f"&fl%5B%5D=identifier&rows={rows}&output=json"
    )
    payload = json.loads(http_get(url, timeout=90))
    return [doc["identifier"] for doc in payload["response"]["docs"]]


def image_url(item: str) -> str | None:
    payload = json.loads(http_get(f"https://archive.org/metadata/{item}", timeout=90))
    for entry in payload.get("files", []):
        if entry["name"].lower().endswith(IMAGE_SUFFIXES):
            return (
                f"https://archive.org/download/{item}/"
                f"{urllib.parse.quote(entry['name'])}"
            )
    return None


def text_members(blob: bytes) -> list[tuple[str, bytes]]:
    """Every TEXT-typed file in an HFS volume, as (path, data)."""
    import machfs  # noqa: PLC0415

    volume = machfs.Volume()
    try:
        volume.read(blob)
    except Exception:  # noqa: BLE001 - malformed or non-HFS image
        return []
    found: list[tuple[str, bytes]] = []

    def walk(folder, prefix: str = "") -> None:  # noqa: ANN001
        for name, obj in folder.items():
            path = f"{prefix}/{name}"
            if isinstance(obj, machfs.Folder):
                walk(obj, path)
            elif getattr(obj, "type", b"") == b"TEXT" and obj.data:
                found.append((path, obj.data))

    walk(volume)
    return found


def evaluate(data: bytes, codec: str) -> tuple[int, int] | None:
    """Return (letters, high bytes) if this is usable text in *codec*."""
    if not MIN_SIZE <= len(data) <= MAX_SIZE or b"\x00" in data:
        return None
    high = sum(1 for b in data if b >= 0x80)
    if high < MIN_HIGH_BYTES or high > len(data) * MAX_HIGH_FRACTION:
        return None
    # Valid UTF-8 means it is a UTF-8 file, whatever its type code says.
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return None
    try:
        text = data.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None
    # Classic Mac uses CR line endings; anything else in C0 is binary.
    if any(ord(ch) < 0x20 and ch not in "\t\n\r" for ch in text):
        return None
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < MIN_LETTERS:
        return None
    return letters, high


def write_candidates(rows: list[tuple], codec: str, language: str, output_dir: Path, chardet_module) -> None:  # noqa: ANN001
    target_dir = output_dir / codec
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    existing = manifest_path.is_file()
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for item, member, data, letters, high in rows:
            digest = hashlib.sha256(data).hexdigest()[:12]
            path = target_dir / f"machfs_{digest}.txt"
            path.write_bytes(data)
            detected = ""
            if chardet_module is not None:
                detected = chardet_module.detect(data)["encoding"] or "None"
            writer.writerow([
                path.relative_to(output_dir), item, member, codec, language,
                len(data), letters, high, detected, "strict",
            ])
    print(f"Wrote {len(rows)} candidates to {target_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--item", default="", help="a single archive.org item")
    parser.add_argument("--items", type=int, default=24, help="items to try")
    parser.add_argument("--codec", default="mac-roman")
    parser.add_argument("--language", default="en")
    parser.add_argument("--per-item", type=int, default=2)
    parser.add_argument("--max-files", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    try:
        import machfs  # noqa: F401, PLC0415
    except ImportError:
        sys.exit(
            "machfs is required to read classic HFS images.\n"
            "  uv run --with machfs python3 scripts/mine_mac_images.py ..."
        )

    items = [arguments.item] if arguments.item else collection_items(
        arguments.collection, arguments.items
    )
    print(f"{len(items)} item(s) to try")

    chardet_module = _load_chardet()
    rows: list[tuple] = []
    seen: set[str] = set()
    for item in items:
        if len(rows) >= arguments.max_files:
            break
        try:
            url = image_url(item)
            if url is None:
                continue
            blob = http_get(url)
        except (urllib.error.URLError, OSError, ValueError) as error:
            print(f"  {item}: {str(error)[:50]}")
            continue
        kept = 0
        for member, data in text_members(blob):
            if kept >= arguments.per_item or len(rows) >= arguments.max_files:
                break
            metrics = evaluate(data, arguments.codec)
            if metrics is None:
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            rows.append((item, member, data, *metrics))
            kept += 1
        if kept:
            print(f"  {item}: {kept} candidate(s)")

    if not rows:
        print(f"No {arguments.codec} candidates found.")
        return 1
    write_candidates(
        rows, arguments.codec, arguments.language, arguments.output, chardet_module
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
