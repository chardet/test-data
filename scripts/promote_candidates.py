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
    return mapping


def existing_count(directory: Path) -> int:
    return sum(1 for p in directory.iterdir() if p.is_file()) if directory.is_dir() else 0


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
            if row["verdict"] not in wanted:
                continue
            codec = canonical(row["codec"])
            iso = (row["suggested_dir"].rsplit("-", 1) + [""])[1]
            language = ISO_TO_LANGUAGE.get(iso)
            prefix = by_codec.get(codec)

            if prefix is None:
                rejects.append((row["path"], f"no directory prefix for codec {codec}"))
                continue
            if not language:
                rejects.append((row["path"], f"no language tag ({row['languages']!r})"))
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

            promoted[pair] += 1
            plans.append((arguments.input / row["path"], target_dir))

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
