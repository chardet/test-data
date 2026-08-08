#!/usr/bin/env python3
"""Mine wild DOS-codepage text from archive.org software items.

The DOS codepages are the last family with a plausible wild corpus and no
route to it.  Common Crawl cannot help -- in 2019 an ibm850 label is
nearly always a misconfigured server -- but archive.org holds tens of
thousands of original DOS programs, each a zip of the files as shipped,
READMEs included.

The hard part is not finding text, it is proving *which* codepage it is.
Sibling DOS codepages overlap almost completely in the accented range:
cp437 and cp850 place the German umlauts at identical positions, so a
German README is equally valid under either and proves neither.  A
candidate is therefore only accepted when it contains a byte that decodes
differently under its claimed codepage than under every other single-byte
encoding this repo knows -- the same guard the generator uses, from
encoding_overlaps.py.

Everything else is the discipline the other miners converged on: real
prose rather than binary wearing a text extension, not valid UTF-8, no
control characters, and no mojibake casing.

Dependencies: the standard library.

Usage::

    python3 scripts/mine_dos_software.py --codec cp850 --language de \\
        --query "deutsch OR german" --items 40
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from encoding_overlaps import DISTINGUISHING_BYTES  # noqa: E402

DEFAULT_OUTPUT = Path("scripts/.cache/wild-dos")
TEXT_SUFFIXES = (
    ".txt", ".doc", ".nfo", ".diz", ".me", ".1st", ".now", ".asc", ".dok",
    ".hlp", ".ans", ".readme", ".lis",
)

MIN_SIZE = 400
MAX_SIZE = 200 * 1024
MIN_LETTERS = 200
MIN_HIGH_BYTES = 20
MAX_HIGH_FRACTION = 0.45
MAX_CASE_NOISE = 0.25

MANIFEST_COLUMNS = (
    "path", "item", "member", "codec", "language", "size", "letters",
    "high_bytes", "unique_bytes", "detected", "status",
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


def search_items(query: str, rows: int) -> list[str]:
    encoded = urllib.parse.quote(f"mediatype:software AND ({query})")
    url = (
        f"https://archive.org/advancedsearch.php?q={encoded}"
        f"&fl%5B%5D=identifier&rows={rows}&output=json"
    )
    payload = json.loads(http_get(url, timeout=90))
    return [doc["identifier"] for doc in payload["response"]["docs"]]


IMAGE_SUFFIXES = (".img", ".ima", ".dsk", ".vfd")


def item_archive_url(item: str) -> tuple[str, str] | None:
    """Return (url, kind) for an item's payload, kind being zip or image.

    The shareware collection ships zips of the files as distributed, but
    the games collection ships raw floppy images -- which is where most of
    the non-English software is, and therefore most of the codepages worth
    having.
    """
    payload = json.loads(http_get(f"https://archive.org/metadata/{item}", timeout=90))
    images = []
    for entry in payload.get("files", []):
        name = entry["name"].lower()
        size = int(entry.get("size", 0))
        if size > 40_000_000:
            continue
        url = (
            f"https://archive.org/download/{item}/"
            f"{urllib.parse.quote(entry['name'])}"
        )
        if name.endswith(".zip"):
            return url, "zip"
        if name.endswith(IMAGE_SUFFIXES):
            images.append(url)
    return (images[0], "image") if images else None


def image_members(blob: bytes) -> list[tuple[str, bytes]]:
    """Every file inside a FAT floppy image, as (path, data)."""
    try:
        from pyfatfs.PyFatFS import PyFatFS  # noqa: PLC0415
    except ImportError:
        print("  (pyfatfs not installed -- skipping disk images)")
        return []
    import tempfile  # noqa: PLC0415

    found: list[tuple[str, bytes]] = []
    with tempfile.NamedTemporaryFile(suffix=".img") as handle:
        handle.write(blob)
        handle.flush()
        try:
            filesystem = PyFatFS(handle.name, read_only=True)
        except Exception:  # noqa: BLE001 - not a FAT image, or damaged
            return []
        try:
            for path in filesystem.walk.files():
                try:
                    found.append((path, filesystem.readbytes(path)))
                except Exception:  # noqa: BLE001, PERF203
                    continue
        finally:
            filesystem.close()
    return found


def case_noise(text: str) -> float:
    """Fraction of words with casing real prose does not produce."""
    words = [w for w in text.split() if sum(1 for c in w if c > "\x7f") > 1]
    if not words:
        return 0.0
    noisy = 0
    for word in words:
        cased = [ch for ch in word if ch.isupper() or ch.islower()]
        if len(cased) < 2:
            continue
        rest = cased[1:]
        mixed = any(c.isupper() for c in rest) and any(c.islower() for c in rest)
        if mixed or (cased[0].islower() and any(c.isupper() for c in rest)):
            noisy += 1
    return noisy / len(words)


def evaluate(data: bytes, codec: str) -> tuple[int, int, int] | None:
    """Return (letters, high bytes, unique bytes) for an accepted candidate."""
    if not MIN_SIZE <= len(data) <= MAX_SIZE or b"\x00" in data:
        return None
    high = sum(1 for b in data if b >= 0x80)
    if high < MIN_HIGH_BYTES or high > len(data) * MAX_HIGH_FRACTION:
        return None
    # Valid UTF-8 means it is a UTF-8 file whatever its extension says.
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return None
    # The byte must be one only this codepage claims, or the file is
    # equally good evidence for a sibling and therefore evidence for none.
    marks = DISTINGUISHING_BYTES.get(codec)
    unique = sum(1 for b in set(data) if marks and b in marks)
    if not unique:
        return None
    try:
        text = data.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None
    # DOS text uses CR/LF and the occasional EOF marker; nothing else in C0.
    if any(ord(ch) < 0x20 and ch not in "\t\n\r\x1a" for ch in text):
        return None
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < MIN_LETTERS or case_noise(text) > MAX_CASE_NOISE:
        return None
    return letters, high, unique


def zip_members(blob: bytes) -> list[tuple[str, bytes]]:
    """Every file inside a zip, as (path, data)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError, ValueError):
        return []
    found: list[tuple[str, bytes]] = []
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            try:
                found.append((info.filename, archive.read(info)))
            except (RuntimeError, zipfile.BadZipFile, OSError):
                continue
    return found


