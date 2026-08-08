#!/usr/bin/env python3
"""Mine wild CJK-encoded posts from Usenet mbox archives.

Several encodings this repo needs samples of were built for Usenet and
email and barely existed anywhere else, so no amount of web crawling will
find them.  HZ (RFC 1843) was designed for ``alt.chinese.text`` because the
links of the day could not carry 8-bit bytes; ISO-2022-JP and ISO-2022-KR
were the mail and news encodings for the ``japan.*`` and ``han.*``
hierarchies.  The Internet Archive preserves both the per-group Giganews
spool (``usenet-alt.chinese``) and a fuller backup with one archive per
group (``FULL-USENET-BACKUP-2020-Oct-<group>.<n>.mbox.7z``).

Message bodies are matched against a target charset, validated, filtered
for real CJK content, and written as candidates with a review manifest.
Promotion into the tree stays a separate, manual step -- see
scripts/promote_candidates.py.

Validation notes per charset:

``hz``
    Wild HZ routinely violates RFC 1843 in the plain-ASCII stretches
    (stray bare ``~``), which Python's strict codec rejects even when every
    Chinese span is well-formed.  Each ``~{...~}`` span is validated
    separately and a minority of bad spans tolerated; that tolerance is the
    difference between finding eight wild HZ posts and finding none.
``iso-2022-jp`` / ``iso-2022-kr``
    Required to carry the charset's own escape sequence, so a plain-ASCII
    post in a Japanese group is not mistaken for one.
``big5`` / ``euc-kr``
    Byte-oriented, so they need a strict decode plus enough characters in
    the expected script to rule out a Latin page in a CJK newsgroup.

Dependencies: the standard library, plus ``py7zr`` for the .7z archives
(the .gz spools need nothing).  chardet is optional; without it the
``detected`` column stays empty.

Usage::

    python3 scripts/mine_usenet.py --charset hz
    uv run --with py7zr python3 scripts/mine_usenet.py --charset iso-2022-jp \
        --item FULL-USENET-BACKUP-2020-Oct-japan.soc.cult.121.mbox.7z
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import mailbox
import re
import shutil
import sys
import urllib.request
from pathlib import Path

DEFAULT_OUTPUT = Path("scripts/.cache/wild-usenet")
DEFAULT_CACHE = Path("scripts/.cache/usenet")

MIN_SCRIPT_CHARS = 40
MAX_BODY_BYTES = 64 * 1024

# Some genuine HZ posts (notably the alt.chinese.text FAQ, which quotes
# deliberately malformed examples) contain a few spans that do not decode.
# Tolerate a minority; a majority means line noise, not sloppy framing.
MAX_BAD_SPAN_FRACTION = 0.25

_HZ_SPAN = re.compile(rb"~\{(.*?)~\}", re.DOTALL)

# charset -> (python codec, required byte marker or None, script ranges)
CHARSETS: dict[str, tuple[str, bytes | None, tuple[tuple[str, str], ...]]] = {
    "hz": ("hz", b"~{", (("一", "鿿"),)),
    "iso-2022-jp": ("iso2022_jp", b"\x1b$", (("一", "鿿"), ("぀", "ヿ"))),
    "iso-2022-kr": ("iso2022_kr", b"\x1b$)C", (("가", "힣"),)),
    "big5": ("big5", None, (("一", "鿿"),)),
    "euc-kr": ("euc_kr", None, (("가", "힣"),)),
}

# Default source per charset: the group hierarchy that actually used it.
DEFAULT_SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "hz": (
        "usenet-alt.chinese",
        ("alt.chinese.text.hz.20140404.mbox.gz", "alt.chinese.text.20140612.mbox.gz"),
    ),
}

MANIFEST_COLUMNS = (
    "path", "message_id", "subject", "size", "script_chars", "detected", "status",
)


def list_item_files(item: str) -> tuple[str, ...]:
    """Names of the mbox archives inside an archive.org item.

    Filenames cannot be derived from the identifier: the item
    ``FULL-USENET-BACKUP-2020-Oct-japan.soc.cult.121.mbox.7z`` actually
    holds ``japan.soc.cult.(121).mbox.7z``, parentheses and all.
    """
    request = urllib.request.Request(  # noqa: S310
        f"https://archive.org/metadata/{item}",
        headers={"User-Agent": "chardet-test-data-miner/0.1"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        payload = json.load(response)
    return tuple(
        entry["name"]
        for entry in payload.get("files", [])
        if entry["name"].endswith((".mbox.7z", ".mbox.gz"))
        and not entry["name"].startswith("history/")
    )


def _load_chardet():  # noqa: ANN202
    try:
        import chardet  # noqa: PLC0415
    except ImportError:
        return None
    return chardet


def script_char_count(text: str, ranges: tuple[tuple[str, str], ...]) -> int:
    """Count characters falling in any of the expected script ranges."""
    return sum(1 for ch in text if any(low <= ch <= high for low, high in ranges))


def download(item: str, filename: str, cache_dir: Path) -> Path:
    """Download an archive.org file, decompressing .gz and .7z to an mbox."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / filename.removesuffix(".gz").removesuffix(".7z")
    if target.exists():
        print(f"Using cached {target}")
        return target

    url = f"https://archive.org/download/{item}/{filename}"
    print(f"Downloading {url} ...")
    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "chardet-test-data-miner/0.1"}
    )
    partial = target.with_suffix(target.suffix + ".partial")

    if filename.endswith(".7z"):
        try:
            import py7zr  # noqa: PLC0415
        except ImportError:
            sys.exit(
                "py7zr is required for .7z archives.\n"
                "  uv run --with py7zr python3 scripts/mine_usenet.py ..."
            )
        # Extract into a directory of its own.  Globbing the shared cache
        # would pick up whichever mbox an earlier group left behind, and
        # silently mine the wrong newsgroup.
        extract_dir = cache_dir / filename.removesuffix(".7z")
        if extract_dir.is_dir():
            existing = sorted(extract_dir.glob("*.mbox"))
            if existing:
                print(f"Using cached {existing[0]}")
                return existing[0]
        extract_dir.mkdir(parents=True, exist_ok=True)
        archive_path = cache_dir / filename
        with (
            urllib.request.urlopen(request, timeout=600) as response,  # noqa: S310
            archive_path.open("wb") as out,
        ):
            shutil.copyfileobj(response, out)
        with py7zr.SevenZipFile(archive_path, "r") as archive:
            archive.extractall(path=extract_dir)
        archive_path.unlink()
        found = sorted(extract_dir.rglob("*.mbox"))
        if not found:
            sys.exit(f"no mbox found inside {filename}")
        return found[0]

    with (
        urllib.request.urlopen(request, timeout=600) as response,  # noqa: S310
        gzip.GzipFile(fileobj=response) as stream,
        partial.open("wb") as out,
    ):
        # Stream-decompress to disk: group mboxes reach hundreds of MB.
        shutil.copyfileobj(stream, out)
    partial.rename(target)
    print(f"Saved {target} ({target.stat().st_size:,} bytes)")
    return target


