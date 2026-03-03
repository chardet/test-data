# Encoding Uniqueness Guards Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add four guards to `scripts/generate_test_files.py` that prevent generating test data files which are indistinguishable across overlapping encodings.

**Architecture:** A new `scripts/encoding_overlaps.py` module provides a precomputed static map of distinguishing byte values per encoding. The generation script's `passes_quality_gates()` function gains three new checks (distinguishing-byte, escape-sequence, multibyte), and `generate_culturax()` gains an MD5 dedup check against existing files. All guards skip the candidate on failure so the next CulturaX article is tried.

**Tech Stack:** Python 3.10+ stdlib only (`codecs`, `hashlib`). No new dependencies.

---

### Task 1: Create `scripts/encoding_overlaps.py` with `DISTINGUISHING_BYTES` map

This is the precomputed static data that Guard 1 needs. The module also has a `if __name__ == "__main__"` regeneration block for maintenance.

**Files:**
- Create: `scripts/encoding_overlaps.py`

**Step 1: Write the regeneration script that computes the map**

Create `scripts/encoding_overlaps.py` with a `_compute_distinguishing_bytes()` function and a `if __name__ == "__main__"` block that prints the map as copy-pasteable Python source.

The algorithm:
1. For each single-byte encoding in `encoding_gaps.ENCODING_LANGUAGES`, build a decode table: byte (0x00-0xFF) -> Unicode character (or `None` if invalid).
2. For each pair of encodings, compute the overlap percentage: count of byte values 0x80-0xFF where both decode to the same character, divided by 128. Two encodings "overlap" if this is > 0.30 (30%).
3. For each encoding with at least one overlap, collect the set of byte values 0x80-0xFF where it decodes to a different character than at least one overlapping encoding.
4. Store as `DISTINGUISHING_BYTES: dict[str, frozenset[int]]`.

Multi-byte, escape-sequence, and Unicode encodings are excluded from this map (they use Guards 2/3 instead or have no single-byte overlap issue).

