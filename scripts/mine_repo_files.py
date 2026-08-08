#!/usr/bin/env python3
"""Mine wild files in a target encoding from a public git repository.

Some encodings are *file* encodings rather than web-page encodings, and no
amount of crawling will produce them.  UTF-8 with a BOM and UTF-16 are the
clearest cases: browsers want a charset header or a BOM-less UTF-8, so the
crawl has almost none, while Windows tooling emits them constantly.  A
30-part Common Crawl scan yielded one utf-8-sig page; a single Microsoft
sample repository holds thousands.

The encoding is read from the bytes -- a byte-order mark is unambiguous
where a charset label is not -- so this needs no metadata and no API key.
Candidates are filtered for enough real text to be worth keeping, deduped
by content, and written with a review manifest.  Promotion stays a
separate step, see scripts/promote_candidates.py.

Pick repositories whose licence allows redistribution; this repo keeps
test files under their publisher's copyright, so note the source when
promoting.

Dependencies: the standard library, plus git on PATH.

Usage::

    python3 scripts/mine_repo_files.py \
        --repo https://github.com/microsoft/Windows-classic-samples \
        --charset utf-8-sig --max-files 8
"""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_OUTPUT = Path("scripts/.cache/wild-repo")

MIN_LETTERS = 150
MIN_SIZE = 256
MAX_SIZE = 64 * 1024

# charset -> (byte-order mark, python codec).  Every entry is identified
# from its BOM, which is what makes this reliable without any metadata.
BOM_CHARSETS: dict[str, tuple[bytes, str]] = {
    "utf-8-sig": (codecs.BOM_UTF8, "utf-8-sig"),
    "utf-16": (codecs.BOM_UTF16_LE, "utf-16"),
    "utf-16-be-bom": (codecs.BOM_UTF16_BE, "utf-16"),
    "utf-32": (codecs.BOM_UTF32_LE, "utf-32"),
}

# BOM-less wide encodings cannot be recognised from a mark, and the web has
# essentially none -- a page without a BOM needs a charset header, so
# authors just use one.  Files are different: Windows .rc resource scripts
# are routinely UTF-16LE with no BOM.  They are identified structurally, by
# ASCII text leaving NULs in every high byte: odd offsets for little-endian,
# even for big-endian.
BOMLESS_CHARSETS = ("utf-16-le", "utf-16-be")


def wide_without_bom(data: bytes, codec: str) -> bool:
    """Whether *data* looks like BOM-less UTF-16 of the given endianness."""
    if len(data) < 64 or len(data) % 2 or data[:2] in (
        codecs.BOM_UTF16_LE,
        codecs.BOM_UTF16_BE,
    ):
        return False
    window = min(len(data), 8192)
    pairs = window // 2
    odd = sum(1 for i in range(1, window, 2) if data[i] == 0)
    even = sum(1 for i in range(0, window, 2) if data[i] == 0)
    if codec == "utf-16-le":
        return odd > pairs * 0.8 and even < pairs * 0.05
    return even > pairs * 0.8 and odd < pairs * 0.05

MANIFEST_COLUMNS = (
    "path", "repo", "member", "size", "letters", "detected", "status",
)


def _load_chardet():  # noqa: ANN202
    try:
        import chardet  # noqa: PLC0415
    except ImportError:
        return None
    return chardet


def clone(repo: str, destination: Path) -> None:
    """Shallow-clone *repo* into *destination*."""
    print(f"Cloning {repo} ...")
    subprocess.run(  # noqa: S603
        ["git", "clone", "--depth", "1", "--quiet", repo, str(destination)],  # noqa: S607
        check=True,
        env={"GIT_LFS_SKIP_SMUDGE": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


def evaluate(data: bytes, bom: bytes | None, codec: str) -> int | None:
    """Return the letter count if *data* is usable text in this encoding."""
    if not MIN_SIZE <= len(data) <= MAX_SIZE:
        return None
    if bom is None:
        if not wide_without_bom(data, codec):
            return None
    elif not data.startswith(bom):
        return None
    try:
        text = data.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < MIN_LETTERS:
        return None
    # Reject control characters that would fail the repo's quality check.
    if any(ord(ch) < 0x20 and ch not in "\t\n\r" for ch in text):
        return None
    return letters


def scan(root: Path, charset: str, limit: int) -> list[tuple]:
    """Walk *root* collecting files that match the target charset."""
    if charset in BOMLESS_CHARSETS:
        bom, codec = None, charset
    else:
        bom, codec = BOM_CHARSETS[charset]
    found: list[tuple] = []
    seen: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        letters = evaluate(data, bom, codec)
        if letters is None:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        found.append((str(path.relative_to(root)), data, letters))
        if len(found) >= limit:
            break
    return found


def write_candidates(
    rows: list[tuple], repo: str, charset: str, output_dir: Path, chardet_module,  # noqa: ANN001
) -> None:
    """Write candidate files and the review manifest."""
    target_dir = output_dir / charset
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    existing = manifest_path.is_file()
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for member, data, letters in rows:
            digest = hashlib.sha256(data).hexdigest()[:12]
            path = target_dir / f"repo_{digest}.txt"
            path.write_bytes(data)
            detected = ""
            if chardet_module is not None:
                detected = chardet_module.detect(data)["encoding"] or "None"
            writer.writerow([
                path.relative_to(output_dir), repo, member,
                len(data), letters, detected, "strict",
            ])
    print(f"Wrote {len(rows)} candidates to {target_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", required=True, help="git URL to clone")
    parser.add_argument(
        "--charset",
        default="utf-8-sig",
        choices=sorted({*BOM_CHARSETS, *BOMLESS_CHARSETS}),
    )
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    chardet_module = _load_chardet()
    workdir = Path(tempfile.mkdtemp(prefix="repo-mine-"))
    try:
        checkout = workdir / "repo"
        try:
            clone(arguments.repo, checkout)
        except subprocess.CalledProcessError as error:
            sys.exit(f"clone failed: {error}")
        rows = scan(checkout, arguments.charset, arguments.max_files)
        print(f"{len(rows)} candidate(s) matching {arguments.charset}")
        if not rows:
            return 1
        write_candidates(
            rows, arguments.repo, arguments.charset, arguments.output, chardet_module
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
