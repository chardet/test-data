#!/usr/bin/env python3
"""Mine wild CP437 text from BBS-era ANSI artpacks.

The DOS codepages are the one family Common Crawl cannot help with: a
30-part scan targeting ibm850/852/855/866 and the Mac pages returned 67
candidates and zero usable files, because in 2019 those labels are almost
always wrong -- sites declaring ibm855 while serving GB18030, or
x-maccyrillic while serving windows-1251.  The real CP437 corpus is the
BBS artscene, preserved as artpacks at 16colo.rs.

Not every file in a pack is useful.  A .ANS is mostly box-drawing and
colour escapes with barely any language in it, which teaches an encoding
detector nothing.  The .NFO and FILE_ID.DIZ files are the prize: English
prose framed in CP437 box-drawing, so they carry both real text and the
high bytes that prove the codepage.

Candidates are validated for enough letters to be text, enough high bytes
to be more than ASCII, and a plausible share of CP437's box-drawing and
accented range.  Promotion stays a separate step -- see
scripts/promote_candidates.py.

Dependencies: the standard library only.

Usage::

    python3 scripts/mine_artpacks.py --year 1996 --packs 6
    python3 scripts/mine_artpacks.py --pack 0196ciph
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_OUTPUT = Path("scripts/.cache/wild-artpacks")
BASE = "https://16colo.rs"

# Files worth reading: prose, not raster-ish ANSI canvases.
TEXT_SUFFIXES = (".nfo", ".diz", ".txt", ".asc", ".me", ".doc")

MIN_LETTERS = 200  # ASCII letters: enough to be language, not just art
MIN_HIGH_BYTES = 30  # high bytes: enough to prove it is not plain ASCII
MAX_BYTES = 64 * 1024
MIN_HIGH_FRACTION = 0.01
MAX_HIGH_FRACTION = 0.60  # beyond this it is artwork, not text

MANIFEST_COLUMNS = (
    "path", "pack", "member", "size", "letters", "high_bytes",
    "high_fraction", "detected", "status",
)

_PACK_LINK = re.compile(r'href="/pack/([A-Za-z0-9._-]+)/?"')


def _load_chardet():  # noqa: ANN202
    try:
        import chardet  # noqa: PLC0415
    except ImportError:
        return None
    return chardet


def http_get(url: str) -> bytes:
    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "chardet-test-data-miner/0.1"}
    )
    with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
        return response.read()


def packs_for_year(year: int) -> list[str]:
    """Pack names released in a given year."""
    html = http_get(f"{BASE}/year/{year}").decode("latin-1")
    seen, ordered = set(), []
    for name in _PACK_LINK.findall(html):
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def pack_zip(pack: str, year: int) -> bytes | None:
    """Fetch a pack's zip, or None if the archive path does not resolve."""
    try:
        return http_get(f"{BASE}/archive/{year}/{pack}.zip")
    except (urllib.error.URLError, OSError) as error:
        print(f"  {pack}: {error}")
        return None


def evaluate(data: bytes) -> tuple[int, int, float] | None:
    """Return (letters, high_bytes, high_fraction) if this looks like text."""
    if not data or len(data) > MAX_BYTES:
        return None
    letters = sum(1 for b in data if 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A)
    high = sum(1 for b in data if b >= 0x80)
    if letters < MIN_LETTERS or high < MIN_HIGH_BYTES:
        return None
    fraction = high / len(data)
    if not MIN_HIGH_FRACTION <= fraction <= MAX_HIGH_FRACTION:
        return None
    # CP437 maps every byte, so decoding always succeeds; the check that
    # matters is control characters.  A .NFO often carries raw ANSI colour
    # escapes (0x1B) and stray NULs, which make it a terminal recording
    # rather than text -- the repo's own quality check rejects those, so
    # reject them here instead of promoting a file that cannot pass.
    if b"\x00" in data:
        return None
    controls = sum(1 for b in data if b < 0x20 and b not in (0x09, 0x0A, 0x0D))
    if controls:
        return None
    return letters, high, fraction


def mine_pack(pack: str, year: int, limit: int) -> list[tuple]:
    """Extract validated CP437 text members from one pack."""
    blob = pack_zip(pack, year)
    if blob is None:
        return []
    results = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        print(f"  {pack}: not a zip")
        return []
    with archive:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.lower().endswith(TEXT_SUFFIXES):
                continue
            try:
                data = archive.read(info)
            except (RuntimeError, zipfile.BadZipFile, OSError):
                continue
            metrics = evaluate(data)
            if metrics is None:
                continue
            results.append((pack, info.filename, data, *metrics))
            if len(results) >= limit:
                break
    return results


def write_candidates(rows: list[tuple], output_dir: Path, chardet_module) -> None:  # noqa: ANN001
    """Write candidate files and the review manifest."""
    target_dir = output_dir / "cp437"
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    existing = manifest_path.is_file()
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for pack, member, data, letters, high, fraction in rows:
            import hashlib  # noqa: PLC0415

            digest = hashlib.sha256(data).hexdigest()[:12]
            path = target_dir / f"artpack_{pack}_{digest}.txt"
            path.write_bytes(data)
            detected = ""
            if chardet_module is not None:
                detected = chardet_module.detect(data)["encoding"] or "None"
            writer.writerow([
                path.relative_to(output_dir), pack, member, len(data),
                letters, high, f"{fraction:.3f}", detected, "strict",
            ])
    print(f"Wrote {len(rows)} candidates to {target_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--year", type=int, default=1996)
    parser.add_argument("--pack", default="", help="a single pack name")
    parser.add_argument("--packs", type=int, default=8, help="packs to try")
    parser.add_argument("--per-pack", type=int, default=2)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    if arguments.pack:
        names = [arguments.pack]
    else:
        names = packs_for_year(arguments.year)[: arguments.packs]
        if not names:
            sys.exit(f"no packs listed for {arguments.year}")
    print(f"{len(names)} pack(s) from {arguments.year}")

    chardet_module = _load_chardet()
    rows: list[tuple] = []
    seen: set[bytes] = set()
    for pack in names:
        found = mine_pack(pack, arguments.year, arguments.per_pack)
        for row in found:
            if row[2] in seen:
                continue
            seen.add(row[2])
            rows.append(row)
        print(f"  {pack}: {len(found)} candidate(s)")

    if not rows:
        print("No CP437 candidates found.")
        return 1
    write_candidates(rows, arguments.output, chardet_module)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
