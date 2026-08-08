#!/usr/bin/env python3
"""Promote adjudicated miner candidates into the test-data tree.

Mining produces candidates under scripts/.cache/; this copies the ones worth
keeping into their `{encoding}-{language}/` directory.  It is deliberately
conservative, because a wrong file here becomes wrong ground truth:

- only `strong` candidates by default (`--verdict` to widen);
- the encoding-language pair must be one chardet's registry actually
  claims.  The crawl's language tag is often wrong about *which* language,
  and a Vietnamese codepage labelled German (windows-1258 + de) is a
  mislabelled page, not a new test case;
- the target directory must already resolve to the candidate's codec, so a
  file never invents a directory whose name means something else;
- per-pair caps keep one prolific host from dominating a directory.

Nothing is promoted without `--apply`; the default run is a dry report.

Usage::

    python3 scripts/promote_candidates.py --input scripts/.cache/wild-bulk
    python3 scripts/promote_candidates.py --input scripts/.cache/wild-bulk --apply
"""

from __future__ import annotations

import argparse
import codecs
import csv
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from encoding_gaps import ENCODING_LANGUAGES, ISO_TO_LANGUAGE, get_codec  # noqa: E402
from encoding_overlaps import DISTINGUISHING_BYTES  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_MAX_PER_PAIR = 8


def canonical(name: str) -> str | None:
    try:
        return codecs.lookup(name).name
    except (LookupError, ValueError):
        return None


def prefix_for_codec() -> dict[str, str]:
    """Map a Python codec to the directory prefix this repo uses for it.

    Some prefixes are deliberately mapped to a superset codec -- the
    `shift_jis` directory holds shift_jis_2004 -- so a plain shift_jis page
    would otherwise find no home.  The prefix's own literal codec is
    registered as a fallback, which is safe because the directory decodes
    such a file identically.
    """
    mapping: dict[str, str] = {}
    for prefix in ENCODING_LANGUAGES:
        codec = canonical(get_codec(prefix))
        if codec and codec not in mapping:
            mapping[codec] = prefix
    for prefix in ENCODING_LANGUAGES:
        literal = canonical(prefix)
        if literal and literal not in mapping:
            mapping[literal] = prefix
    # Codecs this repo folds into a superset directory, matching chardet's
    # registry: GB18030 is a strict superset of GBK, so GBK bytes decode
    # identically there and the detector's correct answer is "gb18030".
    for narrow, wide in (("gbk", "gb18030"), ("cp936", "gb18030")):
        if narrow not in mapping and wide in mapping:
            mapping[narrow] = mapping[wide]
    return mapping


_WILD_CACHE: dict[str, str] | None = None


def existing_count(directory: Path) -> int:
    """Wild files already in a directory.

    The cap exists to stop one prolific host dominating a directory, so it
    counts only files that are themselves wild.  Counting every file would
    let a directory of transcoded CulturaX text block the wild samples it
    most needs -- ascii-en held twelve files and exactly one wild one.
    """
    global _WILD_CACHE  # noqa: PLW0603
    if _WILD_CACHE is None:
        try:
            from provenance import WILD, classify_all  # noqa: PLC0415

            _WILD_CACHE = {k: v for k, v in classify_all().items() if v == WILD}
        except Exception:  # noqa: BLE001
            _WILD_CACHE = {}
    if not directory.is_dir():
        return 0
    name = directory.name
    return sum(
        1 for p in directory.iterdir() if p.is_file() and f"{name}/{p.name}" in _WILD_CACHE
    )


# The Usenet miner groups candidates by charset rather than tagging each row
# with a codec and language, because a newsgroup hierarchy implies both.
USENET_PAIR: dict[str, tuple[str, str]] = {
    "hz": ("hz", "zh"),
    "iso-2022-jp": ("iso2022_jp", "ja"),
    "iso-2022-kr": ("iso2022_kr", "ko"),
    "big5": ("big5", "zh"),
    "euc-kr": ("euc_kr", "ko"),
    # BBS artpacks: an English-language scene, so the language is implied
    # by the source the same way a newsgroup hierarchy implies one.
    "cp437": ("cp437", "en"),
    "cp850": ("cp850", "en"),
    # Repository scans: BOM-identified file encodings, English sources.
    "utf-8-sig": ("utf-8-sig", "en"),
    "utf-16": ("utf-16", "en"),
    "utf-32": ("utf-32", "en"),
}


