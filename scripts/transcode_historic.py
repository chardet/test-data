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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="self-test tables and exit")
    parser.add_argument("--source", default="kamenicky", choices=sorted(HISTORIC_TABLES))
    parser.add_argument("--target", default="cp852")
    parser.add_argument("--language", default="cs")
    parser.add_argument("--input", nargs="*", type=Path, default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    print("Table self-test:")
    if not self_test():
        sys.exit("table self-test failed; refusing to transcode")
    if arguments.check:
        return 0
    if not arguments.input:
        sys.exit("pass --input with one or more files in the source encoding")

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
