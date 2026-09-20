"""Turn a plain EPUB into a Kobo kepub.

Kobo renders ``.kepub.epub`` files with its own WebKit-based engine (MathML,
pop-up footnotes, proper CSS) instead of Adobe RMSDK. That engine expects each
sentence wrapped in ``<span class="koboSpan" id="kobo.P.S">`` for reading
position, highlights and stats to work, plus a ``book-columns``/``book-inner``
wrapper around the body. This is a compact re-implementation of what kepubify
does.
"""

import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

XHTML = "http://www.w3.org/1999/xhtml"
SKIP_TAGS = {"math", "svg", "script", "style", "head", "title", "img", "video", "audio"}
BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "td", "th",
    "figcaption", "caption", "blockquote", "pre", "div", "section", "aside",
    "body", "article", "header", "footer", "nav", "figure", "ol", "ul", "table",
    "tr",
}
SENTENCE_RE = re.compile(r".*?[.!?:][\"”’)\]]*(?:\s+|$)|.+$", re.S)


def _localname(tag):
    return tag.split("}", 1)[1] if isinstance(tag, str) and tag.startswith("{") else tag


class _Counter:
    def __init__(self):
        self.para = 0
        self.seg = 0


def _sentences(text, split):
    if not split:
        return [text]
    parts = [m.group(0) for m in SENTENCE_RE.finditer(text) if m.group(0)]
    return parts or [text]


def _make_spans(text, ctx, split=True):
    spans = []
    for chunk in _sentences(text, split):
        ctx.seg += 1
        span = etree.Element(f"{{{XHTML}}}span")
        span.set("class", "koboSpan")
        span.set("id", f"kobo.{ctx.para}.{ctx.seg}")
        span.text = chunk
        spans.append(span)
    return spans


def _is_kobo_span(el):
    return isinstance(el.tag, str) and el.get("class") == "koboSpan"


def _process(elem, ctx, in_pre=False):
    if not isinstance(elem.tag, str):
        return
    name = _localname(elem.tag)
    if name in SKIP_TAGS:
        return
    if name in BLOCK_TAGS:
        ctx.para += 1
        ctx.seg = 0
    in_pre = in_pre or name == "pre"
    if elem.text and elem.text.strip():
        spans = _make_spans(elem.text, ctx, split=not in_pre)
        elem.text = None
        for i, span in enumerate(spans):
            elem.insert(i, span)
    for child in [c for c in elem if not _is_kobo_span(c)]:
        _process(child, ctx, in_pre)
        if child.tail and child.tail.strip():
            spans = _make_spans(child.tail, ctx, split=not in_pre)
            child.tail = None
            idx = elem.index(child)
            for j, span in enumerate(spans):
                elem.insert(idx + 1 + j, span)


def kepubify_xhtml(data: bytes) -> bytes:
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    tree = etree.fromstring(data, parser).getroottree()
    root = tree.getroot()
    body = root.find(f"{{{XHTML}}}body")
    if body is None:
        return data
    if body.find(f"{{{XHTML}}}div[@id='book-columns']") is None:
        columns = etree.Element(f"{{{XHTML}}}div", id="book-columns")
        inner = etree.SubElement(columns, f"{{{XHTML}}}div", id="book-inner")
        inner.text = body.text
        body.text = None
        for child in list(body):
            body.remove(child)
            inner.append(child)
        body.append(columns)
    _process(body, _Counter())
    return etree.tostring(tree, xml_declaration=True, encoding="utf-8")


def kepubify(src: Path, dst: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="kepub-"))
    try:
        with zipfile.ZipFile(src) as zin:
            names = zin.namelist()
            zin.extractall(tmp)
        for name in names:
            if name.lower().endswith((".xhtml", ".html", ".htm")) and not name.endswith("nav.xhtml"):
                path = tmp / name
                path.write_bytes(kepubify_xhtml(path.read_bytes()))
        with zipfile.ZipFile(dst, "w") as zout:
            # EPUB spec: mimetype first and uncompressed.
            zout.write(tmp / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
            for name in names:
                if name != "mimetype":
                    zout.write(tmp / name, name, compress_type=zipfile.ZIP_DEFLATED)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    kepubify(Path(sys.argv[1]), Path(sys.argv[2]))
