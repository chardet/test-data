#!/usr/bin/env python3
r"""Mine Common Crawl for pages served in rare legacy charsets.

Queries the Common Crawl columnar index (Parquet over HTTPS) for pages
whose ``content_charset`` is one of the encodings this repo lacks real
(non-transcoded) samples for, fetches the underlying WARC records with
HTTP range requests, and validates each candidate:

- the payload must decode strictly under the declared charset;
- the HTTP ``Content-Type`` header and HTML ``<meta>`` charset are
  extracted independently and compared against the index value;
- a C1-control/replacement-character fraction guards against mojibake;
- the decoded text must land in the declared encoding's home script;
- chardet's verdict is recorded when chardet is importable (agreement is
  informative, not a filter -- disagreement on valid wild data is
  exactly what we want to find).

Declared charsets lie often, so every candidate gets one verdict saying
what the evidence supports:

``strong``           header verified; usable as labeled test data
``ambiguous``        a cross-script reading also decodes -- adjudicate
``sparse``           too little non-ASCII to verify; header uncontradicted
``vacuous``          pure ASCII; the header is untestable, not wrong
``utf8-mislabeled``  content is valid multi-byte UTF-8; header wrong
``wrong-legacy``     decodes, but outside the declared home script
``mojibake``         declared decode fails or yields junk
``review``           residual conflicts (meta vs index charset)

See ``classify`` for the precise ladder.

Candidates land in ``--output`` as ``{codec}/cc_{host}_{digest}.html``
with a ``manifest.csv`` of every signal.  Nothing is promoted into the
repo automatically: vetted files are copied into their
``{encoding}-{language}/`` directory by hand.

Dependencies: the standard library, plus ``duckdb`` for the ``mine`` and
``stats`` modes (querying remote Parquet).  ``adjudicate`` needs neither
duckdb nor the network.  chardet is optional everywhere; without it the
detector columns stay empty.

Usage::

    # what charsets does a slice of the crawl declare?
    python3 scripts/mine_common_crawl.py stats

    # mine the default rare-charset list
    python3 scripts/mine_common_crawl.py mine --parts 4 --max-per-charset 8

    # show competing readings of everything needing a human/LLM ruling
    python3 scripts/mine_common_crawl.py adjudicate

To include chardet's opinion, run under the sibling chardet checkout::

    uv run --project ../chardet --with duckdb python3 scripts/mine_common_crawl.py mine
"""

from __future__ import annotations

import argparse
import codecs
import csv
import gzip
import hashlib
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

CC_BASE = "https://data.commoncrawl.org/"
DEFAULT_CRAWL = "CC-MAIN-2019-35"
DEFAULT_OUTPUT = Path("scripts/.cache/wild-charsets")

# Index charset values (lowercased) worth harvesting: encodings this repo
# has no wild samples for, or none at all.  The columnar index stores the
# charset Common Crawl's WET converter sniffed from the HTTP header and
# HTML meta -- a strong prior, not ground truth.  Validation re-derives
# both independently.
DEFAULT_TARGETS: tuple[str, ...] = (
    "big5-hkscs",
    "hz-gb-2312",
    "koi8-u",
    "koi8-r",
    "koi8-t",
    "tis-620",
    "windows-874",
    "x-windows-874",
    "iso-8859-11",
    "cp874",
    "ibm866",
    "cp866",
    "ibm855",
    "ibm852",
    "ibm850",
    "x-mac-cyrillic",
    "x-maccyrillic",
    "macintosh",
    "iso-2022-jp",
    "iso-2022-kr",
    "johab",
    "ptcp154",
    "kz-1048",
    "iso-8859-10",
    "iso-8859-14",
    "iso-8859-16",
    "gb18030",
)

# Charset spellings the index uses that codecs.lookup() does not know.
# Every DEFAULT_TARGETS value must resolve through canonical_codec():
# a page we cannot decode is useless as labeled test data.
INDEX_CHARSET_ALIASES: dict[str, str] = {
    "x-windows-874": "cp874",
    "windows-874": "cp874",
    "x-maccyrillic": "mac-cyrillic",
    "x-mac-cyrillic": "mac-cyrillic",
    "macintosh": "mac-roman",
    "kz-1048": "kz1048",
    "iso-8859-11": "tis-620",
    "hz-gb-2312": "hz",
    "big5-hkscs": "big5hkscs",
    "ibm850": "cp850",
    "ibm852": "cp852",
    "ibm855": "cp855",
    "ibm866": "cp866",
    # Python has no alias for these, though the crawl reports them.
    "windows-949": "cp949",
    "x-windows-949": "cp949",
    "windows-950": "cp950",
    "x-windows-950": "cp950",
    "x-mac-roman": "mac-roman",
    "x-mac-greek": "mac-greek",
    "x-mac-turkish": "mac-turkish",
    "x-mac-icelandic": "mac-iceland",
    "x-mac-ce": "mac-latin2",
}

# Candidate quality thresholds.
MAX_BODY_BYTES = 1 << 20  # skip pages larger than 1 MiB
MIN_BODY_BYTES = 256  # skip pages too small to be useful test data
MAX_JUNK_FRACTION = 0.002  # C1 controls + U+FFFD per decoded char
MIN_NON_ASCII = 50  # non-ASCII bytes needed to verify a declared charset
MIN_SCRIPT_FRACTION = 0.5  # non-ASCII chars that must be in the home script

_CHARSET_IN_CONTENT_TYPE = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)
_META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)
_TAGS = re.compile(r"(?s)<(script|style)[^>]*>.*?</\1>|<[^>]+>")

