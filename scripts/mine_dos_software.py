#!/usr/bin/env python3
"""Mine wild DOS-codepage text from archive.org software items.

The DOS codepages are the last family with a plausible wild corpus and no
route to it.  Common Crawl cannot help -- in 2019 an ibm850 label is
nearly always a misconfigured server -- but archive.org holds tens of
thousands of original DOS programs, each a zip of the files as shipped,
READMEs included.

The hard part is not finding text, it is proving *which* codepage it is.
Sibling DOS codepages overlap almost completely in the accented range:
cp437 and cp850 place the German umlauts at identical positions, so a
German README is equally valid under either and proves neither.  A
candidate is therefore only accepted when it contains a byte that decodes
differently under its claimed codepage than under every other single-byte
encoding this repo knows -- the same guard the generator uses, from
encoding_overlaps.py.

Everything else is the discipline the other miners converged on: real
prose rather than binary wearing a text extension, not valid UTF-8, no
control characters, and no mojibake casing.

Dependencies: the standard library.

Usage::

    python3 scripts/mine_dos_software.py --codec cp850 --language de \\
        --query "deutsch OR german" --items 40
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collections import defaultdict  # noqa: E402

from encoding_overlaps import DISTINGUISHING_BYTES  # noqa: E402

DEFAULT_OUTPUT = Path("scripts/.cache/wild-dos")
TEXT_SUFFIXES = (
    ".txt", ".doc", ".nfo", ".diz", ".me", ".1st", ".now", ".asc", ".dok",
    ".hlp", ".ans", ".readme", ".lis",
)

MIN_SIZE = 400
MAX_SIZE = 200 * 1024
MIN_LETTERS = 200
MIN_HIGH_BYTES = 20
MAX_HIGH_FRACTION = 0.45
MAX_CASE_NOISE = 0.25

# Letters characteristic of each language, used to check that the text is
# actually in the language being claimed.  --language is an assertion by
# the caller; without this a Spanish README passed as Polish cp852 purely
# because its accented letters happened to be cp852-distinguishing.
LANGUAGE_LETTERS: dict[str, str] = {
    "pl": "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ",
    "cs": "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ",
    "sk": "áäčďéíĺľňóôŕšťúýžÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ",
    "hu": "áéíóöőúüűÁÉÍÓÖŐÚÜŰ",
    "ro": "ăâîșşțţĂÂÎȘŞȚŢ",
    "de": "äöüßÄÖÜ",
    "da": "æøåÆØÅ",
    "no": "æøåÆØÅ",
    "sv": "åäöÅÄÖ",
    "pt": "ãáàâçéêíóôõúÃÁÀÂÇÉÊÍÓÔÕÚ",
    "es": "áéíóúñüÁÉÍÓÚÑÜ",
    "fr": "àâçéèêëîïôùûüÀÂÇÉÈÊËÎÏÔÙÛÜ",
    "tr": "çğıöşüÇĞİÖŞÜ",
    "el": "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ",
    "he": "אבגדהוזחטיכלמנסעפצקרשת",
    "ru": "абвгдежзийклмнопрстуфхцчшщыьэюяАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЫЬЭЮЯ",
    "lt": "ąčęėįšųūžĄČĘĖĮŠŲŪŽ",
    "lv": "āčēģīķļņšūžĀČĒĢĪĶĻŅŠŪŽ",
    "is": "áðéíóúýþæöÁÐÉÍÓÚÝÞÆÖ",
}
MIN_LANGUAGE_LETTERS = 8

MANIFEST_COLUMNS = (
    "path", "item", "member", "codec", "language", "size", "letters",
    "high_bytes", "unique_bytes", "detected", "status",
)


def _load_chardet():  # noqa: ANN202
    try:
        import chardet  # noqa: PLC0415
    except ImportError:
        return None
    return chardet


def http_get(url: str, timeout: int = 240) -> bytes:
    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "chardet-test-data-miner/0.1"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def search_items(query: str, rows: int) -> list[str]:
    encoded = urllib.parse.quote(f"mediatype:software AND ({query})")
    url = (
        f"https://archive.org/advancedsearch.php?q={encoded}"
        f"&fl%5B%5D=identifier&rows={rows}&output=json"
    )
    payload = json.loads(http_get(url, timeout=90))
    return [doc["identifier"] for doc in payload["response"]["docs"]]


IMAGE_SUFFIXES = (".img", ".ima", ".dsk", ".vfd")


def item_archive_url(item: str) -> tuple[str, str] | None:
    """Return (url, kind) for an item's payload, kind being zip or image.

    The shareware collection ships zips of the files as distributed, but
    the games collection ships raw floppy images -- which is where most of
    the non-English software is, and therefore most of the codepages worth
    having.
    """
    payload = json.loads(http_get(f"https://archive.org/metadata/{item}", timeout=90))
    images = []
    for entry in payload.get("files", []):
        name = entry["name"].lower()
        size = int(entry.get("size", 0))
        if size > 40_000_000:
            continue
        url = (
            f"https://archive.org/download/{item}/"
            f"{urllib.parse.quote(entry['name'])}"
        )
        if name.endswith(".zip"):
            return url, "zip"
        if name.endswith(IMAGE_SUFFIXES):
            images.append(url)
    return (images[0], "image") if images else None


def image_members(blob: bytes) -> list[tuple[str, bytes]]:
    """Every file inside a FAT floppy image, as (path, data)."""
    try:
        from pyfatfs.PyFatFS import PyFatFS  # noqa: PLC0415
    except ImportError:
        print("  (pyfatfs not installed -- skipping disk images)")
        return []
    import tempfile  # noqa: PLC0415

    found: list[tuple[str, bytes]] = []
    with tempfile.NamedTemporaryFile(suffix=".img") as handle:
        handle.write(blob)
        handle.flush()
        try:
            filesystem = PyFatFS(handle.name, read_only=True)
        except Exception:  # noqa: BLE001 - not a FAT image, or damaged
            return []
        try:
            for path in filesystem.walk.files():
                try:
                    found.append((path, filesystem.readbytes(path)))
                except Exception:  # noqa: BLE001, PERF203
                    continue
        finally:
            filesystem.close()
    return found


def case_noise(text: str) -> float:
    """Fraction of words with casing real prose does not produce."""
    words = [w for w in text.split() if sum(1 for c in w if c > "\x7f") > 1]
    if not words:
        return 0.0
    noisy = 0
    for word in words:
        cased = [ch for ch in word if ch.isupper() or ch.islower()]
        if len(cased) < 2:
            continue
        rest = cased[1:]
        mixed = any(c.isupper() for c in rest) and any(c.islower() for c in rest)
        if mixed or (cased[0].islower() and any(c.isupper() for c in rest)):
            noisy += 1
    return noisy / len(words)


def evaluate(data: bytes, codec: str, language: str = "") -> tuple[int, int, int] | None:
    """Return (letters, high bytes, unique bytes) for an accepted candidate."""
    if not MIN_SIZE <= len(data) <= MAX_SIZE or b"\x00" in data:
        return None
    high = sum(1 for b in data if b >= 0x80)
    if high < MIN_HIGH_BYTES or high > len(data) * MAX_HIGH_FRACTION:
        return None
    # Valid UTF-8 means it is a UTF-8 file whatever its extension says.
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return None
    try:
        text = data.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None
    # The byte must be one only this codepage claims, or the file is
    # equally good evidence for a sibling and therefore evidence for none.
    # It must also decode to a *letter*: DOS ASCII art is full of bytes
    # that are technically unique to a codepage but render as the same
    # block or box character everywhere, which proves nothing about the
    # language.  An English art file tripped this before the check existed.
    marks = DISTINGUISHING_BYTES.get(codec) or frozenset()
    unique = sum(
        1
        for b in sorted(set(data))
        if b in marks and bytes([b]).decode(codec, errors="replace").isalpha()
    )
    if not unique:
        return None
    # DOS text uses CR/LF and the occasional EOF marker; nothing else in C0.
    if any(ord(ch) < 0x20 and ch not in "\t\n\r\x1a" for ch in text):
        return None
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < MIN_LETTERS or case_noise(text) > MAX_CASE_NOISE:
        return None
    expected = LANGUAGE_LETTERS.get(language)
    if expected:
        hits = [ch for ch in text if ch in expected]
        if len(hits) < MIN_LANGUAGE_LETTERS:
            return None
        # Box-drawing runs decode to letters too, and in a Cyrillic
        # codepage they come out as a handful of repeated capitals -- an
        # English ASCII-art README scored as Kazakh on exactly that.  Real
        # prose is mostly lowercase, so require it.
        cased = [ch for ch in hits if ch.isupper() or ch.islower()]
        if cased and sum(1 for ch in cased if ch.islower()) < len(cased) * 0.4:
            return None
    return letters, high, unique


def zip_members(blob: bytes) -> list[tuple[str, bytes]]:
    """Every file inside a zip, as (path, data)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError, ValueError):
        return []
    found: list[tuple[str, bytes]] = []
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            try:
                found.append((info.filename, archive.read(info)))
            except (RuntimeError, zipfile.BadZipFile, OSError):
                continue
    return found


