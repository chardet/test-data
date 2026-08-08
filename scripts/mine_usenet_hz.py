#!/usr/bin/env python3
"""Mine HZ-GB-2312 encoded posts from Usenet mbox archives.

HZ (RFC 1843) was the 7-bit encoding of GB2312 used on the
``alt.chinese.text`` newsgroup hierarchy in the early 1990s -- one of the
very few places wild HZ data ever existed, since it was designed for
Usenet and email links that could not carry 8-bit bytes.  The Internet
Archive's Usenet collection preserves per-group mbox files (Giganews
spool), including ``alt.chinese.text.hz``, which carried HZ traffic by
charter.

This script downloads a group's mbox, extracts message bodies containing
HZ shift sequences (``~{`` ... ``~}``), validates them, filters for real
CJK content, and writes candidates plus a review manifest.  Promotion of
vetted candidates into ``hz-zh/`` stays a manual step.

Validation note: wild HZ routinely violates RFC 1843 in the plain-ASCII
stretches (stray bare ``~`` bytes), which Python's strict ``hz`` codec
rejects even when every Chinese span is well-formed.  Each ``~{...~}``
span is therefore validated independently, and a message is kept when
the overwhelming majority of its spans decode -- that tolerance is the
difference between finding 8 wild HZ posts and finding none.

Dependencies: the standard library.  chardet is optional; without it the
``detected`` column stays empty.

Usage::

    python3 scripts/mine_usenet_hz.py
    python3 scripts/mine_usenet_hz.py --mbox alt.chinese.text.hz.20140404.mbox.gz
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import mailbox
import re
import shutil
import urllib.request
from pathlib import Path

ARCHIVE_ITEM = "usenet-alt.chinese"
DEFAULT_MBOXES = (
    "alt.chinese.text.hz.20140404.mbox.gz",
    "alt.chinese.text.20140612.mbox.gz",
)
DEFAULT_OUTPUT = Path("scripts/.cache/wild-hz")
DEFAULT_CACHE = Path("scripts/.cache/usenet")

MIN_CJK_CHARS = 40
MAX_BODY_BYTES = 64 * 1024

# Some genuine HZ posts (notably the alt.chinese.text FAQ, which quotes
# deliberately malformed examples) contain a few spans that do not decode.
# Tolerate a minority; a majority means line noise, not sloppy framing.
MAX_BAD_SPAN_FRACTION = 0.25

_HZ_SPAN = re.compile(rb"~\{(.*?)~\}", re.DOTALL)

MANIFEST_COLUMNS = (
    "path",
    "message_id",
    "subject",
    "size",
    "cjk_chars",
    "detected",
    "status",
)


def _load_chardet():  # noqa: ANN202
    """Import chardet if available; None otherwise (it stays optional)."""
    try:
        import chardet  # noqa: PLC0415
    except ImportError:
        return None
    return chardet


def download_mbox(item: str, filename: str, cache_dir: Path) -> Path:
    """Download and decompress an archive.org mbox, with a local cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / filename.removesuffix(".gz")
    if target.exists():
        print(f"Using cached {target}")
        return target

    url = f"https://archive.org/download/{item}/{filename}"
    print(f"Downloading {url} ...")
    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "chardet-test-data-miner/0.1"}
    )
    partial = target.with_suffix(target.suffix + ".partial")
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


def cjk_char_count(text: str) -> int:
    """Count CJK unified ideographs in *text*."""
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def analyze_hz_spans(payload: bytes) -> tuple[int, int, int, bytes]:
    """Return (cjk_count, good_spans, bad_spans, span_content) for a payload.

    The concatenated span content is returned for content-level dedup:
    reposts of the same document differ in their headers, not their
    Chinese spans.
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
        total += cjk_char_count(decoded)
    return total, good, bad, b"".join(spans)


def extract_candidates(
    mbox_path: Path, max_files: int, seen: set[str], chardet_module
) -> list[tuple[str, str, bytes, str, int, str]]:  # noqa: ANN001
    """Return (message_id, subject, body, detected, cjk, status) candidates."""
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
        if b"~{" not in payload or b"~}" not in payload:
            continue

        cjk, good_spans, bad_spans, span_content = analyze_hz_spans(payload)
        span_digest = hashlib.sha256(span_content).hexdigest()
        if span_digest in seen:
            continue
        seen.add(span_digest)

        try:
            cjk = cjk_char_count(payload.decode("hz"))
            status = "strict"
        except (UnicodeDecodeError, ValueError):
            status = "spans-only"
            total_spans = good_spans + bad_spans
            if good_spans == 0 or bad_spans > total_spans * MAX_BAD_SPAN_FRACTION:
                continue
        if cjk < MIN_CJK_CHARS:
            continue

        detected = ""
        if chardet_module is not None:
            detected = chardet_module.detect(payload)["encoding"] or "None"
        message_id = str(message.get("Message-ID", f"<no-id-{scanned}>"))
        subject = str(message.get("Subject", ""))[:60]
        candidates.append((message_id, subject, payload, detected, cjk, status))
        if len(candidates) >= max_files:
            break

    strict = sum(1 for candidate in candidates if candidate[5] == "strict")
    print(
        f"Scanned {scanned} messages, kept {len(candidates)} HZ candidates "
        f"({strict} strict, {len(candidates) - strict} spans-only)"
    )
    return candidates


def write_candidates(
    candidates: list[tuple[str, str, bytes, str, int, str]], output_dir: Path
) -> None:
    """Write candidate bodies and the review manifest."""
    target_dir = output_dir / "hz"
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.writer(manifest_file)
        writer.writerow(MANIFEST_COLUMNS)
        for message_id, subject, body, detected, cjk, status in candidates:
            digest = hashlib.sha256(body).hexdigest()[:12]
            path = target_dir / f"usenet_{digest}.txt"
            path.write_bytes(body)
            writer.writerow(
                [
                    path.relative_to(output_dir),
                    message_id,
                    subject,
                    len(body),
                    cjk,
                    detected,
                    status,
                ]
            )
    print(f"Wrote {len(candidates)} candidates and manifest to {output_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine HZ-GB-2312 posts from Usenet mbox archives.",
    )
    parser.add_argument("--item", default=ARCHIVE_ITEM, help="archive.org item id")
    parser.add_argument(
        "--mbox",
        nargs="*",
        default=list(DEFAULT_MBOXES),
        help="mbox filenames within the item",
    )
    parser.add_argument("--max-files", type=int, default=25)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"candidate directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help=f"mbox download cache (default: {DEFAULT_CACHE})",
    )
    arguments = parser.parse_args()

    chardet_module = _load_chardet()
    if chardet_module is None:
        print("note: chardet not importable -- the detected column will be empty.\n")

    candidates: list[tuple[str, str, bytes, str, int, str]] = []
    seen: set[str] = set()
    for mbox_name in arguments.mbox:
        remaining = arguments.max_files - len(candidates)
        if remaining <= 0:
            break
        mbox_path = download_mbox(arguments.item, mbox_name, arguments.cache_dir)
        candidates.extend(
            extract_candidates(mbox_path, remaining, seen, chardet_module)
        )

    if not candidates:
        print("No HZ candidates found.")
        return 1

    if chardet_module is not None:
        detections: dict[str, int] = {}
        for candidate in candidates:
            detections[candidate[3]] = detections.get(candidate[3], 0) + 1
        print("Detector verdicts on the HZ bodies:")
        for name, count in sorted(detections.items(), key=lambda kv: -kv[1]):
            print(f"  {name:<16} {count}")

    write_candidates(candidates, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
