# Encoding Uniqueness Guards for Test Data Generation

## Problem

73 encoding-language gaps remain, concentrated in heavily-overlapping
single-byte encodings (cp850, cp858, iso-8859-15, windows-1252, iso-8859-1,
cp437). The previous generation run produced 155 duplicate groups because
CulturaX text for Latin-script languages often uses only codepoints encoded
identically across overlapping encodings. The existing ASCII-ratio gate does
not catch this.

We need guards in `passes_quality_gates()` that reject candidates which are
indistinguishable from files in other encoding directories, plus an MD5 dedup
check against existing files on disk.

## Guards

### Guard 1: Distinguishing-Byte Check (single-byte encodings)

After encoding text to bytes, verify the bytes contain at least one byte value
that maps to a *different* Unicode character in the target encoding vs at least
one overlapping encoding. If the encoded bytes only use byte values where all
overlapping encodings agree, the file is useless for testing disambiguation.

**Scope:** All single-byte encodings with >30% high-byte overlap (250 pairs
across Latin ISO/Windows, EBCDIC, DOS codepages, and Mac encodings).

**Implementation:** A precomputed static map in `scripts/encoding_overlaps.py`:

```python
# For each encoding, byte values (0x80-0xFF) where this encoding maps to
# a different character than at least one overlapping encoding.
DISTINGUISHING_BYTES: dict[str, frozenset[int]] = {
    "iso-8859-1": frozenset({0xA4, 0xA6, ...}),   # 125 bytes
    "windows-1252": frozenset({0x80, 0x82, ...}),  # 120 bytes
    "cp037": frozenset({...}),                      # vs cp500/cp1140/etc.
    ...
}
```

Two encodings "overlap" when >30% of byte values 0x80-0xFF decode to the same
Unicode character in both. For each encoding, we collect byte values where it
differs from *any* overlapping encoding (not all simultaneously). Example
counts:

| Encoding     | Overlapping encodings | Distinguishing bytes |
|--------------|----------------------|---------------------|
| iso-8859-1   | 16                   | 125                 |
| windows-1252 | 16                   | 120                 |
| cp437        | 15                   | 98                  |
| macroman     | 4                    | 102                 |
| cp037        | 5                    | ~110                |

The module includes a `if __name__ == "__main__"` block that regenerates the
map by comparing all codec tables, for maintenance.

**Quality gate check:**

```python
dist_bytes = DISTINGUISHING_BYTES.get(encoding_prefix)
if dist_bytes is not None:
    if not any(b in dist_bytes for b in encoded):
        return False
```

Encodings absent from the map (no overlaps) skip this check.

### Guard 2: Escape-Sequence Check (escape encodings)

Escape encodings (ISO-2022-JP, ISO-2022-KR, HZ-GB-2312) use 7-bit bytes with
ESC shift sequences. A file without escape sequences is just ASCII and
indistinguishable from any other encoding.

**Check:** Encoded bytes must contain at least one `0x1B` byte, or `~{` for
HZ-GB-2312.

### Guard 3: Multibyte Check (flexible multibyte encodings)

Flexible multibyte encodings like UTF-8 and UTF-7 can represent pure ASCII. A
"UTF-8" file with no bytes >= 0x80 is indistinguishable from ASCII.

**Check:** For UTF-8/UTF-8-SIG, encoded bytes must contain at least one byte
>= 0x80. For UTF-7, encoded bytes must contain at least one `+` shift
sequence (a `+` followed by non-`-`).

Does not apply to inherently multibyte CJK encodings (Big5, EUC-JP, Shift_JIS,
etc.) where CJK text naturally produces high bytes.

### Guard 4: MD5 Dedup Against Existing Files

After encoding, compute MD5 of the resulting bytes and check against a
pre-built index of all existing test files. Skip if identical bytes already
exist under any encoding directory.

**Implementation:** Build the MD5 index once in `main()`, pass to generation
functions:

```python
existing_md5s: set[str] = set()
for enc_dir in base_dir.iterdir():
    if enc_dir.is_dir() and not enc_dir.name.startswith("."):
        for f in enc_dir.iterdir():
            if f.is_file():
                existing_md5s.add(hashlib.md5(f.read_bytes()).hexdigest())
```

Update the set as new files are written so within-run duplicates are caught.

## Failure Mode

All guards use the same pattern: skip this candidate, try the next CulturaX
article. Only if ALL articles fail all guards is the gap logged as unfillable
and flagged for real-world sourcing (Wayback Machine, enca, uchardet corpora).

## Files

- **`scripts/encoding_overlaps.py`** (new) -- `DISTINGUISHING_BYTES` map +
  regeneration script
- **`scripts/generate_test_files.py`** (modified) -- integrate all four guards
  into `passes_quality_gates()` and candidate selection loop