# Codepages worth testing every candidate against in broadcast mode.
BROADCAST_CODECS = (
    "cp437", "cp850", "cp852", "cp855", "cp857", "cp858", "cp860", "cp861",
    "cp862", "cp863", "cp864", "cp865", "cp866", "cp869", "cp737", "cp775",
    "cp720", "cp1125", "mac-roman", "mac-latin2", "mac-greek", "mac-iceland",
    "mac-turkish", "mac-cyrillic", "hp-roman8", "koi8-t", "kz1048",
    "ptcp154", "iso8859-10", "iso8859-14",
)


def best_match(data: bytes) -> tuple[str, str, int] | None:
    """Which (codec, language) this file is evidence for, if exactly one.

    Querying archive.org one codepage at a time depends on guessing the
    right keywords, and most DOS items are not tagged by language at all.
    Testing every candidate against every codepage instead lets one sweep
    serve all of them -- but only when the answer is unambiguous.  A file
    matching two codepages equally well is evidence for neither, so it is
    dropped rather than filed under whichever was tried first.
    """
    hits: list[tuple[str, str, int]] = []
    for codec in BROADCAST_CODECS:
        for language, letters in LANGUAGE_LETTERS.items():
            metrics = evaluate(data, codec, language)
            if metrics is None:
                continue
            score = sum(1 for ch in data.decode(codec) if ch in letters)
            hits.append((codec, language, score))
    if not hits:
        return None
    hits.sort(key=lambda h: -h[2])
    # Require a clear winner: a runner-up within 20% means the file does
    # not distinguish the two.
    if len(hits) > 1 and hits[1][2] > hits[0][2] * 0.8 and hits[1][0] != hits[0][0]:
        return None
    return hits[0]