# Expected Unicode script ranges per encoding family.  A page declared as
# a Cyrillic codepage whose non-ASCII content is not mostly Cyrillic is a
# mislabel however cleanly it decodes -- single-byte codecs decode any
# byte.  Ranges are (start, end) inclusive.
SCRIPT_RANGES: dict[str, tuple[tuple[str, str], ...]] = {
    "cyrillic": (("Ѐ", "ӿ"), ("Ԁ", "ԯ")),
    "greek": (("Ͱ", "Ͽ"), ("ἀ", "῿")),
    "hebrew": (("֐", "׿"),),
    "arabic": (("؀", "ۿ"), ("ݐ", "ݿ"), ("ﭐ", "﷿")),
    "thai": (("฀", "๿"),),
    "cjk": (("一", "鿿"), ("　", "〿"), ("＀", "￯")),
    "japanese": (
        ("぀", "ヿ"),
        ("一", "鿿"),
        ("＀", "￯"),
    ),
    "korean": (("가", "힣"), ("㄰", "㆏")),
    "latin": (("À", "ɏ"),),
}

ENCODING_SCRIPT: dict[str, str] = {
    "koi8-r": "cyrillic",
    "koi8-u": "cyrillic",
    "koi8-t": "cyrillic",
    "cp866": "cyrillic",
    "cp855": "cyrillic",
    "cp1125": "cyrillic",
    "mac-cyrillic": "cyrillic",
    "cp1251": "cyrillic",
    "iso8859-5": "cyrillic",
    "kz1048": "cyrillic",
    "ptcp154": "cyrillic",
    "cp1253": "greek",
    "iso8859-7": "greek",
    "cp737": "greek",
    "cp869": "greek",
    "mac-greek": "greek",
    "cp1255": "hebrew",
    "iso8859-8": "hebrew",
    "cp862": "hebrew",
    "cp1256": "arabic",
    "iso8859-6": "arabic",
    "cp720": "arabic",
    "cp864": "arabic",
    "tis-620": "thai",
    "cp874": "thai",
    "big5": "cjk",
    "big5hkscs": "cjk",
    "gb2312": "cjk",
    "gbk": "cjk",
    "gb18030": "cjk",
    "hz": "cjk",
    "iso2022_jp": "japanese",
    "iso2022_jp_2": "japanese",
    "shift_jis": "japanese",
    "cp932": "japanese",
    "euc_jp": "japanese",
    "iso2022_kr": "korean",
    "euc_kr": "korean",
    "cp949": "korean",
    "johab": "korean",
    "iso8859-10": "latin",
    "iso8859-14": "latin",
    "iso8859-16": "latin",
    "mac-roman": "latin",
    "cp850": "latin",
    "cp852": "latin",
}

# Encodings tried when adjudicating a disputed page, beyond the declared
# and detected ones.  Reading the text is the only way to settle these:
# the ISO-8859-6 Arabic pages in the first mining run were named by
# neither their header (x-maccyrillic) nor chardet (windows-1250).
ADJUDICATION_POOL: tuple[str, ...] = (
    "utf-8",
    "cp1251",
    "koi8-r",
    "koi8-u",
    "cp866",
    "cp855",
    "mac-cyrillic",
    "iso8859-5",
    "cp1256",
    "iso8859-6",
    "cp720",
    "cp864",
    "cp1255",
    "iso8859-8",
    "cp1253",
    "iso8859-7",
    "cp874",
    "tis-620",
    "gb18030",
    "big5",
    "big5hkscs",
    "euc_kr",
    "euc_jp",
    "shift_jis",
    "iso2022_jp_2",
    "cp1250",
    "cp1252",
    "iso8859-1",
    "iso8859-2",
    "cp850",
    "cp852",
    "mac-roman",
)

# ISO 639-3 (as the index reports languages) -> ISO 639-1, for suggesting
# the destination directory name of a promoted file.
ISO3_TO_ISO1: dict[str, str] = {
    "ara": "ar",
    "bel": "be",
    "bre": "br",
    "bul": "bg",
    "ces": "cs",
    "cym": "cy",
    "epo": "eo",
    "gla": "gd",
    "gle": "ga",
    "ind": "id",
    "mlt": "mt",
    "msa": "ms",
    "zsm": "ms",
    "dan": "da",
    "deu": "de",
    "ell": "el",
    "eng": "en",
    "est": "et",
    "fas": "fa",
    "fin": "fi",
    "fra": "fr",
    "heb": "he",
    "hrv": "hr",
    "hun": "hu",
    "isl": "is",
    "ita": "it",
    "jpn": "ja",
    "kat": "ka",
    "kaz": "kk",
    "kor": "ko",
    "lav": "lv",
    "lit": "lt",
    "mkd": "mk",
    "nld": "nl",
    "nor": "no",
    "pol": "pl",
    "por": "pt",
    "ron": "ro",
    "rus": "ru",
    "slk": "sk",
    "slv": "sl",
    "spa": "es",
    "srp": "sr",
    "swe": "sv",
    "tgk": "tg",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "urd": "ur",
    "vie": "vi",
    "zho": "zh",
}

# The reverse mapping, for the --languages filter.  Everything else in this
# repo speaks ISO 639-1 (directory names, encoding_gaps, check_test_data), so
# the CLI does too; only the index query needs 639-3.  A few 639-1 codes cover
# several 639-3 codes -- Malay is a macrolanguage whose pages the crawl tags
# either "msa" or "zsm" -- so this maps to a tuple.
ISO1_TO_ISO3: dict[str, tuple[str, ...]] = {}
for _iso3, _iso1 in ISO3_TO_ISO1.items():
    ISO1_TO_ISO3.setdefault(_iso1, ())
    ISO1_TO_ISO3[_iso1] = (*ISO1_TO_ISO3[_iso1], _iso3)
