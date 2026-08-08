#!/usr/bin/env python3
"""Check this repo's language tables against chardet's registry.

The tables in ``encoding_gaps.py`` (which encoding-language pairs we expect
to have data for) and ``check_test_data.py`` (which scripts a language may
be written in) restate facts that chardet's ``registry.py`` owns.  Restated
facts drift: chardet added Celtic languages to the Western code pages,
Romanian to CP852 and Latin-script Serbian to Windows-1250 in #351, and
this repo did not notice for five months -- which meant the gap analysis
was quietly blind to those pairs and the quality check flagged valid
Latin-script Serbian as a language mismatch.

This script re-derives the comparison and fails loudly, so the next such
change shows up as a failing check rather than a mystery months later.

chardet is an optional import: without it the script skips rather than
failing, so the repo stays dependency-free.

Usage::

    uv run --project ../chardet python3 scripts/check_registry_sync.py
    python3 scripts/check_registry_sync.py          # skips if chardet absent
"""

from __future__ import annotations

import codecs
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_test_data import ISO_TO_LANGUAGE as CHECK_ISO  # noqa: E402
from check_test_data import LANGUAGE_SCRIPTS  # noqa: E402
from encoding_gaps import ENCODING_LANGUAGES, ISO_TO_LANGUAGE, get_codec  # noqa: E402

# Pairs this repo deliberately does not mirror from the registry, with the
# reason.  Anything not listed here is treated as drift.
ALLOWED_DIVERGENCES: dict[tuple[str, str], str] = {
    ("ascii", "english"): "ascii is language-neutral in the registry; the "
    "repo keeps one directory of English ASCII baselines",
}

# Script families the registry implies for a language, by the encodings that
# language appears under.  Only single-script legacy code pages are listed:
# Unicode encodings say nothing about script, and CJK is handled by the
# language tables directly.
SCRIPT_OF_CODEC: dict[str, set[str]] = {
    "Cyrillic": {
        "koi8-r", "koi8-u", "koi8-t", "cp866", "cp855", "cp1125",
        "mac-cyrillic", "cp1251", "iso8859-5", "kz1048", "ptcp154",
    },
    "Greek": {"cp1253", "iso8859-7", "cp737", "cp869", "mac-greek"},
    "Hebrew": {"cp1255", "iso8859-8", "cp862", "cp424"},
    "Arabic": {"cp1256", "iso8859-6", "cp720", "cp864", "cp1006"},
    "Thai": {"tis-620", "cp874"},
    "Latin": {
        "cp1250", "cp1252", "cp1254", "cp1257", "cp1258", "iso8859-1",
        "iso8859-2", "iso8859-3", "iso8859-4", "iso8859-9", "iso8859-10",
        "iso8859-13", "iso8859-14", "iso8859-15", "iso8859-16", "mac-roman",
        "mac-latin2", "mac-iceland", "mac-turkish", "cp437", "cp850",
        "cp852", "cp857", "cp858", "cp860", "cp861", "cp863", "cp865",
        "cp775", "hp-roman8", "cp500", "cp1140", "cp273",
    },
}


def canonical(name: str) -> str | None:
    try:
        return codecs.lookup(name).name
    except (LookupError, ValueError):
        return None


def load_chardet():  # noqa: ANN201
    try:
        from chardet._utils import ISO_TO_LANGUAGE as chardet_iso  # noqa: PLC0415
        from chardet.registry import REGISTRY  # noqa: PLC0415
    except ImportError:
        return None, None
    return REGISTRY, dict(chardet_iso)


def check_iso_tables(chardet_iso: dict[str, str]) -> list[str]:
    """Language-name spellings must match chardet's."""
    problems = []
    for label, table in (
        ("encoding_gaps.ISO_TO_LANGUAGE", ISO_TO_LANGUAGE),
        ("check_test_data.ISO_TO_LANGUAGE", CHECK_ISO),
    ):
        for code, name in sorted(table.items()):
            upstream = chardet_iso.get(code)
            if upstream is None:
                problems.append(f"{label}: {code!r} -> {name!r} is not a chardet language")
            elif upstream.lower() != name.lower():
                problems.append(
                    f"{label}: {code!r} is {name!r} here but {upstream!r} in chardet"
                )
    return problems


def check_encoding_languages(registry, chardet_iso: dict[str, str]) -> list[str]:
    """Every registry encoding-language pair should be expected here."""
    name_to_iso = {v.lower(): k for k, v in chardet_iso.items()}
    by_codec: dict[str, set[str]] = {}
    for info in registry.values():
        codec = canonical(info.name)
        if codec:
            by_codec.setdefault(codec, set()).update(info.languages)

    problems = []
    for prefix, languages in sorted(ENCODING_LANGUAGES.items()):
        codec = canonical(get_codec(prefix))
        if codec is None:
            continue
        expected = by_codec.get(codec)
        if expected is None:
            continue  # aliases (gb2312) and codecs the registry folds together
        ours = {name_to_iso.get(name.lower(), name) for name in languages}
        if len(ours) > 40:
            continue  # the _ALL_LANGUAGES Unicode buckets
        for code in sorted(expected - ours):
            problems.append(
                f"{prefix}: chardet lists {chardet_iso.get(code, code)!r} "
                f"but ENCODING_LANGUAGES does not"
            )
        for code in sorted(ours - expected):
            name = chardet_iso.get(code, code)
            if (prefix, name) in ALLOWED_DIVERGENCES:
                continue
            problems.append(
                f"{prefix}: ENCODING_LANGUAGES lists {name!r} but chardet does not"
            )
    return problems


def check_language_scripts(registry, chardet_iso: dict[str, str]) -> list[str]:
    """A language's scripts must cover every script its encodings imply."""
    name_to_iso = {v.lower(): k for k, v in chardet_iso.items()}
    implied: dict[str, set[str]] = {}
    for info in registry.values():
        codec = canonical(info.name)
        for script, members in SCRIPT_OF_CODEC.items():
            if codec in members or info.name in members:
                for code in info.languages:
                    implied.setdefault(code, set()).add(script)

    problems = []
    for language, scripts in sorted(LANGUAGE_SCRIPTS.items()):
        code = name_to_iso.get(language)
        if code is None:
            continue
        missing = implied.get(code, set()) - set(scripts)
        if missing:
            problems.append(
                f"LANGUAGE_SCRIPTS[{language!r}] = {sorted(scripts)} but chardet "
                f"puts it in {sorted(missing)} encodings too"
            )
    return problems


def main() -> int:
    registry, chardet_iso = load_chardet()
    if registry is None:
        print(
            "chardet not importable -- skipping registry sync check.\n"
            "  uv run --project ../chardet python3 scripts/check_registry_sync.py"
        )
        return 0

    problems = (
        check_iso_tables(chardet_iso)
        + check_encoding_languages(registry, chardet_iso)
        + check_language_scripts(registry, chardet_iso)
    )
    if not problems:
        print("test-data tables are consistent with chardet's registry.")
        return 0

    print(f"{len(problems)} inconsistencies with chardet's registry:\n")
    for problem in problems:
        print(f"  {problem}")
    print(
        "\nEither mirror the change here, or add an entry to "
        "ALLOWED_DIVERGENCES explaining why this repo differs."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