def analyze_hz_spans(payload: bytes) -> tuple[int, int, int, bytes]:
    """Return (cjk_count, good_spans, bad_spans, span_content) for a payload.

    The concatenated span content is returned for content-level dedup:
    reposts of the same document differ in their headers, not their spans.
    """
    total = good = bad = 0
    spans: list[bytes] = []
    for match in _HZ_SPAN.finditer(payload):
        spans.append(match.group(1))
        try:
            decoded = (b"~{" + match.group(1) + b"~}").decode("hz")
        except (UnicodeDecodeError, ValueError):
            bad += 1
            continue
        good += 1
        total += script_char_count(decoded, CHARSETS["hz"][2])
    return total, good, bad, b"".join(spans)


def evaluate(payload: bytes, charset: str) -> tuple[int, str] | None:
    """Return (script_chars, status) if the payload is valid for *charset*."""
    codec, marker, ranges = CHARSETS[charset]
    if marker and marker not in payload:
        return None

    if charset == "hz":
        cjk, good, bad, _ = analyze_hz_spans(payload)
        try:
            return script_char_count(payload.decode("hz"), ranges), "strict"
        except (UnicodeDecodeError, ValueError):
            if good and bad <= (good + bad) * MAX_BAD_SPAN_FRACTION:
                return cjk, "spans-only"
            return None

    try:
        decoded = payload.decode(codec)
    except (UnicodeDecodeError, ValueError):
        return None
    return script_char_count(decoded, ranges), "strict"