ISO1_TO_ISO3["no"] = ("nor", "nob", "nno")
ISO1_TO_ISO3["sr"] = ("srp", "hbs")

MANIFEST_COLUMNS = (
    "path",
    "verdict",
    "charset_index",
    "codec",
    "charset_header",
    "charset_meta",
    "languages",
    "suggested_dir",
    "size",
    "decode_ok",
    "junk_fraction",
    "non_ascii_bytes",
    "script_fraction",
    "utf8_mislabel",
    "detected",
    "detected_confidence",
    "detected_language",
    "detector_agrees",
    "url",
)


@dataclass
class Hit:
    """One matching row from the columnar index."""

    charset_index: str
    url: str
    languages: str
    digest: str
    warc_filename: str
    warc_offset: int
    warc_length: int


@dataclass
class Candidate:
    """A fetched and validated page."""

    hit: Hit
    codec: str
    charset_header: str | None
    charset_meta: str | None
    size: int
    decode_ok: bool
    junk_fraction: float
    non_ascii_bytes: int
    script_fraction: float | None
    utf8_mislabel: bool
    detected: str | None
    detected_confidence: float
    detected_language: str | None
    detector_agrees: bool
    verdict: str
    path: Path | None = None
    body: bytes = field(default=b"", repr=False)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def canonical_codec(name: str | None) -> str | None:
    """Resolve a charset spelling to a Python codec name, or None."""
    if not name:
        return None
    lowered = name.strip().lower()
    lowered = INDEX_CHARSET_ALIASES.get(lowered, lowered)
    try:
        return codecs.lookup(lowered).name
    except (LookupError, ValueError):
        return None


def same_codec(left: str | None, right: str | None) -> bool:
    """Whether two charset names resolve to the same Python codec."""
    if not left or not right:
        return False
    return canonical_codec(left) == canonical_codec(right)


def script_of(codec: str | None) -> str | None:
    """Home script family of a codec, if it has one."""
    if codec is None:
        return None
    canonical = canonical_codec(codec) or codec
    return ENCODING_SCRIPT.get(canonical) or ENCODING_SCRIPT.get(codec)


def script_fraction(codec: str, text: str) -> float | None:
    """Fraction of non-ASCII chars inside the codec's home script.

    Returns ``None`` when the codec has no script expectation or the text
    has too few non-ASCII characters to judge.
    """
    script = script_of(codec)
    if script is None:
        return None
    non_ascii = [ch for ch in text if ch > "\x7f"]
    if len(non_ascii) < 20:
        return None
    ranges = SCRIPT_RANGES[script]
    inside = sum(1 for ch in non_ascii if any(lo <= ch <= hi for lo, hi in ranges))
    return inside / len(non_ascii)


def junk_fraction(text: str) -> float:
    """Fraction of C1 controls and replacement characters in decoded text."""
    if not text:
        return 1.0
    junk = sum(1 for ch in text if "\x80" <= ch <= "\x9f" or ch == "�")
    return junk / len(text)


def is_utf8_mislabel(body: bytes, codec: str) -> bool:
    """Whether a non-UTF-8-declared page is valid multi-byte UTF-8."""
    if codec in {"utf-8", "ascii"}:
        return False
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return any(byte > 0x7F for byte in body)


def visible_text(text: str) -> str:
    """Strip markup, leaving the words a reader would see."""
    return " ".join(_TAGS.sub(" ", text).split())


def _load_chardet():  # noqa: ANN202
    """Import chardet if available; None otherwise (it stays optional)."""
    try:
        import chardet  # noqa: PLC0415
    except ImportError:
        return None
    return chardet


# ---------------------------------------------------------------------------
# HTTP / WARC
# ---------------------------------------------------------------------------


def http_get(url: str, *, byte_range: tuple[int, int] | None = None) -> bytes:
    """Fetch *url* (https only), optionally with a byte range."""
    if not url.startswith("https://"):
        msg = f"refusing non-https URL: {url}"
        raise ValueError(msg)
    headers = {"User-Agent": "chardet-test-data-miner/0.1"}
    if byte_range is not None:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return response.read()


def load_index_part_urls(crawl: str) -> list[str]:
    """Return URLs of the columnar index's warc-subset Parquet parts."""
    listing = gzip.decompress(
        http_get(f"{CC_BASE}crawl-data/{crawl}/cc-index-table.paths.gz")
    )
    return [
        CC_BASE + line for line in listing.decode().splitlines() if "/subset=warc/" in line
    ]