def mine_item(item: str, codec: str, per_item: int) -> list[tuple]:
    try:
        resolved = item_archive_url(item)
        if resolved is None:
            return []
        url, kind = resolved
        blob = http_get(url)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    members = zip_members(blob) if kind == "zip" else image_members(blob)

    found = []
    for name, data in members:
        if len(found) >= per_item:
            break
        if not name.lower().endswith(TEXT_SUFFIXES):
            continue
        metrics = evaluate(data, codec)
        if metrics is not None:
            found.append((item, name, data, *metrics))
    return found


def write_candidates(rows, codec, language, output_dir, chardet_module) -> None:  # noqa: ANN001
    target_dir = output_dir / codec
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    existing = manifest_path.is_file()
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for item, member, data, letters, high, unique in rows:
            digest = hashlib.sha256(data).hexdigest()[:12]
            path = target_dir / f"dos_{digest}.txt"
            path.write_bytes(data)
            detected = ""
            if chardet_module is not None:
                detected = chardet_module.detect(data)["encoding"] or "None"
            writer.writerow([
                path.relative_to(output_dir), item, member, codec, language,
                len(data), letters, high, unique, detected, "strict",
            ])
    print(f"Wrote {len(rows)} candidates to {target_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--codec", default="cp850")
    parser.add_argument("--language", default="de")
    parser.add_argument("--query", default="deutsch OR german")
    parser.add_argument("--items", type=int, default=40)
    parser.add_argument("--per-item", type=int, default=2)
    parser.add_argument("--max-files", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    if arguments.codec not in DISTINGUISHING_BYTES:
        sys.exit(
            f"{arguments.codec} has no distinguishing-byte set, so a candidate "
            f"could not be told from a sibling codepage"
        )

    items = search_items(arguments.query, arguments.items)
    print(f"{len(items)} item(s) to try for {arguments.codec}")
    chardet_module = _load_chardet()

    rows: list[tuple] = []
    seen: set[str] = set()
    for item in items:
        if len(rows) >= arguments.max_files:
            break
        for row in mine_item(item, arguments.codec, arguments.per_item):
            digest = hashlib.sha256(row[2]).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            rows.append(row)
            print(f"  {item}/{row[1][:34]}  unique_bytes={row[5]}")

    if not rows:
        print(f"No {arguments.codec} candidates found.")
        return 1
    write_candidates(
        rows, arguments.codec, arguments.language, arguments.output, chardet_module
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
