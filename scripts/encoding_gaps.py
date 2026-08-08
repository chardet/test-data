"""Gap analysis for missing encoding-language test data directories.

Compares the full set of encoding-language pairs expected by the chardet
registry against what already exists in the test-data repo.
"""

from __future__ import annotations

import codecs
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# ISO 639-1 code <-> English language name mappings
# ---------------------------------------------------------------------------
# Copied from chardet/scripts/utils.py with the addition of "ms" -> "malay".
ISO_TO_LANGUAGE: dict[str, str] = {
    "ar": "arabic",
    "be": "belarusian",
    "bg": "bulgarian",
    "br": "breton",
    "cs": "czech",
    "cy": "welsh",
    "da": "danish",
    "de": "german",
    "el": "greek",
    "en": "english",
    "eo": "esperanto",
    "es": "spanish",
    "et": "estonian",
    "fa": "farsi",
    "fi": "finnish",
    "fr": "french",
    "ga": "irish",
    "gd": "gaelic",
    "he": "hebrew",
    "hr": "croatian",
    "hu": "hungarian",
    "id": "indonesian",
    "is": "icelandic",
    "it": "italian",
    "ja": "japanese",
    "kk": "kazakh",
    "ko": "korean",
    "lt": "lithuanian",
    "lv": "latvian",
    "mk": "macedonian",
    "ms": "malay",
    "mt": "maltese",
    "nl": "dutch",
    "no": "norwegian",
    "pl": "polish",
    "pt": "portuguese",
    "ro": "romanian",
    "ru": "russian",
    "sk": "slovak",
    "sl": "slovene",
    "sr": "serbian",
    "sv": "swedish",
    "tg": "tajik",
    "th": "thai",
    "tr": "turkish",
    "uk": "ukrainian",
    "ur": "urdu",
    "vi": "vietnamese",
    "zh": "chinese",
}

LANGUAGE_TO_ISO: dict[str, str] = {v: k for k, v in ISO_TO_LANGUAGE.items()}

# All language names (used for Unicode encodings that support every language).
_ALL_LANGUAGES = tuple(sorted(ISO_TO_LANGUAGE.values()))

# ---------------------------------------------------------------------------
# Shared language groups (mirror the chardet registry tuples, but as names)
# ---------------------------------------------------------------------------
_WESTERN = (
    "english", "french", "german", "spanish", "portuguese", "italian",
    "dutch", "danish", "swedish", "norwegian", "finnish", "icelandic",
    "indonesian", "malay",
    # Celtic languages use Latin script and appear across the Western
    # code pages; added to chardet's registry in #351.
    "irish", "welsh", "breton",
)
_WESTERN_TR = (*_WESTERN, "turkish")
_CYRILLIC = (
    "russian", "bulgarian", "ukrainian", "serbian", "macedonian", "belarusian",
)
_CENTRAL_EU = (
    "polish", "czech", "hungarian", "croatian", "romanian", "slovak", "slovene",
)
_CENTRAL_EU_NO_RO = (
    "polish", "czech", "hungarian", "croatian", "slovak", "slovene",
)
_BALTIC = ("estonian", "lithuanian", "latvian")
_ARABIC = ("arabic", "farsi")