def select_index_parts(part_urls: list[str], count: int, offset: int) -> list[str]:
    """Spread the selection across the crawl to avoid TLD/domain bias.

    The index is sorted by ``url_surtkey``, so consecutive parts cover one
    alphabetical slice of reversed domain names.
    """
    stride = max(1, len(part_urls) // max(1, count))
    return part_urls[offset::stride][:count]


def connect_duckdb():  # noqa: ANN201
    """Open a DuckDB connection able to read remote Parquet."""
    try:
        import duckdb  # noqa: PLC0415
    except ImportError:
        sys.exit(
            "duckdb is required for this mode.\n"
            "  pip install duckdb   (or: uv run --with duckdb python3 ...)"
        )
    connection = duckdb.connect()
    connection.execute("INSTALL httpfs; LOAD httpfs;")
    # Sustained scanning earns 503s from data.commoncrawl.org.  Retry with
    # a long backoff rather than losing a multi-minute scan to one blip.
    for setting, value in (
        ("http_retries", "8"),
        ("http_retry_backoff", "4"),
        ("http_timeout", "120000"),
        # Scanning several remote Parquet parts at once will happily eat
        # all of RAM and get the process OOM-killed mid-scan.  Bound it:
        # the query is IO-bound on the crawl's servers anyway, so a small
        # memory ceiling and thread count cost almost nothing.
        ("memory_limit", "'2GB'"),
        ("threads", "3"),
        ("preserve_insertion_order", "false"),
    ):
        try:
            connection.execute(f"SET {setting} = {value}")
        except Exception:  # noqa: BLE001, S110 - older duckdb lacks some knobs
            pass
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        connection.execute("SET http_proxy = ?", [proxy.split("//", 1)[-1]])
    return connection


def split_warc_record(record: bytes) -> bytes:
    """Return the HTTP response bytes inside a decompressed WARC record."""
    header_end = record.find(b"\r\n\r\n")
    if header_end < 0 or not record.startswith(b"WARC/"):
        msg = "malformed WARC record"
        raise ValueError(msg)
    return record[header_end + 4 :]


def _dechunk(body: bytes) -> bytes:
    chunks = []
    position = 0
    while True:
        line_end = body.find(b"\r\n", position)
        if line_end < 0:
            break
        try:
            size = int(body[position:line_end].split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        chunks.append(body[line_end + 2 : line_end + 2 + size])
        position = line_end + 2 + size + 2
    return b"".join(chunks) if chunks else body


def split_http_response(http_bytes: bytes) -> tuple[dict[str, str], bytes]:
    """Split an HTTP response into (lowercased headers, decoded body)."""
    header_end = http_bytes.find(b"\r\n\r\n")
    if header_end < 0:
        msg = "malformed HTTP response in WARC record"
        raise ValueError(msg)
    header_lines = http_bytes[:header_end].decode("latin-1").split("\r\n")[1:]
    headers: dict[str, str] = {}
    for line in header_lines:
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    body = http_bytes[header_end + 4 :]
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _dechunk(body)
    content_encoding = headers.get("content-encoding", "").lower()
    if content_encoding in {"gzip", "x-gzip"}:
        body = gzip.decompress(body)
    elif content_encoding == "deflate":
        body = zlib.decompress(body, -zlib.MAX_WBITS)
    return headers, body


def charset_from_header(headers: dict[str, str]) -> str | None:
    """Extract the charset parameter from a Content-Type header."""
    match = _CHARSET_IN_CONTENT_TYPE.search(headers.get("content-type", ""))
    return match.group(1).lower() if match else None


def charset_from_meta(body: bytes) -> str | None:
    """Extract a charset declared in an HTML meta tag (first 4 KiB)."""
    match = _META_CHARSET.search(body[:4096])
    return match.group(1).decode("latin-1").lower() if match else None


# ---------------------------------------------------------------------------
# Index queries
# ---------------------------------------------------------------------------


def print_charset_stats(part_urls: list[str]) -> None:
    """Print the declared-charset distribution for the given index parts."""
    rows = (
        connect_duckdb()
        .execute(
            """
            SELECT lower(content_charset) AS charset, count(*) AS pages
            FROM read_parquet(?)
            WHERE fetch_status = 200 AND content_charset IS NOT NULL
            GROUP BY 1
            ORDER BY 2 DESC
            """,
            [part_urls],
        )
        .fetchall()
    )
    total = sum(count for _, count in rows)
    print(f"{total:,} pages with a charset across {len(part_urls)} index part(s)")
    print(f"{'charset':<24} {'pages':>10}  {'codec':<16}")
    for charset, count in rows:
        codec = canonical_codec(charset) or "-- UNSUPPORTED --"
        print(f"{charset:<24} {count:>10,}  {codec:<16}")


def query_hits(
    part_urls: list[str],
    targets: tuple[str, ...],
    per_charset: int,
    languages: tuple[str, ...] = (),
) -> list[Hit]:
    """Query the index parts for target charsets, capped per charset.

    *languages* optionally restricts to pages the crawl tagged with one of
    the given ISO 639-3 codes, and partitions the per-charset cap by
    language.  Without it, mining a common charset for a rare language is
    hopeless: iso-8859-1 alone has 629k pages in a single index part, and
    the Welsh ones would never surface.

    The cap counts *distinct domains*, not rows.  Ranking rows by
    url_surtkey and taking the first N returns the alphabetically-first
    pages, which cluster on a handful of hosts -- a 24-part scan of
    windows-1252 yielded 2 usable candidates out of 72k pages per part
    because nearly all of the top-ranked rows shared one domain.
    """
    language_filter = ""
    if languages:
        # content_languages is a comma-separated list, most confident first.
        language_filter = (
            "AND list_has_any(str_split(content_languages, ','), "
            "?::varchar[]) "
        )
    partition = (
        "lower(content_charset), coalesce(content_languages, '')"
        if languages
        else "lower(content_charset)"
    )
    parameters: list[object] = [part_urls]
    if languages:
        parameters.append(list(languages))
    parameters.append(per_charset * 3)

    rows = (
        connect_duckdb()
        .execute(
            f"""
            SELECT charset_index, url, languages, digest,
                   warc_filename, warc_offset, warc_length
            FROM (
                SELECT lower(content_charset) AS charset_index,
                       url,
                       coalesce(content_languages, '') AS languages,
                       content_digest AS digest,
                       warc_filename,
                       warc_record_offset AS warc_offset,
                       warc_record_length AS warc_length,
                       row_number() OVER (
                           PARTITION BY lower(content_charset),
                                        url_host_registered_domain
                           ORDER BY url_surtkey
                       ) AS per_domain,
                       dense_rank() OVER (
                           PARTITION BY {partition}
                           ORDER BY url_host_registered_domain
                       ) AS domain_rank
                FROM read_parquet(?)
                WHERE fetch_status = 200
                  AND lower(content_charset) IN (SELECT unnest(?::varchar[]))
                  {language_filter}
                  AND content_mime_detected IN ('text/html', 'application/xhtml+xml')
            )
            WHERE per_domain <= 2 AND domain_rank <= ?
            """,  # noqa: S608 - fragments are literals chosen above, not input
            [part_urls, list(targets), *parameters[1:]],
        )
        .fetchall()
    )
    return [Hit(*row) for row in rows]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def classify(candidate: Candidate) -> str:
    """Assign one verdict describing what the evidence supports.

    The ladder is ordered so that each rung rules out a distinct way the
    declared charset can be untrustworthy, strongest evidence first.  In
    particular ``vacuous`` and ``sparse`` are *not* accusations: they mean
    the page carries too little non-ASCII content to confirm or refute its
    header, which is the normal shape of a mostly-markup page whose
    encoded text is a small subsection.
    """
    body = candidate.body
    codec = candidate.codec
    # ISO-2022/HZ put their signal in escape sequences, not high bytes --
    # but only when those sequences actually occur.
    escape_based = codec.startswith(("iso2022", "hz"))
    has_escapes = b"\x1b$" in body or b"\x1b(" in body or b"~{" in body
    has_signal = (
        has_escapes if escape_based else candidate.non_ascii_bytes >= MIN_NON_ASCII
    )
    meta_codec = canonical_codec(candidate.charset_meta)
    meta_conflict = meta_codec is not None and meta_codec != codec

    if not has_signal and candidate.non_ascii_bytes == 0:
        # For a page declared us-ascii, carrying no high bytes is the
        # claim being confirmed rather than a missing signal.  Every other
        # encoding is untestable in that state.
        if codec == "ascii" and candidate.decode_ok:
            return "strong"
        return "vacuous"
    if candidate.utf8_mislabel:
        return "utf8-mislabeled"
    if not candidate.decode_ok or candidate.junk_fraction > MAX_JUNK_FRACTION:
        return "mojibake"
    if (
        candidate.script_fraction is not None
        and candidate.script_fraction < MIN_SCRIPT_FRACTION
    ):
        return "wrong-legacy"
    if not has_signal:
        return "sparse"
    if meta_conflict:
        return "review"
    if candidate.detected and not candidate.detector_agrees:
        detected_codec = canonical_codec(candidate.detected)
        if detected_codec:
            # A single-byte codec decodes any byte soup into plausible
            # letters, so passing every check above still cannot rule out
            # that the page is really some other encoding.  If the
            # detector's choice also decodes and yields *different* text,
            # there are two live readings and a human has to choose.
            # Supersets and near-twins (gb2312/gb18030, cp874/tis-620)
            # decode to identical text -- those are not disputes.
            try:
                alternative = body.decode(detected_codec)
                declared_text = body.decode(codec)
            except (UnicodeDecodeError, LookupError):
                pass
            else:
                if alternative != declared_text:
                    return "ambiguous"
    return "strong"


def suggested_dir(codec: str, languages: str) -> str:
    """Suggest a ``{encoding}-{language}`` destination directory name."""
    first = languages.split(",")[0].strip().lower() if languages else ""
    iso1 = ISO3_TO_ISO1.get(first, "")
    return f"{codec}-{iso1}" if iso1 else codec


def refine_by_bom(codec: str, body: bytes) -> str:
    """Resolve BOM-distinguished codecs from the bytes themselves.

    The index reports a charset name, but utf-8 vs utf-8-sig, and utf-16
    vs utf-16le/be, are distinctions about a byte-order mark that a charset
    label cannot carry.  This repo keeps them in separate directories, so
    the bytes have to decide.
    """
    if codec == "utf-8" and body.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if codec in {"utf-16-le", "utf-16-be"} and body[:2] in (
        codecs.BOM_UTF16_LE,
        codecs.BOM_UTF16_BE,
    ):
        return "utf-16"
    if codec == "utf-16" and body[:2] not in (
        codecs.BOM_UTF16_LE,
        codecs.BOM_UTF16_BE,
    ):
        return "utf-16-be" if body[:1] == b"\x00" else "utf-16-le"
    return codec


def validate_candidate(hit: Hit, body: bytes, chardet_module) -> Candidate | None:  # noqa: ANN001
    """Run every validation signal for one fetched page."""
    codec = canonical_codec(hit.charset_index)
    if codec is None:
        print(f"  unsupported charset {hit.charset_index!r}: {hit.url[:60]}")
        return None
    codec = refine_by_bom(codec, body)

    fraction: float | None = None
    try:
        decoded = body.decode(codec)
    except (UnicodeDecodeError, LookupError):
        decode_ok = False
        junk = 1.0
    else:
        decode_ok = True
        junk = junk_fraction(decoded)
        fraction = script_fraction(codec, decoded)

    detected = None
    confidence = 0.0
    language = None
    if chardet_module is not None:
        result = chardet_module.detect(body)
        detected = result["encoding"]
        confidence = result["confidence"] or 0.0
        language = result["language"]

    candidate = Candidate(
        hit=hit,
        codec=codec,
        charset_header=None,
        charset_meta=charset_from_meta(body),
        size=len(body),
        decode_ok=decode_ok,
        junk_fraction=junk,
        non_ascii_bytes=sum(1 for byte in body if byte > 0x7F),
        script_fraction=fraction,
        utf8_mislabel=is_utf8_mislabel(body, codec),
        detected=detected,
        detected_confidence=confidence,
        detected_language=language,
        detector_agrees=same_codec(codec, detected),
        verdict="",
        body=body,
    )
    candidate.verdict = classify(candidate)
    return candidate


def fetch_and_validate(hit: Hit, chardet_module) -> Candidate | None:  # noqa: ANN001
    """Fetch one WARC record and validate its payload."""
    record = gzip.decompress(
        http_get(
            CC_BASE + hit.warc_filename,
            byte_range=(hit.warc_offset, hit.warc_offset + hit.warc_length - 1),
        )
    )
    headers, body = split_http_response(split_warc_record(record))
    if not MIN_BODY_BYTES <= len(body) <= MAX_BODY_BYTES:
        return None
    candidate = validate_candidate(hit, body, chardet_module)
    if candidate is not None:
        candidate.charset_header = charset_from_header(headers)
    return candidate


def write_candidates(candidates: list[Candidate], output_dir: Path) -> None:
    """Write candidate files and the review manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.writer(manifest_file)
        writer.writerow(MANIFEST_COLUMNS)
        for candidate in candidates:
            host = candidate.hit.url.split("/")[2].replace(":", "_")
            digest = hashlib.sha256(candidate.body).hexdigest()[:12]
            directory = output_dir / candidate.codec
            directory.mkdir(exist_ok=True)
            path = directory / f"cc_{host}_{digest}.html"
            path.write_bytes(candidate.body)
            candidate.path = path
            writer.writerow(
                [
                    path.relative_to(output_dir),
                    candidate.verdict,
                    candidate.hit.charset_index,
                    candidate.codec,
                    candidate.charset_header or "",
                    candidate.charset_meta or "",
                    candidate.hit.languages,
                    suggested_dir(candidate.codec, candidate.hit.languages),
                    candidate.size,
                    candidate.decode_ok,
                    f"{candidate.junk_fraction:.5f}",
                    candidate.non_ascii_bytes,
                    ""
                    if candidate.script_fraction is None
                    else f"{candidate.script_fraction:.3f}",
                    candidate.utf8_mislabel,
                    candidate.detected or "",
                    f"{candidate.detected_confidence:.3f}",
                    candidate.detected_language or "",
                    candidate.detector_agrees,
                    candidate.hit.url,
                ]
            )
    print(f"\nWrote {len(candidates)} candidates and manifest to {output_dir}")


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


@dataclass
class Reading:
    """One candidate decoding of a disputed page."""

    codecs: list[str]
    script: str | None
    letter_ratio: float
    case_noise: float
    score: float
    sample: str
    origin: str  # "declared", "detected", or "pool"


# Real Unicode letters that real text is nonetheless almost never encoded
# in: halfwidth katakana, Arabic presentation forms, and the private use
# area are all classic mojibake destinations.
_COMPAT_RANGES: tuple[tuple[str, str], ...] = (
    ("｡", "ﾟ"),  # halfwidth katakana
    ("ﭐ", "﷿"),  # Arabic presentation forms-A
    ("ﹰ", "﻿"),  # Arabic presentation forms-B
    ("", ""),  # private use area
)


def _is_text_char(ch: str) -> bool:
    """Whether a character is the kind real prose is made of."""
    category = unicodedata.category(ch)
    if not (category.startswith(("L", "M"))):
        return False
    return not any(low <= ch <= high for low, high in _COMPAT_RANGES)


def text_plausibility(text: str) -> tuple[float, float]:
    """Return (letter_ratio, case_noise) for decoded non-ASCII text.

    These are the two signals that discriminate a real reading from
    mojibake without needing frequency models:

    - *letter_ratio*: the fraction of non-ASCII characters that are
      letters or combining marks, excluding compatibility forms.  Legacy
      bytes read through the wrong codec often land on box-drawing and
      symbol characters (the ``╬─╝■├√`` signature of CJK read as a DOS
      codepage) or on halfwidth/presentation forms.  Combining marks
      count as text: Thai vowel signs and Arabic diacritics are ordinary
      content, and excluding them would penalize the correct reading of
      exactly the scripts this repo is thinnest on.
    - *case_noise*: the fraction of words with impossible casing.  Real
      prose capitalizes the first letter, all letters, or none; Arabic
      read as Cyrillic yields words like ``ЗдЕПЗСЙ`` that alternate case
      mid-word, and Russian read as KOI8-R yields ``тЕДЕПЮКЭМШЕ`` --
      lowercase first, uppercase rest.  Unicameral scripts (Arabic,
      Thai, CJK, Hebrew) score 0, which is correct: case says nothing
      about them either way.

    Deliberately *not* used: the fraction of characters inside the
    codec's own script.  That is tautological for single-byte codecs --
    every byte maps to some letter of that codec's script, so it reads
    1.00 for the wrong answer just as readily as the right one.
    """
    non_ascii = [ch for ch in text if ch > "\x7f"]
    if not non_ascii:
        return (0.0, 1.0)
    letter_ratio = sum(1 for ch in non_ascii if _is_text_char(ch)) / len(non_ascii)

    words = [word for word in text.split() if sum(1 for c in word if c > "\x7f") > 1]
    if not words:
        return (letter_ratio, 0.0)
    noisy = 0
    for word in words:
        cased = [ch for ch in word if ch.isupper() or ch.islower()]
        if len(cased) < 2:
            continue
        rest = cased[1:]
        mixed_tail = any(ch.isupper() for ch in rest) and any(
            ch.islower() for ch in rest
        )
        lower_then_upper = cased[0].islower() and any(ch.isupper() for ch in rest)
        if mixed_tail or lower_then_upper:
            noisy += 1
    return (letter_ratio, noisy / len(words))


def candidate_readings(body: bytes, declared: str, detected: str | None) -> list[Reading]:
    """Every distinct strict decoding of *body*, most plausible first.

    The declared and detected encodings are always included when they
    decode; the pool fills in alternatives neither the page nor the
    detector proposed -- which is how the ISO-8859-6 Arabic pages in the
    first mining run were identified, having been declared
    ``x-maccyrillic`` and detected as ``windows-1250``.

    Codecs producing byte-identical text are merged into one reading, so
    near-twins like cp874/tis-620 do not crowd out real alternatives.
    The ranking is a heuristic to order the options; the decision comes
    from a human (or an LLM) reading the samples.
    """
    origins: dict[str, str] = {}
    for codec in ADJUDICATION_POOL:
        origins.setdefault(canonical_codec(codec) or codec, "pool")
    declared_codec = canonical_codec(declared) or declared
    origins[declared_codec] = "declared"
    if detected:
        detected_codec = canonical_codec(detected)
        if detected_codec:
            origins[detected_codec] = (
                "declared+detected" if detected_codec == declared_codec else "detected"
            )

    by_text: dict[str, Reading] = {}
    for codec, origin in origins.items():
        try:
            text = body.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        visible = visible_text(text)
        non_ascii_words = [w for w in visible.split() if any(c > "\x7f" for c in w)]
        if not non_ascii_words:
            continue
        sample = " ".join(non_ascii_words)

        existing = by_text.get(sample)
        if existing is not None:
            existing.codecs.append(codec)
            # Keep the strongest provenance among the twins.
            if origin != "pool" and existing.origin == "pool":
                existing.origin = origin
                existing.script = script_of(codec)
            continue

        letter_ratio, case_noise = text_plausibility(sample)
        by_text[sample] = Reading(
            codecs=[codec],
            script=script_of(codec),
            letter_ratio=letter_ratio,
            case_noise=case_noise,
            score=letter_ratio * (1.0 - case_noise),
            sample=sample,
            origin=origin,
        )

    readings = list(by_text.values())
    # Ties (identical scores) resolve toward the encoding the page or the
    # detector named, so we never rename a file gratuitously.
    origin_rank = {"declared+detected": 0, "detected": 1, "declared": 1, "pool": 2}
    readings.sort(key=lambda r: (-r.score, origin_rank.get(r.origin, 3)))
    return readings


def adjudicate(
    output_dir: Path, verdicts: set[str], limit: int, sample_chars: int
) -> int:
    """Print competing readings of candidates needing a human ruling."""
    manifest_path = output_dir / "manifest.csv"
    if not manifest_path.is_file():
        sys.exit(f"no manifest at {manifest_path} -- run the 'mine' mode first")

    with manifest_path.open(encoding="utf-8") as manifest_file:
        rows = [
            row
            for row in csv.DictReader(manifest_file)
            if "all" in verdicts or row["verdict"] in verdicts
        ]
    if not rows:
        print(f"No candidates in {output_dir} with verdict(s): {', '.join(verdicts)}")
        return 0

    print(f"{len(rows)} candidate(s) to adjudicate in {output_dir}\n")
    print(
        "For each page: every encoding that strict-decodes it, ranked by how\n"
        "much of the decoded text lands in that encoding's home script.  The\n"
        "reading that yields real words in a real language is the truth --\n"
        "the declared charset frequently is not.\n"
    )
    for row in rows:
        body = (output_dir / row["path"]).read_bytes()
        print("=" * 78)
        print(f"{row['path']}  [{row['verdict']}]")
        print(f"  url:      {row['url'][:100]}")
        print(
            f"  declared: {row['charset_index']} ({row['codec']})"
            f"   meta: {row['charset_meta'] or '-'}"
            f"   detected: {row['detected'] or '-'}"
            f"   cc_langs: {row['languages'] or '-'}"
        )
        readings = candidate_readings(body, row["codec"], row["detected"])
        if not readings:
            print("  (no encoding decodes this page with non-ASCII content)")
            continue
        for reading in readings[:limit]:
            names = ", ".join(sorted(reading.codecs)[:3])
            print(
                f"  [{names:<24} {reading.origin:<17} "
                f"script={reading.script or '-':<9} "
                f"score={reading.score:4.2f} "
                f"letters={reading.letter_ratio:4.2f} "
                f"case-noise={reading.case_noise:4.2f}]"
            )
            print(f"      {reading.sample[:sample_chars]}")
        print()
    return 0


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def run_mine(arguments: argparse.Namespace) -> int:
    """Query the index, fetch matching records, and validate them."""
    chardet_module = _load_chardet()
    if chardet_module is None:
        print(
            "note: chardet not importable -- detector columns will be empty.\n"
            "      run under the chardet checkout to include its verdict:\n"
            "      uv run --project ../chardet --with duckdb python3 "
            "scripts/mine_common_crawl.py mine\n"
        )

    part_urls = load_index_part_urls(arguments.crawl)
    selected = select_index_parts(part_urls, arguments.parts, arguments.part_offset)
    print(
        f"Crawl {arguments.crawl}: scanning {len(selected)} of "
        f"{len(part_urls)} index parts"
    )

    targets = tuple(value.strip() for value in arguments.charsets.split(","))
    unsupported = [target for target in targets if canonical_codec(target) is None]
    if unsupported:
        print(f"skipping charsets Python cannot decode: {', '.join(unsupported)}")
        targets = tuple(t for t in targets if canonical_codec(t) is not None)

    requested = [
        value.strip() for value in arguments.languages.split(",") if value.strip()
    ]
    unknown = [code for code in requested if code not in ISO1_TO_ISO3]
    if unknown:
        sys.exit(
            f"unknown language code(s): {', '.join(unknown)}\n"
            f"  use ISO 639-1, e.g. cy,ga,br (not cym,gle,bre)"
        )
    languages = tuple(code for iso1 in requested for code in ISO1_TO_ISO3[iso1])
    if languages:
        print(
            f"restricting to languages: {', '.join(requested)} "
            f"(index codes: {', '.join(languages)})"
        )
    try:
        hits = query_hits(selected, targets, arguments.max_per_charset, languages)
    except Exception as error:  # noqa: BLE001 - duckdb wraps HTTP failures
        if "503" in str(error) or "Service Unavailable" in str(error):
            sys.exit(
                "data.commoncrawl.org returned 503 -- sustained scanning gets "
                "rate limited.\n  Wait a few minutes, or lower --parts."
            )
        raise
    by_charset: dict[str, int] = {}
    for hit in hits:
        by_charset[hit.charset_index] = by_charset.get(hit.charset_index, 0) + 1
    print(f"Index hits: {len(hits)}")
    for charset, count in sorted(by_charset.items()):
        print(f"  {charset:<20} {count}")
    if arguments.dry_run:
        print("\n--dry-run: no WARC records fetched")
        return 0

    candidates: list[Candidate] = []
    kept: dict[str, int] = {}
    seen_digests: set[str] = set()
    for hit in hits:
        if kept.get(hit.charset_index, 0) >= arguments.max_per_charset:
            continue
        if hit.digest in seen_digests:
            continue
        seen_digests.add(hit.digest)
        try:
            candidate = fetch_and_validate(hit, chardet_module)
        except (urllib.error.URLError, ValueError, OSError) as error:
            print(f"  fetch failed for {hit.url}: {error}")
            continue
        if candidate is None:
            continue
        candidates.append(candidate)
        kept[hit.charset_index] = kept.get(hit.charset_index, 0) + 1
        time.sleep(0.1)

    by_verdict: dict[str, int] = {}
    for candidate in candidates:
        by_verdict[candidate.verdict] = by_verdict.get(candidate.verdict, 0) + 1
    print(f"\nFetched {len(candidates)} candidates:")
    for verdict, count in sorted(by_verdict.items(), key=lambda kv: -kv[1]):
        print(f"  {verdict:<16} {count}")

    strong = [c for c in candidates if c.verdict == "strong"]
    disagreements = [c for c in strong if c.detected and not c.detector_agrees]
    if disagreements:
        print(f"Detector disagrees on {len(disagreements)} strong candidates:")
        for candidate in disagreements:
            print(
                f"  {candidate.codec:<14} detected as "
                f"{candidate.detected or 'None':<14} {candidate.hit.url[:70]}"
            )

    write_candidates(candidates, arguments.output)
    ambiguous = by_verdict.get("ambiguous", 0)
    if ambiguous:
        print(
            f"\n{ambiguous} candidate(s) need adjudication:\n"
            f"  python3 scripts/mine_common_crawl.py adjudicate --output {arguments.output}"
        )
    return 0


def run_stats(arguments: argparse.Namespace) -> int:
    """Print the declared-charset distribution of a crawl slice."""
    part_urls = load_index_part_urls(arguments.crawl)
    selected = select_index_parts(part_urls, arguments.parts, arguments.part_offset)
    print(
        f"Crawl {arguments.crawl}: scanning {len(selected)} of "
        f"{len(part_urls)} index parts"
    )
    print_charset_stats(selected)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine Common Crawl for pages served in rare legacy charsets.",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="mine",
        choices=("mine", "stats", "adjudicate"),
        help="mine (default): fetch and validate; stats: charset distribution; "
        "adjudicate: show competing readings of disputed candidates",
    )
    parser.add_argument("--crawl", default=DEFAULT_CRAWL, help="crawl id to query")
    parser.add_argument("--parts", type=int, default=1, help="index parts to scan")
    parser.add_argument("--part-offset", type=int, default=0)
    parser.add_argument("--max-per-charset", type=int, default=10)
    parser.add_argument(
        "--charsets",
        default=",".join(DEFAULT_TARGETS),
        help="comma-separated index charset values to harvest",
    )
    parser.add_argument(
        "--languages",
        default="",
        help="comma-separated ISO 639-1 codes to restrict to (e.g. cy,ga,br); "
        "needed to find a rare language inside a common charset",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"candidate directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="mine: query the index but fetch nothing",
    )
    parser.add_argument(
        "--verdict",
        default="ambiguous",
        help="adjudicate: comma-separated verdicts to show, or 'all' "
        "(default: ambiguous)",
    )
    parser.add_argument(
        "--max-readings",
        type=int,
        default=4,
        help="adjudicate: readings to show per candidate (default: 4)",
    )
    parser.add_argument(
        "--sample-chars",
        type=int,
        default=280,
        help="adjudicate: sample length per reading (default: 280)",
    )
    arguments = parser.parse_args()

    if arguments.mode == "adjudicate":
        verdicts = {v.strip() for v in arguments.verdict.split(",")}
        return adjudicate(
            arguments.output, verdicts, arguments.max_readings, arguments.sample_chars
        )
    if arguments.mode == "stats":
        return run_stats(arguments)
    return run_mine(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