```python
"""Precomputed encoding overlap data for test data generation guards.

For each single-byte encoding that overlaps with other single-byte encodings,
DISTINGUISHING_BYTES maps the encoding name to the set of byte values
(0x80-0xFF) where it decodes to a different Unicode character than at least
one overlapping encoding.

Run ``python -m scripts.encoding_overlaps`` to regenerate the map.
"""

from __future__ import annotations

import codecs
import sys
from pathlib import Path

# Overlap threshold: two encodings "overlap" when this fraction of byte
# values 0x80-0xFF decode to the same Unicode character in both.
_OVERLAP_THRESHOLD = 0.30

# Single-byte encodings to analyze.  Sourced from encoding_gaps.py,
# excluding Unicode, multibyte, and escape-sequence encodings.
_SINGLE_BYTE_ENCODINGS = [
    "iso-8859-1", "iso-8859-2", "iso-8859-3", "iso-8859-4", "iso-8859-5",
    "iso-8859-6", "iso-8859-7", "iso-8859-8", "iso-8859-9", "iso-8859-10",
    "iso-8859-11", "iso-8859-13", "iso-8859-14", "iso-8859-15", "iso-8859-16",
    "windows-1250", "windows-1251", "windows-1252", "windows-1253",
    "windows-1254", "windows-1255", "windows-1256", "windows-1257",
    "windows-1258",
    "cp037", "cp273", "cp424", "cp437", "cp500", "cp720", "cp737",
    "cp775", "cp850", "cp852", "cp855", "cp856", "cp857", "cp858",
    "cp860", "cp861", "cp862", "cp863", "cp864", "cp865", "cp866",
    "cp869", "cp874", "cp875", "cp1006", "cp1026", "cp1125", "cp1140",
    "koi8-r", "koi8-u", "koi8-t", "kz1048", "ptcp154",
    "tis-620",
    "macroman", "maccyrillic", "maclatin2", "macgreek", "macturkish",
    "maciceland", "hp-roman8",
]


def _build_decode_table(encoding: str) -> dict[int, str | None] | None:
    """Build byte -> Unicode character table for a single-byte encoding."""
    try:
        codec_name = codecs.lookup(encoding).name
    except LookupError:
        return None
    table: dict[int, str | None] = {}
    for b in range(256):
        try:
            table[b] = bytes([b]).decode(codec_name)
        except (UnicodeDecodeError, ValueError):
            table[b] = None
    return table


def _compute_distinguishing_bytes() -> dict[str, frozenset[int]]:
    """Compute the DISTINGUISHING_BYTES map from codec tables."""
    # Build decode tables for all valid encodings.
    tables: dict[str, dict[int, str | None]] = {}
    for enc in _SINGLE_BYTE_ENCODINGS:
        table = _build_decode_table(enc)
        if table is not None:
            tables[enc] = table

    # Find overlapping pairs.
    overlaps: dict[str, list[str]] = {enc: [] for enc in tables}
    enc_list = sorted(tables)
    for i, enc_a in enumerate(enc_list):
        for enc_b in enc_list[i + 1:]:
            same = sum(
                1 for b in range(128, 256)
                if tables[enc_a][b] is not None
                and tables[enc_b][b] is not None
                and tables[enc_a][b] == tables[enc_b][b]
            )
            if same / 128 > _OVERLAP_THRESHOLD:
                overlaps[enc_a].append(enc_b)
                overlaps[enc_b].append(enc_a)

    # For each encoding with overlaps, find distinguishing byte values.
    result: dict[str, frozenset[int]] = {}
    for enc, partners in sorted(overlaps.items()):
        if not partners:
            continue
        dist: set[int] = set()
        for b in range(128, 256):
            ch = tables[enc][b]
            if ch is None:
                continue
            for partner in partners:
                if tables[partner][b] != ch:
                    dist.add(b)
                    break
        result[enc] = frozenset(dist)

    return result


# --- Precomputed map (regenerate with: python -m scripts.encoding_overlaps) ---

DISTINGUISHING_BYTES: dict[str, frozenset[int]] = {}  # placeholder


# Populate on import by computing from codec tables.  This runs once and
# takes <50 ms, so there is no need to hardcode the values.
DISTINGUISHING_BYTES = _compute_distinguishing_bytes()


if __name__ == "__main__":
    # Print the map as formatted Python source for review / hardcoding.
    print("DISTINGUISHING_BYTES: dict[str, frozenset[int]] = {")
    for enc, dist in sorted(DISTINGUISHING_BYTES.items()):
        byte_strs = ", ".join(f"0x{b:02X}" for b in sorted(dist))
        print(f'    "{enc}": frozenset({{{byte_strs}}}),')
    print("}")
    print(f"\n# {len(DISTINGUISHING_BYTES)} encodings with overlaps", file=sys.stderr)
```

**Step 2: Run the module to verify the map**

Run: `python -m scripts.encoding_overlaps 2>&1 | tail -5`

Expected: The map prints as Python source with ~50 encoding entries. stderr line shows count like `# 50 encodings with overlaps`.

**Step 3: Commit**

```bash
git add scripts/encoding_overlaps.py
git commit -m "Add encoding overlap map for distinguishing-byte guard"
```

---

### Task 2: Add Guard 1 (distinguishing-byte check) to `passes_quality_gates()`

**Files:**
- Modify: `scripts/generate_test_files.py:30-34` (imports)
- Modify: `scripts/generate_test_files.py:241-274` (`passes_quality_gates()`)

**Step 1: Add the import**

At `scripts/generate_test_files.py:30`, add to the imports section:

```python
from scripts.encoding_overlaps import DISTINGUISHING_BYTES  # noqa: E402
```

**Step 2: Add the guard to `passes_quality_gates()`**

Insert after the round-trip check (line 272) and before `return True` (line 274):

```python
    # Distinguishing-byte check: encoded bytes must contain at least one
    # byte value that differs between this encoding and an overlapping one.
    dist_bytes = DISTINGUISHING_BYTES.get(encoding_prefix)
    if dist_bytes is not None:
        if not any(b in dist_bytes for b in encoded):
            return False
```

