"""Source-position based HTML transforms.

Nothing here re-serializes a document. The standard library tokenizer reports
where every tag starts in the source text, and every change (adding
``data-eid`` attributes, injecting the editor, stripping for publish) is a
text splice at those positions. Bytes we don't intend to change stay
byte-identical, including scripts, styles and embedded data.
"""

from __future__ import annotations

import codecs
import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Elements that can become editable units.
CANDIDATE_TAGS = frozenset(
    "p h1 h2 h3 h4 h5 h6 li td th dt dd caption figcaption blockquote span".split()
)

# Markup allowed *inside* an editable unit. Anything else (a div, an img, a
# nested list, an svg) makes the enclosing element non-editable.
INLINE_TAGS = frozenset(
    """a abbr b bdi bdo br cite code data del dfn em font i ins kbd mark q s
    samp small span strong sub sup time u var wbr""".split()
)

# Nothing at or below these is ever tagged.
EXCLUDED_TAGS = frozenset(
    """head script style template noscript svg math canvas textarea select
    button object iframe title""".split()
)

VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

EID_ATTR = "data-eid"
_EID_RE = re.compile(r"^e(\d+)$")
_EID_ATTR_RE = re.compile(r"""\s+data-eid\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+)""", re.I)
_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Encoding


_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)


def detect_encoding(data: bytes) -> str:
    """Encoding of an HTML file: BOM, then <meta charset>, then UTF-8."""
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    m = _META_CHARSET_RE.search(data[:4096])
    if m:
        name = m.group(1).decode("ascii", "replace")
        try:
            enc = codecs.lookup(name).name
        except LookupError:
            return "utf-8"
        # Browsers treat latin-1 / ascii declarations as windows-1252.
        if enc in ("latin-1", "iso8859-1", "ascii"):
            return "cp1252"
        return enc
    return "utf-8"


def decode(data: bytes) -> tuple[str, str]:
    """Decode losslessly; ``encode(text, enc)`` gives back the same bytes."""
    enc = detect_encoding(data)
    return data.decode(enc, errors="surrogateescape"), enc


def encode(text: str, encoding: str) -> bytes:
    return text.encode(encoding, errors="surrogateescape")


# ---------------------------------------------------------------------------
# Tokenizing with positions


@dataclass
class Element:
    tag: str
    start: int  # offset of "<"
    start_end: int  # offset just past the start tag's ">"
    attrs: dict[str, str | None]
    parent: int | None
    excluded: bool  # at or inside an EXCLUDED_TAGS element
    end_start: int | None = None  # offset of the matching "</"
    end: int | None = None  # offset just past the end tag's ">"
    complex: bool = False  # contains something other than inline markup
    children: list[int] = field(default_factory=list)

    @property
    def closed(self) -> bool:
        return self.end_start is not None


class _Scanner(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.line_starts = [0]
        for m in re.finditer("\n", text):
            self.line_starts.append(m.end())
        self.elements: list[Element] = []
        self.stack: list[int] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def _open(self, tag: str, attrs, void: bool) -> None:
        start = self._offset()
        raw = self.get_starttag_text() or ""
        parent = self.stack[-1] if self.stack else None
        excluded = tag in EXCLUDED_TAGS or (
            parent is not None and self.elements[parent].excluded
        )
        if tag not in INLINE_TAGS:
            for i in self.stack:
                self.elements[i].complex = True
        el = Element(
            tag=tag,
            start=start,
            start_end=start + len(raw),
            attrs={k: v for k, v in attrs},
            parent=parent,
            excluded=excluded,
        )
        idx = len(self.elements)
        self.elements.append(el)
        if parent is not None:
            self.elements[parent].children.append(idx)
        if not void:
            self.stack.append(idx)

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, tag in VOID_TAGS)

    def handle_startendtag(self, tag, attrs):
        # "<p/>" is just "<p>" in HTML, but treating every self-closed tag as
        # empty is the conservative choice: it never yields an editable unit.
        self._open(tag, attrs, True)

    def handle_endtag(self, tag):
        pos = self._offset()
        for depth in range(len(self.stack) - 1, -1, -1):
            if self.elements[self.stack[depth]].tag == tag:
                el = self.elements[self.stack[depth]]
                el.end_start = pos
                close = self.text.find(">", pos)
                el.end = len(self.text) if close < 0 else close + 1
                # Anything still open above it was never explicitly closed.
                del self.stack[depth:]
                return
        # Stray end tag: ignore.


def scan(text: str) -> list[Element]:
    s = _Scanner(text)
    s.feed(text)
    s.close()
    return s.elements


# ---------------------------------------------------------------------------
# Tagging


