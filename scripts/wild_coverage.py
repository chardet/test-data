#!/usr/bin/env python3
"""Report how many wild (non-transcoded) files each encoding has.

The repo's stated goal is 5-10 genuinely wild samples per encoding -- bytes
that were really served or stored in that encoding, not CulturaX text
transcoded into it.  This prints the shortfall per encoding and groups the
remainder by how it could plausibly be sourced, because the answer differs
sharply: Common Crawl can supply a Cyrillic web codepage all day and will
never supply EBCDIC.

Usage::

    python3 scripts/wild_coverage.py              # table + shortfall
    python3 scripts/wild_coverage.py --targets    # just the encodings to mine
"""

from __future__ import annotations

import argparse
import codecs
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from encoding_gaps import get_codec  # noqa: E402
from provenance import HISTORIC, WILD, classify_all, data_files  # noqa: E402

TARGET = 5

# How a given encoding family could plausibly yield wild bytes.
WEB = "web"  # Common Crawl: served over HTTP in living memory
USENET = "usenet"  # Usenet/mailing-list archives (escape-based, 7-bit era)
ARCHIVE = "archive"  # software/BBS archives: DOS, Mac, HP text files
NONE = "no-known-source"  # EBCDIC and friends: not published as files

SOURCE_CLASS: dict[str, str] = {}
for _codec in (
    "cp1251", "cp1250", "cp1252", "cp1253", "cp1254", "cp1255", "cp1256",
    "cp1257", "cp1258", "iso8859-1", "iso8859-2", "iso8859-3", "iso8859-4",
    "iso8859-5", "iso8859-6", "iso8859-7", "iso8859-8", "iso8859-9",
    "iso8859-13", "iso8859-15", "iso8859-16", "koi8-r", "koi8-u", "tis-620",
    "cp874", "big5", "big5hkscs", "gb2312", "gb18030", "euc_jp", "euc_kr",
    "shift_jis", "cp932", "cp949", "utf-8", "utf-8-sig", "utf-16", "utf-16-be",
    "utf-16-le", "utf-32", "utf-32-be", "utf-32-le", "mac-cyrillic", "cp866",
    "ascii", "euc_jis_2004", "shift_jis_2004",
):
    SOURCE_CLASS[_codec] = WEB
for _codec in ("hz", "iso2022_jp", "iso2022_jp_2", "iso2022_jp_2004",
               "iso2022_jp_ext", "iso2022_kr", "utf-7", "johab"):
    SOURCE_CLASS[_codec] = USENET
for _codec in ("cp437", "cp850", "cp852", "cp855", "cp857", "cp858", "cp860",
               "cp861", "cp862", "cp863", "cp864", "cp865", "cp869", "cp737",
               "cp775", "cp720", "cp1125", "mac-roman", "mac-latin2",
               "mac-greek", "mac-iceland", "mac-turkish", "hp-roman8",
               "koi8-t", "kz1048", "ptcp154", "iso8859-10", "iso8859-14"):
    SOURCE_CLASS[_codec] = ARCHIVE
for _codec in ("cp037", "cp273", "cp424", "cp500", "cp875", "cp1026",
               "cp1006", "cp1140", "cp856"):
    SOURCE_CLASS[_codec] = NONE


def dir_codec(dirname: str) -> str | None:
    if dirname.startswith("None"):
        return None
    parts = dirname.split("-")
    for i in range(len(parts), 0, -1):
        try:
            return codecs.lookup(get_codec("-".join(parts[:i]))).name
        except LookupError:
            continue
    return None


# Historic transcodes count toward coverage: for encodings whose own
# corpus survives only in a codepage Python cannot name, period text
# re-encoded into the supported sibling is the closest thing to a real
# sample that can exist, and is far better evidence than modern web text.
COUNTED = (WILD, HISTORIC)


def wild_counts() -> dict[str, int]:
    """Countable file count per codec, including encodings with zero."""
    verdicts = classify_all()
    counts: dict[str, int] = defaultdict(int)
    for path in data_files():
        codec = dir_codec(path.partition("/")[0])
        if codec is None:
            continue
        counts[codec] += 1 if verdicts.get(path) in COUNTED else 0
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--target", type=int, default=TARGET)
    parser.add_argument(
        "--targets",
        action="store_true",
        help="print only the web-mineable encodings still short",
    )
    arguments = parser.parse_args()

    counts = wild_counts()
    short = {c: n for c, n in counts.items() if n < arguments.target}

    if arguments.targets:
        web = sorted(c for c in short if SOURCE_CLASS.get(c) == WEB)
        print(",".join(web))
        return 0

    print(f"{len(counts)} encodings; target {arguments.target} wild files each")
    met = len(counts) - len(short)
    print(f"  meeting target: {met}   short: {len(short)}\n")

    by_source: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for codec, count in sorted(short.items()):
        by_source[SOURCE_CLASS.get(codec, ARCHIVE)].append((codec, count))

    labels = {
        WEB: "Common Crawl can supply these (served over HTTP)",
        USENET: "Usenet / mailing-list archives (7-bit and escape-based)",
        ARCHIVE: "software, BBS and disk archives (DOS, Mac, HP era)",
        NONE: "no known public source of real files (EBCDIC)",
    }
    for source in (WEB, USENET, ARCHIVE, NONE):
        entries = by_source.get(source, [])
        if not entries:
            continue
        need = sum(arguments.target - n for _, n in entries)
        print(f"{labels[source]} -- {len(entries)} encodings, {need} files needed")
        for codec, count in entries:
            print(f"    {codec:<18} has {count}, needs {arguments.target - count}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