# ---------------------------------------------------------------------------
# ENCODING_LANGUAGES — test-data directory prefix -> tuple of language names
#
# Keys use the test-data directory naming convention (not Python codec names).
# Every encoding in chardet's registry.py is covered.
# ---------------------------------------------------------------------------
ENCODING_LANGUAGES: dict[str, tuple[str, ...]] = {
    # === Unicode (all languages) ===
    "utf-8": _ALL_LANGUAGES,
    "utf-8-sig": _ALL_LANGUAGES,
    "utf-16": _ALL_LANGUAGES,
    "utf-16be": _ALL_LANGUAGES,
    "utf-16le": _ALL_LANGUAGES,
    "utf-32": _ALL_LANGUAGES,
    "utf-32be": _ALL_LANGUAGES,
    "utf-32le": _ALL_LANGUAGES,
    "utf-7": _ALL_LANGUAGES,
    # === ASCII ===
    "ascii": ("english",),
    # === CJK ===
    "big5": ("chinese",),           # registry: big5hkscs
    "cp932": ("japanese",),
    "cp949": ("korean",),
    "euc-jp": ("japanese",),        # registry: euc-jis-2004
    "euc-kr": ("korean",),
    "gb18030": ("chinese",),
    "gb2312": ("chinese",),         # alias of gb18030 in registry
    "hz-gb-2312": ("chinese",),
    "iso-2022-jp": ("japanese",),   # registry: iso2022-jp-2
    "iso-2022-jp-2004": ("japanese",),  # registry: iso2022-jp-2004
    "iso-2022-jp-ext": ("japanese",),   # registry: iso2022-jp-ext
    "iso-2022-kr": ("korean",),
    "shift_jis": ("japanese",),     # registry: shift_jis_2004
    "shift-jis": ("japanese",),     # alternate naming in test-data
    "johab": ("korean",),
    # === Windows code pages ===
    "cp874": ("thai",),
    # Latin-script Serbian uses Windows-1250 (chardet #351).
    "windows-1250": (*_CENTRAL_EU, "serbian"),
    "windows-1251": _CYRILLIC,
    "windows-1252": _WESTERN,
    "windows-1253": ("greek",),
    "windows-1254": ("turkish",),
    "windows-1255": ("hebrew",),
    "windows-1256": _ARABIC,
    "windows-1257": _BALTIC,
    "windows-1258": ("vietnamese",),
    # === KOI8 ===
    # Bulgarian in KOI8-R is not a real-world pair; the registry lists only
    # Russian, and the koi8-r-bg directory was removed for the same reason.
    "koi8-r": ("russian",),
    "koi8-u": ("ukrainian",),
    # === TIS-620 ===
    "tis-620": ("thai",),
    "iso-8859-11": ("thai",),       # alias of tis-620 in registry
    # === ISO 8859 ===
    "iso-8859-1": _WESTERN,
    "iso-8859-2": _CENTRAL_EU,
    "iso-8859-3": ("esperanto", "maltese", "turkish"),
    "iso-8859-4": _BALTIC,
    "iso-8859-5": _CYRILLIC,
    "iso-8859-6": _ARABIC,
    "iso-8859-7": ("greek",),
    "iso-8859-8": ("hebrew",),
    "iso-8859-9": ("turkish",),
    "iso-8859-10": ("icelandic", "finnish"),
    "iso-8859-13": _BALTIC,
    "iso-8859-14": ("welsh", "irish", "breton", "gaelic"),
    "iso-8859-15": _WESTERN,
    "iso-8859-16": ("romanian", "polish", "croatian", "hungarian", "slovak", "slovene"),
    # === Legacy Mac ===
    "maccyrillic": _CYRILLIC,       # registry: mac-cyrillic
    "macgreek": ("greek",),         # registry: mac-greek
    "maciceland": ("icelandic",),   # registry: mac-iceland
    "maclatin2": _CENTRAL_EU_NO_RO, # registry: mac-latin2
    "macroman": _WESTERN,           # registry: mac-roman
    "macturkish": ("turkish",),     # registry: mac-turkish
    # === Legacy Regional ===
    "cp720": _ARABIC,
    "cp1006": ("urdu",),
    "cp1125": ("ukrainian",),
    "koi8-t": ("tajik",),
    "kz1048": ("kazakh",),          # registry: kz-1048
    "ptcp154": ("kazakh",),
    "hp-roman8": _WESTERN,
    # === DOS ===
    "cp437": (
        "english", "french", "german", "spanish", "portuguese", "italian",
        "dutch", "danish", "swedish", "finnish", "irish",
    ),
    "cp737": ("greek",),
    "cp775": _BALTIC,
    "cp850": _WESTERN,
    # Romanian was encoded in CP852 on DOS, using cedilla forms (chardet #351).
    "cp852": _CENTRAL_EU,
    "cp855": _CYRILLIC,
    "cp856": ("hebrew",),
    "cp857": ("turkish",),
    "cp858": _WESTERN,
    "cp860": ("portuguese",),
    "cp861": ("icelandic",),
    "cp862": ("hebrew",),
    "cp863": ("french",),
    "cp864": ("arabic",),
    "cp865": ("danish", "norwegian"),
    "cp866": _CYRILLIC,
    "cp869": ("greek",),
    # === Mainframe (EBCDIC) ===
    "cp037": _WESTERN_TR,           # registry: cp1140 (alias cp037)
    "cp424": ("hebrew",),
    "cp500": _WESTERN,
    "cp875": ("greek",),
    "cp1026": ("turkish",),
    "cp273": ("german",),
}

