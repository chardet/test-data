"""Visual-order reordering for encodings whose wild text used it.

Generated test files for the encodings below are stored in *visual* order:
bytes in display order for a renderer with no bidi engine, the dominant
storage convention for these charsets in the wild.  Logical-order coverage
for the same bytes comes from the corpus's wild and suite files, so the
generator emits only the visual form.

``iso8859-8``
    ISO-8859-8(-E) declared visual order and dominated the 1990s Hebrew
    web; ISO-8859-8-I declared logical order and won email and the later
    web.  chardet's he/iso8859-8 model is trained on both conventions;
    windows-1255's is logical-only, so the detected name signals the
    storage convention, the behavior Mozilla's universal charset
    detector algorithm specified for Hebrew.

cp862 test files stay logical: both carved Hebrew MS-DOS 5.0 sources
measure logical order (final-form letters end words).  This module is the
sibling of chardet's scripts/bidi_order.py; keep the encoding sets in
sync with the model training there.

Reordering is the Unicode Bidirectional Algorithm with an LTR base
paragraph, mirrored brackets included.  Backends: python-bidi when
installed, else macOS libicucore via ctypes (stdlib-only).  Both are real
UBA implementations; do not replace them with a hand-rolled reverser.
"""

from __future__ import annotations

#: Encodings whose *generated test files* are stored in visual order.
VISUAL_ORDER_ENCODINGS: frozenset[str] = frozenset({"iso8859-8"})

_backend: str | None = None
_bidi_get_display = None
_icu_shape = None


def _load_backend() -> None:
    global _backend, _bidi_get_display  # noqa: PLW0603
    try:
        try:
            from bidi.algorithm import get_display  # noqa: PLC0415
        except ImportError:
            from bidi import get_display  # noqa: PLC0415
        _bidi_get_display = get_display
        _backend = "python-bidi"
        return
    except ImportError:
        pass
    _load_icu()
    _backend = "icucore"


def _load_icu() -> None:
    import ctypes  # noqa: PLC0415
    import ctypes.util  # noqa: PLC0415

    global _icu_shape  # noqa: PLW0603
    path = ctypes.util.find_library("icucore")
    if path is None:
        msg = (
            "visual-order reordering needs either the python-bidi package "
            "or macOS libicucore; neither is available"
        )
        raise RuntimeError(msg)
    icu = ctypes.CDLL(path)
    ubidi_open = icu.ubidi_open
    ubidi_open.restype = ctypes.c_void_p
    ubidi_close = icu.ubidi_close
    ubidi_close.argtypes = [ctypes.c_void_p]
    ubidi_set_para = icu.ubidi_setPara
    ubidi_set_para.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.c_int32,
        ctypes.c_uint8,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    ubidi_write = icu.ubidi_writeReordered
    ubidi_write.restype = ctypes.c_int32
    ubidi_write.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.c_int32,
        ctypes.c_uint16,
        ctypes.POINTER(ctypes.c_int),
    ]
    do_mirroring = 2  # UBIDI_DO_MIRRORING

    def shape(line: str) -> str:
        units = line.encode("utf-16-le")
        n = len(units) // 2
        src = (ctypes.c_uint16 * n).from_buffer_copy(units)
        bidi = ubidi_open()
        try:
            err = ctypes.c_int(0)
            ubidi_set_para(bidi, src, n, 0, None, ctypes.byref(err))
            if err.value > 0:
                msg = f"ubidi_setPara error {err.value}"
                raise RuntimeError(msg)
            dest = (ctypes.c_uint16 * (n * 2 + 8))()
            err = ctypes.c_int(0)
            written = ubidi_write(
                bidi, dest, len(dest), do_mirroring, ctypes.byref(err)
            )
            if err.value > 0:
                msg = f"ubidi_writeReordered error {err.value}"
                raise RuntimeError(msg)
            return b"".join(u.to_bytes(2, "little") for u in dest[:written]).decode(
                "utf-16-le"
            )
        finally:
            ubidi_close(bidi)

    _icu_shape = shape


def reorder_visual(text: str) -> str:
    """Reorder logical-order *text* into visual order, line by line."""
    if _backend is None:
        _load_backend()
    if _backend == "python-bidi":
        return "\n".join(
            _bidi_get_display(line, base_dir="L") if line else line
            for line in text.split("\n")
        )
    return "\n".join(_icu_shape(line) if line else line for line in text.split("\n"))