def mine_item(item: str, codec: str, per_item: int, language: str = "") -> list[tuple]:
    try:
        resolved = item_archive_url(item)
        if resolved is None:
            return []
        url, kind = resolved
        blob = http_get(url)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    members = zip_members(blob) if kind == "zip" else image_members(blob)

    found = []
    for name, data in members:
        if len(found) >= per_item:
            break
        if not name.lower().endswith(TEXT_SUFFIXES):
            continue
        metrics = evaluate(data, codec)
        if metrics is not None:
            found.append((item, name, data, *metrics))
    return found


def write_candidates(rows, codec, language, output_dir, chardet_module) -> None:  # noqa: ANN001
    target_dir = output_dir / codec
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.csv"
    existing = manifest_path.is_file()
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for item, member, data, letters, high, unique in rows:
            digest = hashlib.sha256(data).hexdigest()[:12]
            path = target_dir / f"dos_{digest}.txt"
            path.write_bytes(data)
            detected = ""
            if chardet_module is not None:
                detected = chardet_module.detect(data)["encoding"] or "None"
            writer.writerow([
                path.relative_to(output_dir), item, member, codec, language,
                len(data), letters, high, unique, detected, "strict",
            ])
    print(f"Wrote {len(rows)} candidates to {target_dir}")


def run_broadcast(arguments) -> int:  # noqa: ANN001
    """Sweep items testing every file against all candidate codepages."""
    items = search_items(arguments.query, arguments.items)
    print(f"{len(items)} item(s), broadcasting across {len(BROADCAST_CODECS)} codepages")
    chardet_module = _load_chardet()
    found: dict[str, list[tuple]] = defaultdict(list)
    seen: set[str] = set()
    for item in items:
        try:
            resolved = item_archive_url(item)
            if resolved is None:
                continue
            url, kind = resolved
            blob = http_get(url)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        members = zip_members(blob) if kind == "zip" else image_members(blob)
        for name, data in members:
            if not name.lower().endswith(TEXT_SUFFIXES):
                continue
            match = best_match(data)
            if match is None:
                continue
            codec, language, score = match
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            metrics = evaluate(data, codec, language)
            if metrics is None:
                continue
            found[f"{codec}|{language}"].append((item, name, data, *metrics))
            print(f"  {item}/{name[:26]} -> {codec} ({language}), score={score}")
    if not found:
        print("No candidates found.")
        return 1
    for key, rows in found.items():
        codec, language = key.split("|")
        write_candidates(rows, codec, language, arguments.output, chardet_module)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--codec", default="cp850")
    parser.add_argument(
        "--broadcast",
        action="store_true",
        help="test every file against all DOS/Mac codepages instead of one",
    )
    parser.add_argument("--language", default="de")
    parser.add_argument("--query", default="deutsch OR german")
    parser.add_argument("--items", type=int, default=40)
    parser.add_argument("--per-item", type=int, default=2)
    parser.add_argument("--max-files", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    if arguments.broadcast:
        return run_broadcast(arguments)
    if arguments.codec not in DISTINGUISHING_BYTES:
        sys.exit(
            f"{arguments.codec} has no distinguishing-byte set, so a candidate "
            f"could not be told from a sibling codepage"
        )

    items = search_items(arguments.query, arguments.items)
    print(f"{len(items)} item(s) to try for {arguments.codec}")
    chardet_module = _load_chardet()

    rows: list[tuple] = []
    seen: set[str] = set()
    for item in items:
        if len(rows) >= arguments.max_files:
            break
        for row in mine_item(
            item, arguments.codec, arguments.per_item, arguments.language
        ):
            digest = hashlib.sha256(row[2]).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            rows.append(row)
            print(f"  {item}/{row[1][:34]}  unique_bytes={row[5]}")

    if not rows:
        print(f"No {arguments.codec} candidates found.")
        return 1
    write_candidates(
        rows, arguments.codec, arguments.language, arguments.output, chardet_module
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