def source_text(inner_html: str) -> str:
    """Normalized visible text of an HTML fragment (for the JS-filled guard)."""
    return normalize_text(html.unescape(_TAG_RE.sub("", inner_html)))


def normalize_text(s: str) -> str:
    return _WS_RE.sub(" ", s.replace(" ", " ")).strip()


def _is_editable(el: Element, elements: list[Element], text: str) -> bool:
    if el.tag not in CANDIDATE_TAGS or el.excluded or el.complex or not el.closed:
        return False
    # Every descendant must also have been closed explicitly, otherwise the
    # browser's idea of where things end may differ from ours.
    todo = list(el.children)
    while todo:
        child = elements[todo.pop()]
        if child.tag not in VOID_TAGS and not child.closed:
            return False
        todo.extend(child.children)
    return bool(source_text(text[el.start_end : el.end_start]))


@dataclass
class Tagged:
    html: str  # source with data-eid attributes
    editable: dict[str, str]  # eid -> normalized source text, in document order


def tag(text: str) -> Tagged:
    """Add ``data-eid`` to every editable unit.

    Existing eids (from a working copy) are kept. New units get numbers after
    the highest existing one, in document order, so tagging is deterministic.
    """
    elements = scan(text)

    units: list[int] = []
    taken: set[int] = set()  # indexes of elements inside an editable unit
    for i, el in enumerate(elements):
        if el.parent is not None and (el.parent in taken):
            taken.add(i)
            continue
        if _is_editable(el, elements, text):
            units.append(i)
            taken.add(i)

    max_n = 0
    for el in elements:
        m = _EID_RE.match(el.attrs.get(EID_ATTR) or "")
        if m:
            max_n = max(max_n, int(m.group(1)))

    seen: set[str] = set()
    splices: list[tuple[int, int, str]] = []  # (start, end, replacement)
    assigned: dict[int, str] = {}
    for i in units:
        el = elements[i]
        existing = el.attrs.get(EID_ATTR)
        if existing and _EID_RE.match(existing) and existing not in seen:
            assigned[i] = existing
            seen.add(existing)
            continue
        max_n += 1
        eid = f"e{max_n}"
        seen.add(eid)
        assigned[i] = eid
        start_tag = text[el.start : el.start_end]
        if EID_ATTR in el.attrs:
            # Missing value or a duplicate (e.g. a copied paragraph): replace it.
            new_tag = _EID_ATTR_RE.sub(f' {EID_ATTR}="{eid}"', start_tag, count=1)
        else:
            name_end = 1 + len(el.tag)
            new_tag = f'{start_tag[:name_end]} {EID_ATTR}="{eid}"{start_tag[name_end:]}'
        splices.append((el.start, el.start_end, new_tag))

    editable = {
        assigned[i]: source_text(text[elements[i].start_end : elements[i].end_start])
        for i in units
    }
    return Tagged(html=_apply(text, splices), editable=editable)


def strip_eids(text: str) -> str:
    """Remove every ``data-eid`` attribute from real start tags."""
    splices = []
    for el in scan(text):
        if EID_ATTR in el.attrs:
            start_tag = text[el.start : el.start_end]
            splices.append((el.start, el.start_end, _EID_ATTR_RE.sub("", start_tag)))
    return _apply(text, splices)


def _apply(text: str, splices: list[tuple[int, int, str]]) -> str:
    out = []
    pos = 0
    for start, end, repl in sorted(splices):
        out.append(text[pos:start])
        out.append(repl)
        pos = end
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Injecting the editor


def inject(text: str, config: dict, script_url: str) -> str:
    """Insert the editor config and script; never written to disk."""
    payload = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    snippet = (
        f'<script type="application/json" id="__hr-config">{payload}</script>'
        f'<script src="{script_url}" defer data-hr></script>'
    )
    elements = scan(text)
    pos = _injection_point(elements)
    if pos is None:
        pos = _after_doctype(text)
    return text[:pos] + snippet + text[pos:]


def _injection_point(elements: list[Element]) -> int | None:
    """End of <head> if it is closed, else just inside <body> or <html>."""
    first = {}
    for el in elements:
        first.setdefault(el.tag, el)
    head = first.get("head")
    if head is not None and head.end_start is not None:
        return head.end_start
    for name in ("body", "html"):
        if name in first:
            return first[name].start_end
    return None


_DOCTYPE_RE = re.compile(r"\A\s*(?:<!--.*?-->\s*)*<!doctype[^>]*>", re.I | re.S)


def _after_doctype(text: str) -> int:
    m = _DOCTYPE_RE.match(text)
    return m.end() if m else 0