def distinguishes(data: bytes, prefix: str) -> bool:
    """Whether *data* contains a byte unique to this encoding.

    Sibling codepages overlap heavily -- cp437 and cp850 place the German
    umlauts at identical positions -- so a German README is equally valid
    under either and proves neither.  The repo already computes, for every
    single-byte encoding, the bytes that decode differently there than in
    any other; requiring one keeps ambiguous files out of directories that
    would then be asserting something the bytes do not show.

    Encodings with no entry (multi-byte, Unicode, escape-based) are
    identified structurally and pass.
    """
    marks = DISTINGUISHING_BYTES.get(prefix) or DISTINGUISHING_BYTES.get(
        canonical(prefix) or ""
    )
    if not marks:
        return True
    return any(b in marks for b in data)


def normalize_row(row: dict[str, str]) -> tuple[str, str] | None:
    """Return (codec, iso language) for a manifest row of any miner."""
    # PO catalogues carry both explicitly: the charset is declared in the
    # file's own header and the language is its filename.
    if row.get("codec") and row.get("language"):
        return canonical(row["codec"]), row["language"]
    if row.get("codec"):
        iso = (row.get("suggested_dir", "").rsplit("-", 1) + [""])[1]
        return canonical(row["codec"]), iso
    # Usenet manifest: the charset is the candidate's parent directory.
    charset = row["path"].split("/", 1)[0]
    pair = USENET_PAIR.get(charset)
    if pair is None:
        return None
    return canonical(pair[0]), pair[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--input", type=Path, required=True, help="candidate directory")
    parser.add_argument("--verdict", default="strong", help="comma-separated verdicts")
    parser.add_argument("--max-per-pair", type=int, default=DEFAULT_MAX_PER_PAIR)
    parser.add_argument("--apply", action="store_true", help="actually copy files")
    arguments = parser.parse_args()

    manifest = arguments.input / "manifest.csv"
    if not manifest.is_file():
        sys.exit(f"no manifest at {manifest}")
    wanted = {v.strip() for v in arguments.verdict.split(",")}
    by_codec = prefix_for_codec()

    promoted: dict[tuple[str, str], int] = defaultdict(int)
    plans, rejects = [], []
    with manifest.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            # The Usenet manifest has no verdict column: every row it
            # writes already passed that miner's per-charset validation.
            if "verdict" in row and row["verdict"] not in wanted:
                continue
            # ...but its "spans-only" rows are deliberately lenient about
            # RFC 1843 violations, and this repo requires every file to
            # decode strictly under its directory's encoding.  Those stay
            # candidates rather than test data.
            if row.get("status") == "spans-only":
                rejects.append((row["path"], "does not decode strictly"))
                continue
            normalized = normalize_row(row)
            if normalized is None:
                rejects.append((row["path"], "unrecognised manifest row"))
                continue
            codec, iso = normalized
            language = ISO_TO_LANGUAGE.get(iso)
            prefix = by_codec.get(codec)

            if prefix is None:
                rejects.append((row["path"], f"no directory prefix for codec {codec}"))
                continue
            if not language:
                rejects.append(
                    (row["path"], f"no language tag ({row.get('languages', '')!r})")
                )
                continue
            if language not in ENCODING_LANGUAGES.get(prefix, ()):
                rejects.append(
                    (row["path"], f"{prefix} + {language} is not a registry pair")
                )
                continue

            target_dir = REPO / f"{prefix}-{iso}"
            pair = (prefix, iso)
            have = existing_count(target_dir) + promoted[pair]
            if have >= arguments.max_per_pair:
                rejects.append((row["path"], f"{prefix}-{iso} already has {have}"))
                continue

            source = arguments.input / row["path"]
            try:
                data = source.read_bytes()
            except OSError as error:
                rejects.append((row["path"], f"unreadable: {error}"))
                continue
            if not distinguishes(data, prefix):
                rejects.append(
                    (row["path"], f"no byte unique to {prefix}; ambiguous with siblings")
                )
                continue

            promoted[pair] += 1
            plans.append((source, target_dir))

    print(f"{len(plans)} to promote, {len(rejects)} skipped\n")
    for pair, count in sorted(promoted.items()):
        directory = REPO / f"{pair[0]}-{pair[1]}"
        state = "new" if not directory.is_dir() else f"has {existing_count(directory)}"
        print(f"  {pair[0]}-{pair[1]:<6} +{count}  ({state})")

    reasons: dict[str, int] = defaultdict(int)
    for _, reason in rejects:
        reasons[reason.split(" is not")[0].split(" already")[0]] += 1
    if reasons:
        print("\nskipped:")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:12]:
            print(f"  {count:>3}  {reason}")

    if not arguments.apply:
        print("\n(dry run -- pass --apply to copy)")
        return 0

    for source, target_dir in plans:
        target_dir.mkdir(exist_ok=True)
        shutil.copy2(source, target_dir / source.name)
    print(f"\nCopied {len(plans)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
