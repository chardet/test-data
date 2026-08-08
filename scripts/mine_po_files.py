#!/usr/bin/env python3
"""Mine wild legacy-encoded text from gettext PO catalogues.

PO files are unusually good evidence.  Every catalogue states its own
encoding in a ``Content-Type: text/plain; charset=...`` header, so the
label travels with the bytes instead of being asserted by a web server
that may be misconfigured -- the failure mode that makes most crawl
candidates worthless.  They are also real prose written by translators,
in exactly the legacy encodings that vanished from the web: KOI8-R and
KOI8-T, EUC-KR, Big5, the ISO-8859 family, the Windows codepages.

A declared charset is still only a claim, so each catalogue must decode
strictly under it and produce enough characters in that encoding's home
script to rule out a mislabelled file.

Dependencies: the standard library, plus git on PATH.

Usage::

    python3 scripts/mine_po_files.py --repo https://github.com/vim/vim
    python3 scripts/mine_po_files.py --repo <url> --max-per-charset 4
"""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_OUTPUT = Path("scripts/.cache/wild-po")

MIN_SIZE = 512
# Catalogues for CJK and Cyrillic locales routinely pass 400 KB, and a cap
# below that quietly excluded exactly the encodings worth having.
MAX_SIZE = 900 * 1024
MIN_SCRIPT_CHARS = 60

_CHARSET = re.compile(rb"charset=([\w.:-]+)", re.IGNORECASE)

# Home script per encoding family, used to reject a mislabelled catalogue.
# Encodings absent here have no single expected script and are accepted on
# a strict decode alone.
SCRIPT_RANGES: dict[str, tuple[tuple[str, str], ...]] = {
    "cyrillic": (("Ѐ", "ӿ"),),
    "greek": (("Ͱ", "Ͽ"), ("ἀ", "῿")),
    "hebrew": (("֐", "׿"),),
    "arabic": (("؀", "ۿ"),),
    "thai": (("฀", "๿"),),
    "cjk": (("一", "鿿"), ("぀", "ヿ"), ("가", "힣")),
}
ENCODING_SCRIPT: dict[str, str] = {
    "koi8-r": "cyrillic", "koi8-u": "cyrillic", "koi8-t": "cyrillic",
    "cp1251": "cyrillic", "iso8859-5": "cyrillic", "cp866": "cyrillic",
    "mac-cyrillic": "cyrillic", "cp1125": "cyrillic", "ptcp154": "cyrillic",
    "kz1048": "cyrillic",
    "cp1253": "greek", "iso8859-7": "greek",
    "cp1255": "hebrew", "iso8859-8": "hebrew",
    "cp1256": "arabic", "iso8859-6": "arabic",
    "tis-620": "thai", "cp874": "thai",
    "big5": "cjk", "big5hkscs": "cjk", "gb2312": "cjk", "gbk": "cjk",
    "gb18030": "cjk", "euc_kr": "cjk", "cp949": "cjk", "euc_jp": "cjk",
    "shift_jis": "cjk", "cp932": "cjk",
}

MANIFEST_COLUMNS = (
    "path", "repo", "member", "charset", "codec", "language", "size",
    "script_chars", "detected", "status",
)


def locale_language(member: str) -> str:
    """ISO 639-1 code from a catalogue's filename.

    PO files are named for their locale -- ``ru.cp1251.po``, ``zh_CN.po``,
    ``pt_BR.po`` -- which is a far better language signal than guessing
    from content.
    """
    stem = Path(member).name.split(".")[0]
    return stem.split("_")[0].lower()


def _load_chardet():  # noqa: ANN202
    try:
        import chardet  # noqa: PLC0415
    except ImportError:
        return None
    return chardet


def canonical(name: str) -> str | None:
    try:
        return codecs.lookup(name).name
    except (LookupError, ValueError):
        return None


def clone(repo: str, destination: Path) -> None:
    print(f"Cloning {repo} ...")
    subprocess.run(  # noqa: S603
        ["git", "clone", "--depth", "1", "--quiet", repo, str(destination)],  # noqa: S607
        check=True,
        env={"GIT_LFS_SKIP_SMUDGE": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


def script_chars(codec: str, text: str) -> int | None:
    """Characters in the codec's home script, or None if it has no single one."""
    script = ENCODING_SCRIPT.get(codec)
    if script is None:
        return None
    ranges = SCRIPT_RANGES[script]
    return sum(1 for ch in text if any(lo <= ch <= hi for lo, hi in ranges))


def evaluate(data: bytes) -> tuple[str, str, int] | None:
    """Return (declared charset, codec, script chars) for a usable catalogue."""
    if not MIN_SIZE <= len(data) <= MAX_SIZE:
        return None
    match = _CHARSET.search(data[:4000])
    if not match:
        return None
    declared = match.group(1).decode("latin-1").lower()
    if declared in {"charset", "utf-8", "us-ascii", "ascii"}:
        return None
    codec = canonical(declared)
    if codec is None:
        return None
    try:
        text = data.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None
    if "\x00" in text:
        return None
    counted = script_chars(codec, text)
    if counted is None:
        # No single expected script (the Latin codepages); require enough
        # non-ASCII to show the encoding is actually exercised.
        counted = sum(1 for ch in text if ch > "\x7f")
    if counted < MIN_SCRIPT_CHARS:
        return None
    return declared, codec, counted


def scan(root: Path, per_charset: int) -> list[tuple]:
    kept: dict[str, int] = defaultdict(int)
    seen: set[str] = set()
    rows: list[tuple] = []
    for path in sorted(root.rglob("*.po")):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        result = evaluate(data)
        if result is None:
            continue
        declared, codec, counted = result
        if kept[codec] >= per_charset:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        kept[codec] += 1
        rows.append((str(path.relative_to(root)), data, declared, codec, counted))
    return rows


def write_candidates(rows: list[tuple], repo: str, output_dir: Path, chardet_module) -> None:  # noqa: ANN001
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    existing = manifest_path.is_file()
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for member, data, declared, codec, counted in rows:
            target_dir = output_dir / codec
            target_dir.mkdir(exist_ok=True)
            digest = hashlib.sha256(data).hexdigest()[:12]
            path = target_dir / f"po_{digest}.po"
            path.write_bytes(data)
            detected = ""
            if chardet_module is not None:
                detected = chardet_module.detect(data)["encoding"] or "None"
            writer.writerow([
                path.relative_to(output_dir), repo, member, declared, codec,
                locale_language(member), len(data), counted, detected, "strict",
            ])
    print(f"Wrote {len(rows)} candidates to {output_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--max-per-charset", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    chardet_module = _load_chardet()
    workdir = Path(tempfile.mkdtemp(prefix="po-mine-"))
    try:
        checkout = workdir / "repo"
        try:
            clone(arguments.repo, checkout)
        except subprocess.CalledProcessError as error:
            sys.exit(f"clone failed: {error}")
        rows = scan(checkout, arguments.max_per_charset)
        by_codec: dict[str, int] = defaultdict(int)
        for row in rows:
            by_codec[row[3]] += 1
        print(f"{len(rows)} catalogue(s) across {len(by_codec)} encodings")
        for codec, count in sorted(by_codec.items()):
            print(f"  {codec:<16} {count}")
        if not rows:
            return 1
        write_candidates(rows, arguments.repo, arguments.output, chardet_module)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