**Step 3: Run a quick smoke test**

Run: `python -c "from scripts.generate_test_files import passes_quality_gates; print('OK')"`

Expected: `OK` (no import errors, no crashes)

**Step 4: Commit**

```bash
git add scripts/generate_test_files.py
git commit -m "Add distinguishing-byte guard to quality gates"
```

---

### Task 3: Add Guard 2 (escape-sequence check) to `passes_quality_gates()`

**Files:**
- Modify: `scripts/generate_test_files.py:241-274` (`passes_quality_gates()`)

**Step 1: Add the guard**

Insert after the distinguishing-byte check, before `return True`:

```python
    # Escape-sequence check: escape encodings must contain actual escape
    # sequences, otherwise the file is indistinguishable from ASCII.
    if encoding_prefix in ESCAPE_ENCODINGS:
        if encoding_prefix == "hz-gb-2312":
            if b"~{" not in encoded:
                return False
        else:
            if b"\x1b" not in encoded:
                return False
```

`ESCAPE_ENCODINGS` is already defined at line 68 of the file.

**Step 2: Commit**

```bash
git add scripts/generate_test_files.py
git commit -m "Add escape-sequence guard to quality gates"
```

---

### Task 4: Add Guard 3 (multibyte check) to `passes_quality_gates()`

**Files:**
- Modify: `scripts/generate_test_files.py:241-274` (`passes_quality_gates()`)

**Step 1: Define the flexible-multibyte set**

Add near the other encoding sets (after `ESCAPE_ENCODINGS` around line 71):

```python
# Flexible multibyte encodings that can represent pure ASCII.  Test files
# for these must contain actual multibyte sequences.
FLEXIBLE_MULTIBYTE_ENCODINGS = {"utf-8", "utf-8-sig"}
```

Note: UTF-7 gaps are handled by `generate_utf7()` which re-encodes from
UTF-8 files that already contain non-ASCII text, so UTF-7 doesn't need this
gate. If a UTF-7 guard were needed in the future, the check would be for a
`+` followed by non-`-` (a Base64-encoded shift sequence).

**Step 2: Add the guard to `passes_quality_gates()`**

Insert after the escape-sequence check, before `return True`:

```python
    # Multibyte check: flexible multibyte encodings must contain actual
    # multibyte sequences, otherwise the file is indistinguishable from ASCII.
    if encoding_prefix in FLEXIBLE_MULTIBYTE_ENCODINGS:
        if max(encoded) < 128:
            return False
```

**Step 3: Commit**

```bash
git add scripts/generate_test_files.py
git commit -m "Add multibyte guard to quality gates"
```

---

### Task 5: Add Guard 4 (MD5 dedup) to `generate_culturax()`

This guard lives in `generate_culturax()` rather than `passes_quality_gates()` because it needs access to the file index (external state), and the index must be updated as new files are written.

**Files:**
- Modify: `scripts/generate_test_files.py:16-19` (add `import hashlib`)
- Modify: `scripts/generate_test_files.py:370-472` (`generate_culturax()`)
- Modify: `scripts/generate_test_files.py:480-596` (`main()`)

**Step 1: Add the hashlib import**

At the top of `scripts/generate_test_files.py`, add `hashlib` to the imports (around line 18):

```python
import hashlib
```

**Step 2: Build the MD5 index in `main()`**

In `main()`, after `base_dir` is resolved (line 507) and before the gap-filling loop, add:

```python
    # Build MD5 index of all existing test files for dedup guard.
    existing_md5s: set[str] = set()
    if not args.dry_run:
        for enc_dir in base_dir.iterdir():
            if not enc_dir.is_dir() or enc_dir.name.startswith((".","scripts")):
                continue
            for f in enc_dir.iterdir():
                if f.is_file():
                    existing_md5s.add(hashlib.md5(f.read_bytes()).hexdigest())
        print(f"MD5 index: {len(existing_md5s)} existing files")
```

**Step 3: Pass `existing_md5s` to `generate_culturax()`**

Update the `generate_culturax()` signature to accept `existing_md5s: set[str]`:

```python
def generate_culturax(
    gap: tuple[str, str],
    base_dir: Path,
    cache_dir: str,
    dry_run: bool,
    manifest: list[dict],
    existing_md5s: set[str],
) -> bool:
```

Update the call site in `main()` (around line 553):

```python
            ok = generate_culturax(
                gap, base_dir, cache_dir, args.dry_run, manifest, existing_md5s,
            )
```

**Step 4: Add the MD5 dedup check in `generate_culturax()`**

In the candidate loop (around line 410), after encoding and the `passes_quality_gates()` check, add the MD5 check. The full candidate acceptance block becomes:

```python
        # Try the full article first.
        encoded = text.encode(codec, errors="ignore")
        if passes_quality_gates(encoded, text, codec, enc_prefix, language):
            md5 = hashlib.md5(encoded).hexdigest()
            if md5 not in existing_md5s:
                candidates.append((text, encoded))
                continue
```

Apply the same pattern to the truncated-version inner loop (around line 427):

```python
                enc = trimmed.encode(codec, errors="ignore")
                if passes_quality_gates(enc, trimmed, codec, enc_prefix, language):
                    md5 = hashlib.md5(enc).hexdigest()
                    if md5 not in existing_md5s:
                        candidates.append((trimmed, enc))
                        added = True
                        break
```

**Step 5: Update the MD5 index when files are written**

In `generate_culturax()`, after writing each file (around line 458), add the new file's MD5 to the index:

```python
    for fname, encoded in generated:
        (dst_dir / fname).write_bytes(encoded)
        existing_md5s.add(hashlib.md5(encoded).hexdigest())
```

**Step 6: Commit**

```bash
git add scripts/generate_test_files.py
git commit -m "Add MD5 dedup guard to CulturaX generation"
```

---

### Task 6: Run dry-run to verify guards work

**Files:** None (verification only)

**Step 1: Run dry-run**

Run: `python scripts/generate_test_files.py --dry-run 2>&1 | head -20`

Expected: Output shows gaps to fill with no crashes. Dry-run mode doesn't exercise the actual guards (it returns early), but confirms imports and setup are correct.

**Step 2: Run a targeted generation to exercise guards**

Pick one encoding with known overlaps to generate and verify the guards are being hit. Choose an encoding that has existing test data so the MD5 dedup has something to check against.

Run: `python scripts/generate_test_files.py --encodings iso-8859-1 2>&1`

Expected: Files are created in the gap directories. If any candidates are rejected by guards, there will be more articles tried (seen in verbose output). No duplicate files should be created (verify with the cleanup script dry-run).

**Step 3: Verify no duplicates were created**

Run: `python scripts/cleanup_duplicates.py 2>&1 | tail -10`

Expected: `Found 0 duplicate groups` (or at least no new duplicates in the just-generated directories).

**Step 4: Commit generated files if they look good**

```bash
git add iso-8859-1-*/
git commit -m "Fill iso-8859-1 coverage gaps with uniqueness guards"
```

---

### Task 7: Generate remaining gaps and verify

**Files:** Generated test data directories

**Step 1: Generate all remaining gaps**

Run: `python scripts/generate_test_files.py 2>&1 | tee /tmp/generate_output.txt`

Expected: Output shows each gap being filled or skipped. Review the output for any unexpected skips.

**Step 2: Check for duplicates**

Run: `python scripts/cleanup_duplicates.py 2>&1`

Expected: `Found 0 duplicate groups` (the guards prevented all duplicates).

**Step 3: Check remaining gaps**

Run: `python -c "from scripts.encoding_gaps import find_gaps; gaps = find_gaps('.'); print(f'{len(gaps)} gaps remain'); [print(f'  {e}-{l}') for e,l in gaps]"`

Expected: Fewer gaps than before. Any remaining gaps are encodings where CulturaX text couldn't produce distinguishable files (flagged for real-world sourcing).

**Step 4: Commit all generated files**

```bash
git add */
git commit -m "Fill remaining coverage gaps with encoding uniqueness guards"
```
