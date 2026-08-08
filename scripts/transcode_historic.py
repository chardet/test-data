#!/usr/bin/env python3
"""Convert period text in unsupported historic encodings into supported ones.

Some national DOS codepages have real surviving corpora but no Python
codec, so their files can never be labelled as themselves here: Czech DOS
text is typically Kamenický (KEYBCS2) and Polish is often Mazovia, neither
of which Python ships.  Mining them found genuine period text that had to
be thrown away -- a Dragon History README decodes as ``Draçí Historie``
under CP852 because it is not CP852 at all.

Rather than discard it, decode with a built-in table and re-encode into
the supported codepage that the same text would have used on a differently
configured machine of the era.  The result is not wild -- the bytes are
ours -- but the *text* is authentic period Czech or Polish rather than
modern web prose, which makes it markedly better transcoding input than
CulturaX for these directories.

Every table is self-tested against known words before use, because a
wrong entry would corrupt the output silently and look plausible.

Dependencies: the standard library.

Usage::

    python3 scripts/transcode_historic.py --check
    python3 scripts/transcode_historic.py --source kamenicky --target cp852 \\
        --language cs --input someREADME.DOC
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

DEFAULT_OUTPUT = Path("scripts/.cache/historic")

# Kamenický (KEYBCS2), the de facto Czech and Slovak DOS encoding.
# Bytes 0x00-0x7F are ASCII; this is the upper half, 0x80 first.
KAMENICKY_UPPER = (
    "ČüéďäĎŤčěĚĹÍľĺÄÁ"
    "ÉžŽôöÓůÚýÖÜŠĽÝŘť"
    "áíóúňŇŮÔšřŕŔ¼§«»"
    "░▒▓│┤╡╢╖╕╣║╗╝╜╛┐"
    "└┴┬├─┼╞╟╚╔╩╦╠═╬╧"
    "╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀"
    "αßΓπΣσµτΦΘΩδ∞φε∩"
    "≡±≥≤⌠⌡÷≈°∙·√ⁿ²■ "
)

# Mazovia (CP667), the de facto Polish DOS encoding.  Positions 0xB0-0xFF
# are identical to CP437; the Polish letters replace CP437's Western
# accents in 0x80-0xAF, which is exactly why Polish DOS text read as CP437
# or CP852 comes out wrong.
MAZOVIA_UPPER = (
    "Çüéâäàąçêëèïî\u0107ÄĄ"
    "Ęęłôö\u0106ûùŚÖÜ¢Ł¥śƒ"
    "ŹŻóÓńŃźż¿⌐¬½¼¡«»"
    "░▒▓│┤╡╢╖╕╣║╗╝╜╛┐"
    "└┴┬├─┼╞╟╚╔╩╦╠═╬╧"
    "╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀"
    "αßΓπΣσµτΦΘΩδ∞φε∩"
    "≡±≥≤⌠⌡÷≈°∙·√ⁿ²■ "
)

HISTORIC_TABLES: dict[str, str] = {
    "kamenicky": KAMENICKY_UPPER,
    "mazovia": MAZOVIA_UPPER,
}

# Words that must appear once a table is applied correctly.  A single wrong
# entry silently produces plausible-looking mojibake, so each table is
# checked against text whose reading is known.
TABLE_SELFTEST: dict[str, tuple[bytes, tuple[str, ...]]] = {
    "kamenicky": (
        b"Dra\x87\xa1 Historie",  # "Dračí Historie"
        ("Dračí", "Historie"),
    ),
    "mazovia": (
        b"ma\x92y du\xa7y \x91d\xa6",  # "mały duży ędź"
        ("mały", "duży"),
    ),
}

MANIFEST_COLUMNS = (
    "path", "source_encoding", "target_encoding", "language", "origin",
    "size", "letters", "status",
)


def decode_historic(data: bytes, source: str) -> str:
    """Decode bytes using a built-in historic table."""
    upper = HISTORIC_TABLES[source]
    if len(upper) != 128:
        msg = f"{source} table has {len(upper)} entries, expected 128"
        raise ValueError(msg)
    return "".join(chr(b) if b < 0x80 else upper[b - 0x80] for b in data)


def self_test() -> bool:
    """Verify every table reproduces known words.  Returns True if all pass."""
    ok = True
    for source, (sample, expected) in TABLE_SELFTEST.items():
        try:
            text = decode_historic(sample, source)
        except ValueError as error:
            print(f"  {source}: {error}")
            ok = False
            continue
        missing = [word for word in expected if word not in text]
        if missing:
            print(f"  {source}: FAIL -- decoded {text!r}, missing {missing}")
            ok = False
        else:
            print(f"  {source}: ok -- {text!r}")
    return ok


def transcode(data: bytes, source: str, target: str) -> tuple[bytes, int] | None:
    """Re-encode historic *data* into *target*, or None if it cannot hold it."""
    text = decode_historic(data, source)
    if any(ord(ch) < 0x20 and ch not in "\t\n\r\x1a" for ch in text):
        return None
    try:
        out = text.encode(target)
    except (UnicodeEncodeError, LookupError):
        return None
    # Round-trip: the target must reproduce the text exactly, or the
    # transcode has quietly dropped or folded characters.
    if out.decode(target) != text:
        return None
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < 200:
        return None
    return out, letters


def run_from_wild(arguments) -> int:  # noqa: ANN001
    """Re-encode wild in-tree text into a target the wild corpus cannot fill."""
    # Most candidates fail: EBCDIC cannot represent the box-drawing in a
    # BBS .NFO, so the pool has to be far larger than the target count.
    sources = wild_sources(arguments.language, max(arguments.count * 30, 60))
    if not sources:
        sys.exit(f"no wild {arguments.language} text in the tree to re-encode")

    target_dir = arguments.output / f"{arguments.target}-{arguments.language}"
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = arguments.output / "manifest.csv"
    existing = manifest_path.is_file()
    written = 0
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for origin, text in sources:
            if written >= arguments.count:
                break
            try:
                out = text.encode(arguments.target)
            except (UnicodeEncodeError, LookupError):
                continue
            if out.decode(arguments.target) != text:
                continue
            digest = hashlib.sha256(out).hexdigest()[:12]
            path = target_dir / f"historic_{digest}.txt"
            path.write_bytes(out)
            writer.writerow([
                path.relative_to(arguments.output), "wild-in-tree",
                arguments.target, arguments.language, origin,
                len(out), sum(1 for ch in text if ch.isalpha()), "transcoded",
            ])
            written += 1
            print(f"  {origin} -> {path.name}")
    print(f"Wrote {written} file(s) to {target_dir}")
    return 0 if written else 1


def wild_sources(language: str, limit: int) -> list[tuple[str, str]]:
    """Text of wild files in a language, newest-authentic sources first.

    Period-appropriate input matters.  A BBS .NFO or a Usenet post is far
    closer to what an EBCDIC mainframe or a UTF-16 export actually held
    than a modern CulturaX web page, so wild files are preferred over the
    transcoded bulk already in the tree.
    """
    import codecs as _codecs  # noqa: PLC0415

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from encoding_gaps import get_codec  # noqa: PLC0415
    from provenance import WILD, classify_all, data_files  # noqa: PLC0415

    verdicts = classify_all()
    scored: list[tuple[int, str, str]] = []
    for path in data_files():
        if verdicts.get(path) != WILD:
            continue
        directory, _, _ = path.partition("/")
        if not directory.endswith(f"-{language}"):
            continue
        prefix = directory.rsplit("-", 1)[0]
        try:
            codec = _codecs.lookup(get_codec(prefix)).name
            text = (REPO / path).read_bytes().decode(codec)
        except (LookupError, UnicodeDecodeError, OSError):
            continue
        if sum(1 for ch in text if ch.isalpha()) < 200:
            continue
        name = path.partition("/")[2]
        # Rank by how good a stand-in the text is.  Period sources first:
        # a BBS .NFO or Usenet post is closer to what a mainframe or a
        # UTF-16 export held than a 2019 web page.  Then prefer text with
        # non-ASCII, so the target codepage is actually exercised -- pure
        # ASCII encodes identically under sibling EBCDIC pages and so
        # distinguishes none of them.
        era = 0 if name.startswith(("artpack_", "usenet_", "_ude_")) else 2
        flat = 0 if any(ch > "\x7f" for ch in text) else 1
        scored.append((era + flat, path, text))
    scored.sort(key=lambda row: row[0])
    return [(path, text) for _, path, text in scored[:limit]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="self-test tables and exit")
    parser.add_argument("--source", default="kamenicky", choices=sorted(HISTORIC_TABLES))
    parser.add_argument("--target", default="cp852")
    parser.add_argument("--language", default="cs")
    parser.add_argument("--input", nargs="*", type=Path, default=[])
    parser.add_argument(
        "--from-wild",
        action="store_true",
        help="re-encode wild files already in the tree, for encodings whose "
        "own corpus is not published as text (EBCDIC, BOM-less UTF-16)",
    )
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    print("Table self-test:")
    if not self_test():
        sys.exit("table self-test failed; refusing to transcode")
    if arguments.check:
        return 0
    if arguments.from_wild:
        return run_from_wild(arguments)
    if not arguments.input:
        sys.exit("pass --input, or --from-wild")

    target_dir = arguments.output / f"{arguments.target}-{arguments.language}"
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = arguments.output / "manifest.csv"
    existing = manifest_path.is_file()
    written = 0
    with manifest_path.open("a" if existing else "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not existing:
            writer.writerow(MANIFEST_COLUMNS)
        for source_path in arguments.input:
            try:
                data = source_path.read_bytes()
            except OSError as error:
                print(f"  {source_path}: {error}")
                continue
            result = transcode(data, arguments.source, arguments.target)
            if result is None:
                print(f"  {source_path.name}: not representable in {arguments.target}")
                continue
            out, letters = result
            digest = hashlib.sha256(out).hexdigest()[:12]
            path = target_dir / f"historic_{digest}.txt"
            path.write_bytes(out)
            writer.writerow([
                path.relative_to(arguments.output), arguments.source,
                arguments.target, arguments.language, source_path.name,
                len(out), letters, "transcoded",
            ])
            written += 1
            print(f"  {source_path.name} -> {path.name} ({letters} letters)")
    print(f"Wrote {written} file(s) to {target_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
