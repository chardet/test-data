"""Character substitution tables for encoding test data generation.

Provides historically accurate character substitutions for transcoding text
into legacy encodings. Characters not representable in the target encoding
are replaced with historically correct alternatives rather than silently
dropped.

A character the target encoding can hold is left alone: wild files in
Windows and Mac code pages keep their curly quotes, dashes, ellipses and
no-break spaces, so the transcoded files do too. Only the language tables
that record a spelling habit (Croatian, Romanian) apply regardless.

Tables ported from chardet's training script (MIT-licensed, same project).
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Universal substitutions (typographic punctuation -> ASCII)
#
# Applied only to characters the target encoding cannot hold; see
# get_substitutions().
# ---------------------------------------------------------------------------

UNIVERSAL_SUBSTITUTIONS: dict[str, str] = {
    # Dashes
    "\u2010": "-",  # HYPHEN
    "\u2011": "-",  # NON-BREAKING HYPHEN
    "\u2012": "-",  # FIGURE DASH
    "\u2013": "-",  # EN DASH
    "\u2014": "-",  # EM DASH
    "\u2015": "-",  # HORIZONTAL BAR
    # Quotes
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK
    "\u201a": "'",  # SINGLE LOW-9 QUOTATION MARK
    "\u201b": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK
    "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK
    "\u201e": '"',  # DOUBLE LOW-9 QUOTATION MARK
    "\u201f": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
    # Ellipsis
    "\u2026": "...",  # HORIZONTAL ELLIPSIS
    # Spaces
    "\u00a0": " ",  # NO-BREAK SPACE
    "\u2002": " ",  # EN SPACE
    "\u2003": " ",  # EM SPACE
    "\u2009": " ",  # THIN SPACE
    "\u200a": " ",  # HAIR SPACE
    # Other common punctuation
    "\u2022": "*",  # BULLET
    "\u2032": "'",  # PRIME
    "\u2033": '"',  # DOUBLE PRIME
    "\u2212": "-",  # MINUS SIGN
    # Wild Latin-1 era files write the currency out beside the amount; none
    # of the 439 wild files checked uses the generic currency sign for it.
    "\u20ac": "EUR",  # EURO SIGN
    # Arabic-Indic and Extended Arabic-Indic digits -> ASCII digits
    **{chr(0x0660 + i): str(i) for i in range(10)},
    **{chr(0x06F0 + i): str(i) for i in range(10)},
    # Zero-width and formatting characters (remove)
    "\u200b": "",  # ZERO WIDTH SPACE
    "\u200c": "",  # ZERO WIDTH NON-JOINER
    "\u200d": "",  # ZERO WIDTH JOINER
    "\u200e": "",  # LEFT-TO-RIGHT MARK
    "\u200f": "",  # RIGHT-TO-LEFT MARK
    "\ufeff": "",  # ZERO WIDTH NO-BREAK SPACE (BOM)
}

# ---------------------------------------------------------------------------
# Arabic substitutions
# ---------------------------------------------------------------------------

ARABIC_SUBSTITUTIONS: dict[str, str] = {
    "\u060c": ",",  # ARABIC COMMA
    "\u061b": ";",  # ARABIC SEMICOLON
    "\u061f": "?",  # ARABIC QUESTION MARK (missing from CP720)
    "\u066a": "%",  # ARABIC PERCENT SIGN
}

# ---------------------------------------------------------------------------
# CP866 substitutions (shared across all CP866 languages)
# ---------------------------------------------------------------------------

# Ukrainian and Belarusian writers on Russian-only systems typed a Latin i:
# in FidoNet/Usenet archives (2000-2015) 85 Ukrainian and 6 Belarusian
# authors do, against a handful of и spellings that read as dialect.
CP866_SUBSTITUTIONS: dict[str, str] = {
    "\u0456": "i",  # і → Latin i
    "\u0406": "I",  # І → Latin I
}

# ---------------------------------------------------------------------------
# CP866 language-specific substitutions
# ---------------------------------------------------------------------------

# cp866 holds ї and є, and writers used them: FidoNet messages relayed to
# Usenet carry both at their cp866 bytes (62 and 52 of 97 authors), beside
# a Latin i for the і the code page lacks.  Only ґ needs a stand-in.
CP866_UKRAINIAN_SUBSTITUTIONS: dict[str, str] = {
    "\u0490": "\u0413",  # Ґ → Г (Ukrainian Ghe uppercase → Russian Ghe uppercase)
    "\u0491": "\u0433",  # ґ → г (Ukrainian Ghe → Russian Ghe)
}

# Stand-ins follow what Serbian text in Russian-only encodings looks like in
# FidoNet/Usenet archives (fido7.ru.linguist, fido7.ru.yugoslavia and others,
# 2003-2015): soft-sign digraphs for љ and њ, Latin h for ћ, дж for џ, Latin j
# for ј.  No message spelled ђ, ќ, ѓ or ѕ, so those keep their base letters.
CP866_SERBIAN_SUBSTITUTIONS: dict[str, str] = {
    "\u0452": "\u0434",  # ђ → д (Dje → De)
    "\u0458": "j",  # ј → Latin j, the spelling writers without the letter use
    "\u0459": "\u043b\u044c",  # љ → ль
    "\u045a": "\u043d\u044c",  # њ → нь
    "\u045b": "h",  # ћ → Latin h
    "\u045f": "\u0434\u0436",  # џ → дж
    "\u0402": "\u0414",  # Ђ → Д (uppercase)
    "\u0408": "J",  # Ј → Latin J (uppercase)
    "\u0409": "\u041b\u044c",  # Љ → Ль
    "\u040a": "\u041d\u044c",  # Њ → Нь
    "\u040b": "H",  # Ћ → Latin H
    "\u040f": "\u0414\u0436",  # Џ → Дж
}

CP866_MACEDONIAN_SUBSTITUTIONS: dict[str, str] = {
    "\u0453": "\u0433",  # ѓ → г (Gje → Ghe)
    "\u0455": "\u0437",  # ѕ → з (Dze → Ze)
    "\u0458": "j",  # ј → Latin j, the spelling writers without the letter use
    "\u045c": "\u043a",  # ќ → к (Kje → Ka)
    "\u0459": "\u043b\u044c",  # љ → ль
    "\u045a": "\u043d\u044c",  # њ → нь
    "\u045f": "\u0434\u0436",  # џ → дж
    "\u0403": "\u0413",  # Ѓ → Г (uppercase)
    "\u0405": "\u0417",  # Ѕ → З (uppercase)
    "\u0408": "J",  # Ј → Latin J (uppercase)
    "\u040c": "\u041a",  # Ќ → К (uppercase)
    "\u0409": "\u041b\u044c",  # Љ → Ль
    "\u040a": "\u041d\u044c",  # Њ → Нь
    "\u040f": "\u0414\u0436",  # Џ → Дж
}

# ---------------------------------------------------------------------------
# ISO-8859-6 Farsi substitutions
# ---------------------------------------------------------------------------

ISO8859_6_FARSI_SUBSTITUTIONS: dict[str, str] = {
    "\u067e": "\u0628",  # پ → ب (Pe → Ba)
    "\u0686": "\u062c",  # چ → ج (Che → Jim)
    "\u0698": "\u0632",  # ژ → ز (Zhe → Za)
    "\u06af": "\u0643",  # گ → ك (Gaf → Kaf)
    "\u06cc": "\u064a",  # ی → ي (Farsi Yeh → Arabic Yeh)
    "\u06a9": "\u0643",  # ک → ك (Farsi Kaf → Arabic Kaf)
}

# cp1006 Urdu: map Urdu-specific chars to base Arabic equivalents (which then
# get mapped to presentation forms by normalize_text).
CP1006_URDU_SUBSTITUTIONS: dict[str, str] = {
    "\u06a9": "\u0643",  # ک → ك (Keheh → Kaf)
    "\u06cc": "\u064a",  # ی → ي (Farsi Yeh → Arabic Yeh)
    "\u0688": "\u062f",  # ڈ → د (Ddal → Dal)
    "\u06d4": ".",        # ۔ → . (Arabic Full Stop → ASCII period)
    "\u064b": "",         # ً  (Fathatan diacritic — drop)
    "\u064c": "",         # ٌ  (Dammatan — drop)
    "\u064d": "",         # ٍ  (Kasratan — drop)
    "\u064e": "",         # َ  (Fathah — drop)
    "\u064f": "",         # ُ  (Dammah — drop)
    "\u0650": "",         # ِ  (Kasrah — drop)
    "\u0652": "",         # ْ  (Sukun — drop)
}

# cp864 Arabic: map characters not in cp864's repertoire to close equivalents.
CP864_ARABIC_SUBSTITUTIONS: dict[str, str] = {
    "\u06a9": "\u0643",  # ک → ك (Keheh → Kaf)
    "\u06cc": "\u064a",  # ی → ي (Farsi Yeh → Arabic Yeh)
    "\u06d4": ".",        # ۔ → . (Arabic Full Stop → ASCII period)
    "\u064b": "",         # ً  (Fathatan — drop)
    "\u064c": "",         # ٌ  (Dammatan — drop)
    "\u064d": "",         # ٍ  (Kasratan — drop)
    "\u064e": "",         # َ  (Fathah — drop)
    "\u064f": "",         # ُ  (Dammah — drop)
    "\u0650": "",         # ِ  (Kasrah — drop)
    "\u0652": "",         # ْ  (Sukun — drop)
}

# ---------------------------------------------------------------------------
# Croatian normalization
# ---------------------------------------------------------------------------

CROATIAN_NORMALIZE: dict[str, str] = {
    "\u00f0": "\u0111",  # ð → đ (Latin small eth → Latin small d with stroke)
    "\u00d0": "\u0110",  # Ð → Đ (Latin capital eth → Latin capital d with stroke)
}

# ---------------------------------------------------------------------------
# Romanian cedilla substitutions
# ---------------------------------------------------------------------------

ROMANIAN_CEDILLA_SUBSTITUTIONS: dict[str, str] = {
    "\u021b": "\u0163",  # ț → ţ (comma-below → cedilla)
    "\u0219": "\u015f",  # ș → ş (comma-below → cedilla)
    "\u021a": "\u0162",  # Ț → Ţ (uppercase)
    "\u0218": "\u015e",  # Ș → Ş (uppercase)
}

# ---------------------------------------------------------------------------
# Vietnamese decomposition (NFC -> decomposed for Windows-1258)
# ---------------------------------------------------------------------------

VIETNAMESE_DECOMPOSITION: dict[str, str] = {
    # Regular vowels + tones
    "à": "a\u0300",
    "á": "a\u0301",
    "ả": "a\u0309",
    "ã": "a\u0303",
    "ạ": "a\u0323",
    "è": "e\u0300",
    "é": "e\u0301",
    "ẻ": "e\u0309",
    "ẽ": "e\u0303",
    "ẹ": "e\u0323",
    "ì": "i\u0300",
    "í": "i\u0301",
    "ỉ": "i\u0309",
    "ĩ": "i\u0303",
    "ị": "i\u0323",
    "ò": "o\u0300",
    "ó": "o\u0301",
    "ỏ": "o\u0309",
    "õ": "o\u0303",
    "ọ": "o\u0323",
    "ù": "u\u0300",
    "ú": "u\u0301",
    "ủ": "u\u0309",
    "ũ": "u\u0303",
    "ụ": "u\u0323",
    "ỳ": "y\u0300",
    "ý": "y\u0301",
    "ỷ": "y\u0309",
    "ỹ": "y\u0303",
    "ỵ": "y\u0323",
    # â (circumflex) + tones
    "ấ": "â\u0301",
    "ầ": "â\u0300",
    "ẩ": "â\u0309",
    "ẫ": "â\u0303",
    "ậ": "â\u0323",
    # ê (circumflex) + tones
    "ế": "ê\u0301",
    "ề": "ê\u0300",
    "ể": "ê\u0309",
    "ễ": "ê\u0303",
    "ệ": "ê\u0323",
    # ô (circumflex) + tones
    "ố": "ô\u0301",
    "ồ": "ô\u0300",
    "ổ": "ô\u0309",
    "ỗ": "ô\u0303",
    "ộ": "ô\u0323",
    # ă (breve) + tones
    "ắ": "ă\u0301",
    "ằ": "ă\u0300",
    "ẳ": "ă\u0309",
    "ẵ": "ă\u0303",
    "ặ": "ă\u0323",
    # ơ (horn) + tones
    "ớ": "ơ\u0301",
    "ờ": "ơ\u0300",
    "ở": "ơ\u0309",
    "ỡ": "ơ\u0303",
    "ợ": "ơ\u0323",
    # ư (horn) + tones
    "ứ": "ư\u0301",
    "ừ": "ư\u0300",
    "ử": "ư\u0309",
    "ữ": "ư\u0303",
    "ự": "ư\u0323",
    # Uppercase variants
    "À": "A\u0300",
    "Á": "A\u0301",
    "Ả": "A\u0309",
    "Ã": "A\u0303",
    "Ạ": "A\u0323",
    "È": "E\u0300",
    "É": "E\u0301",
    "Ẻ": "E\u0309",
    "Ẽ": "E\u0303",
    "Ẹ": "E\u0323",
    "Ì": "I\u0300",
    "Í": "I\u0301",
    "Ỉ": "I\u0309",
    "Ĩ": "I\u0303",
    "Ị": "I\u0323",
    "Ò": "O\u0300",
    "Ó": "O\u0301",
    "Ỏ": "O\u0309",
    "Õ": "O\u0303",
    "Ọ": "O\u0323",
    "Ù": "U\u0300",
    "Ú": "U\u0301",
    "Ủ": "U\u0309",
    "Ũ": "U\u0303",
    "Ụ": "U\u0323",
    "Ỳ": "Y\u0300",
    "Ý": "Y\u0301",
    "Ỷ": "Y\u0309",
    "Ỹ": "Y\u0303",
    "Ỵ": "Y\u0323",
    "Ấ": "Â\u0301",
    "Ầ": "Â\u0300",
    "Ẩ": "Â\u0309",
    "Ẫ": "Â\u0303",
    "Ậ": "Â\u0323",
    "Ế": "Ê\u0301",
    "Ề": "Ê\u0300",
    "Ể": "Ê\u0309",
    "Ễ": "Ê\u0303",
    "Ệ": "Ê\u0323",
    "Ố": "Ô\u0301",
    "Ồ": "Ô\u0300",
    "Ổ": "Ô\u0309",
    "Ỗ": "Ô\u0303",
    "Ộ": "Ô\u0323",
    "Ắ": "Ă\u0301",
    "Ằ": "Ă\u0300",
    "Ẳ": "Ă\u0309",
    "Ẵ": "Ă\u0303",
    "Ặ": "Ă\u0323",
    "Ớ": "Ơ\u0301",
    "Ờ": "Ơ\u0300",
    "Ở": "Ơ\u0309",
    "Ỡ": "Ơ\u0303",
    "Ợ": "Ơ\u0323",
    "Ứ": "Ư\u0301",
    "Ừ": "Ư\u0300",
    "Ử": "Ư\u0309",
    "Ữ": "Ư\u0303",
    "Ự": "Ư\u0323",
}


# ---------------------------------------------------------------------------
# Arabic presentation form tables (for cp1006 / cp864)
# ---------------------------------------------------------------------------
# These encodings use Arabic Presentation Forms (U+FB50+ / U+FE70+) rather
# than the standard Arabic block (U+0600+).  CulturaX text uses the standard
# block, so we must map base characters to their presentation-form equivalents
# before encoding.  We use isolated forms (simplest, always valid).

def _build_arabic_pres_map(codec: str) -> dict[str, str]:
    """Build base-Arabic -> presentation-form map for *codec*."""
    mapping: dict[str, str] = {}
    for b in range(0x80, 0x100):
        try:
            c = bytes([b]).decode(codec)
            base = unicodedata.normalize("NFKC", c)
            if base != c and len(base) == 1 and base not in mapping:
                mapping[base] = c
        except (UnicodeDecodeError, LookupError):
            pass
    return mapping


_CP1006_PRES_MAP: dict[str, str] = _build_arabic_pres_map("cp1006")
_CP864_PRES_MAP: dict[str, str] = _build_arabic_pres_map("cp864")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def normalize_text(text: str, encoding: str) -> str:
    """Clean and normalize text for encoding into a legacy charset."""
    # Collapse repeated whitespace
    text = re.sub(r"(\s)\1+", r"\1", text)
    enc_upper = encoding.upper()
    # Vietnamese decomposition for Windows-1258
    if enc_upper == "WINDOWS-1258":
        nfc = unicodedata.normalize("NFC", text)
        text = "".join(VIETNAMESE_DECOMPOSITION.get(c, c) for c in nfc)
    # Arabic presentation forms for cp1006.  cp864 no longer uses a naive
    # isolated-form map here: the generator shapes its text contextually
    # and stores it in visual order (scripts/arabic_shape.py +
    # scripts/bidi_order.py), matching how chardet trains that model.
    if enc_upper == "CP1006":
        text = "".join(_CP1006_PRES_MAP.get(c, c) for c in text)
    return text


def apply_substitutions(text: str, subs: dict[str, str]) -> str:
    """Apply character substitutions to make text encodable in legacy charsets."""
    for old, new in subs.items():
        if old in text:
            text = text.replace(old, new)
    return text


def _can_encode(char: str, codec: str) -> bool:
    """Return True if *codec* has a byte sequence for *char*."""
    try:
        char.encode(codec)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def get_substitutions(
    encoding: str, language: str, codec: str | None = None,
) -> dict[str, str]:
    """Build the character substitution table for a given encoding and language.

    Returns a merged dictionary of all applicable substitutions based on
    the target encoding and language.  *codec* is the Python codec name
    when it differs from the directory prefix *encoding*.
    """
    subs = dict(UNIVERSAL_SUBSTITUTIONS)
    enc_upper = encoding.upper()

    if enc_upper in ("CP720", "CP864", "ISO-8859-6"):
        subs.update(ARABIC_SUBSTITUTIONS)

    # Fold only what the encoding cannot hold.  The byte order mark is
    # the exception: mid-text it is always noise.
    subs = {
        char: repl
        for char, repl in subs.items()
        if char == "\ufeff" or not _can_encode(char, codec or encoding)
    }

    if enc_upper == "CP1006":
        subs.update(CP1006_URDU_SUBSTITUTIONS)

    if enc_upper == "CP864":
        subs.update(CP864_ARABIC_SUBSTITUTIONS)

    if enc_upper == "CP866":
        subs.update(CP866_SUBSTITUTIONS)  # і → Latin i for all CP866
        if language == "ukrainian":
            subs.update(CP866_UKRAINIAN_SUBSTITUTIONS)
        elif language == "serbian":
            subs.update(CP866_SERBIAN_SUBSTITUTIONS)
        elif language == "macedonian":
            subs.update(CP866_MACEDONIAN_SUBSTITUTIONS)

    if enc_upper == "ISO-8859-6" and language == "farsi":
        subs.update(ISO8859_6_FARSI_SUBSTITUTIONS)

    if language == "croatian":
        subs.update(CROATIAN_NORMALIZE)

    if language == "romanian" and enc_upper != "ISO-8859-16":
        subs.update(ROMANIAN_CEDILLA_SUBSTITUTIONS)

    return subs