# ---------------------------------------------------------------------------
# ENCODING_CODEC — test-data directory prefix -> Python codec name
#
# Only entries where the test-data prefix differs from what codecs.lookup()
# would accept directly.
# ---------------------------------------------------------------------------
ENCODING_CODEC: dict[str, str] = {
    "big5": "big5hkscs",
    "cp037": "cp1140",
    "euc-jp": "euc_jis_2004",
    "iso-2022-jp": "iso2022_jp_2",
    "iso-8859-11": "tis-620",
    "shift_jis": "shift_jis_2004",
    "shift-jis": "shift_jis_2004",
}


def get_codec(encoding_prefix: str) -> str:
    """Return the Python codec name for a test-data directory prefix."""
    return ENCODING_CODEC.get(encoding_prefix, encoding_prefix)


def _verify_codecs() -> None:
    """Verify that every encoding in ENCODING_LANGUAGES has a valid Python codec."""
    for prefix in ENCODING_LANGUAGES:
        codec_name = get_codec(prefix)
        try:
            codecs.lookup(codec_name)
        except LookupError:
            msg = (
                f"No Python codec for test-data prefix {prefix!r} "
                f"(tried {codec_name!r})"
            )
            raise LookupError(msg) from None


def find_gaps(base_dir: str | Path) -> list[tuple[str, str]]:
    """Find missing encoding-language directories.

    Compares existing ``{encoding}-{language}/`` directories under *base_dir*
    against :data:`ENCODING_LANGUAGES` and returns a sorted list of
    ``(encoding_prefix, language_name)`` tuples that are missing.

    Skips ``"ascii"`` (different directory structure) and ``"None"``
    (binary test files).
    """
    base = Path(base_dir)

    # Collect existing (encoding_prefix, language) pairs from directory names.
    existing: set[tuple[str, str]] = set()
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        # Skip non-encoding directories
        if name.startswith(".") or name in ("None", "None-None", "scripts"):
            continue
        # Split on last hyphen — all language names are single words.
        parts = name.rsplit("-", 1)
        if len(parts) != 2:
            continue
        enc_prefix, language = parts[0], parts[1]
        # Directories use ISO 639-1 codes since the 2026 rename, while
        # ENCODING_LANGUAGES is keyed by full name.  Without normalizing,
        # nothing matches and every expected pair reports as a gap.
        language = ISO_TO_LANGUAGE.get(language, language)
        existing.add((enc_prefix, language))

    # Build the full expected set and find what's missing.
    missing: list[tuple[str, str]] = []
    for enc_prefix, languages in ENCODING_LANGUAGES.items():
        # Skip ascii — its test data uses a different directory structure.
        if enc_prefix == "ascii":
            continue
        for lang in languages:
            if (enc_prefix, lang) not in existing:
                missing.append((enc_prefix, lang))

    missing.sort()
    return missing


# Run codec verification on import so problems are caught early.
_verify_codecs()