def extract_candidates(
    mbox_path: Path, charset: str, max_files: int, seen: set[str], chardet_module
) -> list[tuple[str, str, bytes, str, int, str]]:  # noqa: ANN001
    """Return (message_id, subject, body, detected, script_chars, status)."""
    candidates: list[tuple[str, str, bytes, str, int, str]] = []
    box = mailbox.mbox(str(mbox_path))
    scanned = 0
    for message in box:
        scanned += 1
        if message.is_multipart():
            continue
        payload = message.get_payload(decode=True)
        if not payload or len(payload) > MAX_BODY_BYTES:
            continue

        result = evaluate(payload, charset)
        if result is None:
            continue
        script_chars, status = result
        if script_chars < MIN_SCRIPT_CHARS:
            continue

        if charset == "hz":
            key = hashlib.sha256(analyze_hz_spans(payload)[3]).hexdigest()
        else:
            key = hashlib.sha256(payload).hexdigest()
        if key in seen:
            continue
        seen.add(key)

        detected = ""
        if chardet_module is not None:
            detected = chardet_module.detect(payload)["encoding"] or "None"
        candidates.append((
            str(message.get("Message-ID", f"<no-id-{scanned}>")),
            str(message.get("Subject", ""))[:60],
            payload,
            detected,
            script_chars,
            status,
        ))
        if len(candidates) >= max_files:
            break

    print(f"  scanned {scanned} messages, kept {len(candidates)}")
    return candidates


def write_candidates(candidates, charset: str, output_dir: Path) -> None:  # noqa: ANN001
    """Write candidate bodies and the review manifest."""
    target_dir = output_dir / charset
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    existing = manifest_path.is_file()
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for message_id, subject, body, detected, chars, status in candidates:
            digest = hashlib.sha256(body).hexdigest()[:12]
            path = target_dir / f"usenet_{digest}.txt"
            path.write_bytes(body)
            writer.writerow([
                path.relative_to(output_dir), message_id, subject,
                len(body), chars, detected, status,
            ])
    print(f"Wrote {len(candidates)} candidates to {target_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--charset", default="hz", choices=sorted(CHARSETS))
    parser.add_argument("--item", default="", help="archive.org item identifier")
    parser.add_argument("--mbox", nargs="*", default=[], help="files within the item")
    parser.add_argument("--max-files", type=int, default=25)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    arguments = parser.parse_args()

    item, mboxes = arguments.item, tuple(arguments.mbox)
    if not item:
        if arguments.charset not in DEFAULT_SOURCES:
            sys.exit(
                f"no default source for {arguments.charset}; pass --item "
                f"(e.g. a FULL-USENET-BACKUP-2020-Oct-<group>.N.mbox.7z identifier)"
            )
        item, mboxes = DEFAULT_SOURCES[arguments.charset]
    if not mboxes:
        mboxes = list_item_files(item)
        if not mboxes:
            sys.exit(f"no mbox archives found in item {item}")
        print(f"{item}: {', '.join(mboxes)}")

    chardet_module = _load_chardet()
    if chardet_module is None:
        print("note: chardet not importable -- the detected column will be empty.\n")

    candidates: list = []
    seen: set[str] = set()
    for name in mboxes:
        remaining = arguments.max_files - len(candidates)
        if remaining <= 0:
            break
        path = download(item, name, arguments.cache_dir)
        candidates.extend(
            extract_candidates(path, arguments.charset, remaining, seen, chardet_module)
        )

    if not candidates:
        print(f"No {arguments.charset} candidates found.")
        return 1

    if chardet_module is not None:
        counts: dict[str, int] = {}
        for candidate in candidates:
            counts[candidate[3]] = counts.get(candidate[3], 0) + 1
        print("Detector verdicts:")
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {name:<16} {count}")

    write_candidates(candidates, arguments.charset, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
