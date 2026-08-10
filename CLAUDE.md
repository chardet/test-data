# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

Test data repository for the [chardet](https://github.com/chardet/chardet) character encoding detection library. Contains ~2,178 text files in 82 character encodings across 47 languages. This is a **data-only** repo — no build system, no tests, no dependencies.

## Directory Structure

Each subdirectory is named `{encoding}` or `{encoding}-{language}` (e.g., `big5-chinese`, `utf-8-english`, `cp037-breton`). The encoding portion must resolve via Python's `codecs.lookup()`. The special `None/` directory holds binary files (images, video, xlsx) used as negative test cases.

## File Naming Conventions

Filename prefixes indicate provenance:

- `culturax_` — CulturaX dataset (mC4/OSCAR web crawl data), ~1,700 files
- `_ude_` — Ude (C# Universal Detector Engine) test suite
- `_chromium_` — Chromium browser encoding detection tests
- `_mozilla_` — Mozilla charset detection regression tests
- Domain-name `.xml` files (e.g., `kapranoff.ru.xml`) — Mark Pilgrim's original chardet RSS/Atom feeds

## Key Files

- **`scripts/check_test_data.py`** — Standalone Python 3 script (stdlib only) that validates all test files. Checks: decoding correctness, mojibake detection, control character ratios, language/script mismatches, binary file detection.
- **`CATALOG.md`** — Comprehensive catalog documenting every file's source, provenance, size, and notable characteristics. Regenerate with `python3 scripts/regenerate_catalog.py .` (idempotent; hand-written Notes are preserved across runs).
- **`scripts/provenance.py`** — Classifies every file as `wild`, `transcoded`, or `suite` by tracing it through git history to the commit that introduced it. Fills the catalog's Provenance column.
- **`scripts/check_registry_sync.py`** — Verifies this repo's language tables still match chardet's `registry.py`. Run it after any chardet release that touches encoding-language associations.
- **`scripts/find_real_test_data.py`** — Downloads wild samples from the Wayback Machine, uchardet, and ENCA.
- **`scripts/mine_common_crawl.py`** — Mines Common Crawl for pages served in a given charset (see "Mining wild data").
- **`scripts/mine_usenet.py`** — Mines HZ, ISO-2022-JP/KR, Big5 and EUC-KR posts from Internet Archive Usenet mboxes.
- **`scripts/mine_artpacks.py`** — Mines CP437 from BBS artpacks at 16colo.rs (`.NFO`/`.DIZ` members, not `.ANS` canvases).
- **`scripts/mine_repo_files.py`** — Scans a git repo for BOM-identified file encodings (utf-8-sig, utf-16).
- **`scripts/mine_po_files.py`** — Scans a git repo *or release tarball* for gettext catalogues, which declare their own charset and name their language.
- **`scripts/mine_dos_software.py`** — Mines DOS codepages from archive.org software, reading both zips and FAT floppy images.
- **`scripts/mine_mac_images.py`** — Mines mac-roman from classic HFS disk images (needs `machfs`).
- **`scripts/transcode_historic.py`** — Re-encodes period text from Kamenický or Mazovia, which Python cannot name, into a supported codepage.
- **`scripts/promote_candidates.py`** — The only supported way mined candidates enter the tree; refuses pairs chardet's registry does not vouch for.
- **`scripts/wild_coverage.py`** — Wild-file count per encoding against a target, with the shortfall grouped by how it could be sourced.

## Keeping in sync with chardet

`encoding_gaps.ENCODING_LANGUAGES` (which encoding-language pairs we expect
data for) and `check_test_data.LANGUAGE_SCRIPTS` (which scripts a language
uses) restate facts that chardet's `registry.py` owns, so they drift:

```bash
uv run --project ../chardet python3 scripts/check_registry_sync.py
```

Exits non-zero on any divergence. Deliberate differences go in that script's
`ALLOWED_DIVERGENCES` with a reason. Without chardet importable it skips
rather than fails, keeping this repo dependency-free.

**Directory names use ISO 639-1 codes** (`windows-1250-sr`, not
`windows-1250-serbian`) since the March 2026 rename. Three separate scripts
broke on that and were fixed later — any code matching a directory's language
suffix must map the code through `ISO_TO_LANGUAGE` first. Two encoding names
also collide with language codes: `utf-16-be` and `utf-32-be` are Belarusian
in the BOM'd encoding, while `utf-16be-*` and `utf-32be-*` are big-endian.
Resolve the language suffix *before* trying the whole name as a codec, or
`codecs.lookup` will hand back the big-endian codec.

## Common Commands

```bash
# Run the full quality check (from repo root)
python3 scripts/check_test_data.py .

# JSON output for machine processing
python3 scripts/check_test_data.py . --json
```

## Provenance: wild vs transcoded

Every file carries a provenance value in CATALOG.md, derived from the commit
that introduced it (`scripts/provenance.py`):

- **wild** — bytes as found: scraped pages, Wayback and Common Crawl captures,
  Usenet posts, files from real bug reports.
- **transcoded** — generated by re-encoding another text.
- **suite** — imported from another detector's corpus (Ude, ENCA, uchardet,
  Chromium, Mozilla, charset-normalizer). We know where we got them, not how
  *they* were obtained, and some arrived as deliberate multi-encoding sets — so
  they are deliberately not called wild.

Run `python3 scripts/wild_coverage.py` for the current counts. Provenance is
read from the introducing commit's subject, so keep the `Promote ` prefix when
promoting candidates. Files named `historic_`, `artpack_`, `usenet_`, `crawl_`
or `ebcdic_` are classified by that prefix instead, because only the mining
scripts write those names and keying solely on subject lines silently filed two
whole batches as `suite`.

Run `python3 scripts/provenance.py --check` to cross-check the git-derived
classification against an independent signal (whether a file's decoded text
also appears under another encoding). Wild files are ~98% text-unique and
transcoded ~92% shared; a large deviation means a rule in `COMMIT_RULES` needs
revisiting after new data lands.

**A high file count does not imply real-world coverage.** `mac-roman` (41
files) and `hp-roman8` (42) are entirely transcoded. See the Provenance table
in CATALOG.md for the per-encoding breakdown.

### What is reachable

The goal is 5-10 wild samples per encoding, and how attainable that is differs
sharply by family:

- **Web (Common Crawl)** — the Windows and ISO-8859 codepages, CJK, KOI8,
  Thai. Routine; just needs more crawl slices.
- **Usenet / mail archives** — HZ and ISO-2022-JP exist essentially nowhere
  else. `alt.chinese.text` and the `japan.*` hierarchy both deliver. What is
  *not* there is settled: a census of the 454 MB of archives already cached
  (13 mboxes, ~39,000 charset declarations) found zero `utf-7`, zero `johab`,
  and zero of the ISO-2022-JP variants. Korean Usenet used EUC-KR, so the
  whole `han.*` hierarchy yields exactly two `iso-2022-kr` messages, and both
  are halves of one 360-byte MIME test post. Do not go looking again; those
  five are transcoded.
- **Software / BBS / disk archives** — DOS, Mac and HP codepages. CP437 is
  solved via `mine_artpacks.py`. The rest are hard for reasons worth
  recording, so nobody re-runs the same dead ends:
  - *ISO-8859-14 was never on the web at all.* A census of a Common Crawl
    index slice that resolves single pages (72,305 windows-1252 declarations,
    one page each for several DOS codepages) counted zero, and a Wayback hunt
    over 123 raw snapshots from 17 Celtic-language domains of 1998-2010 —
    including the standard author's own evertype.com and egt.ie — found no
    declaration and no Latin-8 byte usage. The period explanation (Dyke, OU
    TR 2003/16): no Internet Explorer version ever supported it, so serving
    it meant mojibake for ~97% of visitors. The only genuine niche remains
    Irish gettext catalogues. Do not go hunting the open web again; wild
    coverage for this encoding can only come from .po files.
  - *Common Crawl cannot help.* A 30-part scan of ibm850/852/855/866 and the
    Mac pages produced 67 candidates and zero usable files; in 2019 those
    labels are nearly always a misconfigured server serving something else.
  - *National DOS text often used encodings we do not have.* A genuine Czech
    DOS README from archive.org decodes as Kamenický, not CP852 (`Dračí`
    renders as `Draçí`), and Polish DOS commonly used Mazovia. Finding the
    files does not yield the encodings; that needs registry support first.
  - *Mac items on archive.org are HFS disk images*, not zips, so their text
    is unreachable without an HFS reader.
- **gettext catalogues** — the densest self-labelling source found: each
  states its charset in its own header and names its language in its
  filename. Current catalogues are all UTF-8 (translators migrated years
  ago), so use *historical* releases — GNU keeps every tarball it shipped,
  and `gettext-0.14.6` alone spans nine encodings. KDE 3.5.10 and the
  Translation Project's current files are 100% UTF-8 and not worth scanning.
- **Historic transcoding** — Czech DOS text is usually Kamenický and Polish
  usually Mazovia, neither of which Python can name, so those files can only
  enter re-encoded into a supported codepage. `transcode_historic.py` does
  that with self-tested tables. The bottleneck is not the codec but surviving
  text: a 40-item sweep of Czech and Slovak DOS software found exactly one
  usable file. Its `--from-wild` mode re-encodes wild text already in the
  tree, preferring period sources and plain text over web pages, and enforces
  two rules worth knowing:
  - *The output may not contradict itself.* A page re-encoded to cp862 while
    its markup still said `windows-1255` would plant the exact contradiction
    the detector exists to resolve into the answer key. The declaration is
    rewritten to name the target, then checked.
  - *The encoding has to have changed something.* Output identical to the
    ASCII encoding of the same text is refused. Test for that by comparing
    against ASCII, not by looking for high bytes: UTF-7 and ISO-2022 are
    7-bit, and a high-byte test throws away every one of their transcodes.
- **File encodings** — utf-8-sig and utf-16 live in files, not web pages.
  A 30-part crawl found one BOM-carrying page; one Microsoft sample repo
  held 2,343. Use `mine_repo_files.py`. Note that real-world UTF-16 nearly
  always carries a BOM, so `utf-16-le`/`utf-16-be`, which mean *BOM-less*
  here, remain unfilled and may describe something that barely exists.
- **EBCDIC** (`cp037`, `cp273`, `cp424`, `cp500`, `cp875`, `cp1026`, `cp1006`,
  `cp1140`, `cp856`) — mostly transcode-only, but not for the reason first
  assumed. Real mainframe files are published; they are just usually *record*
  data. The AWS `mainframe-data-utilities` samples decode to readable names
  and dates yet are VB-format, with binary RDW headers and COMP fields that
  leave 795 NULs in a 1 KB file. The usable category is RECFM=F card images:
  fixed 80-byte records, `0x40`-padded, no NULs at all.
  `larandvit/ebcdic-parser`'s Texas Railroad Commission gas file is one, and
  `cp037-en` is filled from it. So the search that works is *look for card
  images, not for text*. Four further repos scanned that way (IBM-Z-zOS,
  copybook-rs, copybook-ts, vscode-ebcdicconverter) yielded nothing, and no
  non-English card-image dataset has turned up yet.
- **Minority-language pages need `--primary-language`.** `content_languages`
  is ordered most-confident-first, and matching anywhere in it returns the
  majority language's pages: every Kazakh-tagged page in six index parts came
  back `rus,kaz`, a Russian site with a Kazakh section, and the promoter
  rightly called all 16 Russian. Requiring the first entry turned that into 8
  usable Kazakh pages. Note windows-1251 cannot hold `ә ғ қ ң ө ұ ү һ і` at
  all, which is the reason kz1048 and ptcp154 exist, so Kazakh has to be
  mined from UTF-8 and transcoded down.
- **KOI8-T is too narrow for a real Tajik web page.** All 8 wild Tajik pages
  found carry Armenian, Arabic or accented Latin in their navigation chrome,
  and the round-trip check refuses the file over three characters in a
  language-picker menu. The CulturaX Tajik text in the tree encodes cleanly,
  so this is a property of wild pages, not of the codec table.
- **CP864 cannot hold logical-order Arabic at all.** Python's codec maps the
  presentation forms in the U+FE80 block, so base letters like `ا` and `م`
  raise `UnicodeEncodeError` and even `%` is absent. Real CP864 files store
  shaped glyphs. Filling it needs either a genuine wild file or an Arabic
  shaping pass, and a wrong shaper produces text a reader would call broken.

## Mining wild data

Most of this repo is CulturaX text transcoded into each encoding. The mining
scripts look for genuinely *wild* samples — bytes that were really served in
that encoding — which is what the rarest encoding-language pairs still lack.

```bash
# What charsets does a slice of the crawl declare?
python3 scripts/mine_common_crawl.py stats

# Fetch and validate candidates (needs duckdb; chardet optional but useful)
uv run --project ../chardet --with duckdb python3 scripts/mine_common_crawl.py mine

# Show competing readings of candidates that need a human/LLM ruling
python3 scripts/mine_common_crawl.py adjudicate

# Encodings that only ever existed on Usenet and in mail
uv run --with py7zr python3 scripts/mine_usenet.py --charset iso-2022-jp \
    --item FULL-USENET-BACKUP-2020-Oct-japan.town.sapporo.131.mbox.7z

# Copy vetted candidates into the tree (dry run without --apply)
python3 scripts/promote_candidates.py --input scripts/.cache/wild-bulk --apply
```

`--languages` takes ISO 639-1 codes like the rest of the repo, and translates
to the ISO 639-3 codes the crawl index actually stores. It is what makes a rare
language findable inside a common charset — iso-8859-1 alone has 629k pages per
index part.

Both miners write candidates plus a `manifest.csv` under `scripts/.cache/`
(gitignored) and **never promote anything automatically** — copying a vetted
file into its `{encoding}-{language}/` directory is a manual step.

Key lesson encoded in these scripts: **declared charsets lie often.** In the
first mining run, 0 of 11 disputed pages had a correct header. Each candidate
therefore gets a verdict saying what the evidence supports — `strong`,
`ambiguous`, `sparse`, `vacuous`, `utf8-mislabeled`, `wrong-legacy`,
`mojibake`, or `review`. Note that `vacuous` (pure ASCII) and `sparse` (very
little non-ASCII) mean the header is *unverifiable*, not wrong — that is the
normal shape of a mostly-markup page whose encoded text is a small subsection.

`adjudicate` mode exists because neither the header nor a detector is reliable
on the disputed cases: it prints every encoding that strict-decodes a page,
ranked by how much of the decoded text lands in that encoding's home script, so
a human (or an LLM that reads the languages) can pick the reading that yields
real words. This is how two pages declared `x-maccyrillic` and detected as
`windows-1250` were identified as ISO-8859-6 Arabic.

## Encoding Gotchas

- **EBCDIC** encodings (cp037, cp424, cp500, cp875, cp1026) have raw bytes that look entirely non-ASCII — don't use raw-byte ASCII heuristics on them.
- **UTF-32/UTF-16** files legitimately start with null bytes — don't treat `\x00` as a binary signature for these.
- Files may self-declare an encoding in XML/HTML headers that differs from the directory name. The directory name is the ground truth encoding for the raw bytes.

## Git

Branch: `main`.
