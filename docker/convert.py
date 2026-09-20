"""In-container conversion step: /work/meta.json -> /work/out.epub.

The host script (arxiv2epub.py) does all the network I/O and writes the
paper's source plus a meta.json into /work. Depending on ``mode`` we then:

  html  pandoc the (already extracted) arXiv HTML article
  tex   run latexml-oxide on the e-print archive first, then as ``html``
  pdf   pymupdf4llm -> Markdown -> pandoc (last resort, lower fidelity)
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
WORK = Path("/work")

ARTICLE_RE = re.compile(r"<article\b.*?</article>", re.S)
SVG_OBJECT_RE = re.compile(r'<object\b([^>]*?)type="image/svg\+xml"([^>]*?)data="([^"]+)"([^>]*)>\s*</object>', re.S)
IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")')


def log(msg):
    print(f"[convert] {msg}", file=sys.stderr, flush=True)


def run(cmd, **kw):
    log(" ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def extract_article(html: str) -> str:
    m = ARTICLE_RE.search(html)
    return m.group(0) if m else html


def rasterize_svgs(html: str, base: Path) -> str:
    """Kobo's SVG support is patchy (esp. text in figures): render to PNG."""
    html = SVG_OBJECT_RE.sub(r'<img\1\2src="\3"\4>', html)

    def repl(m):
        src = m.group(2)
        if not src.lower().endswith(".svg"):
            return m.group(0)
        svg = base / src
        png = svg.with_suffix(".png")
        if not png.exists():
            try:
                run(["rsvg-convert", "-w", "1400", "--keep-aspect-ratio", str(svg), "-o", str(png)])
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                log(f"svg rasterization failed for {src}: {e}; keeping svg")
                return m.group(0)
        return f"{m.group(1)}{png.relative_to(base)}{m.group(3)}"

    return IMG_SRC_RE.sub(repl, html)


def make_cover(meta, path: Path) -> bool:
    title = meta["title"]
    authors = ", ".join(meta.get("authors", []))
    try:
        run([
            "convert", "-size", "1200x1600", "xc:white",
            "-gravity", "center", "-fill", "black",
            "-pointsize", "72", "-size", "1000x900", f"caption:{title}",
            "-geometry", "+0-200", "-composite",
            "-pointsize", "40", "-size", "1000x400", f"caption:{authors}",
            "-geometry", "+0+400", "-composite",
            "-pointsize", "32", "-size", "1000x100", f"caption:{meta['ident']}",
            "-geometry", "+0+700", "-composite",
            str(path),
        ], capture_output=True)
        return path.exists()
    except subprocess.CalledProcessError as e:
        log(f"cover generation failed: {e.stderr.decode(errors='replace')[:300]}")
        return False


def write_meta_yaml(meta, path: Path):
    def q(s):
        return json.dumps(s, ensure_ascii=False)
    lines = [
        f"title: {q(meta['title'])}",
        "author:",
        *[f"  - {q(a)}" for a in meta.get("authors", [])],
        f"date: {q(meta.get('date', ''))}",
        f"identifier: {q(meta['ident'])}",
        f"description: {q(meta.get('abstract', ''))}",
        f"source: {q(meta.get('url', ''))}",
        "lang: en",
        f"publisher: {q('arXiv')}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def pandoc_to_epub(input_path: Path, fmt: str, meta, out: Path, resource_dirs, lua=True, extra=()):
    meta_yaml = WORK / "meta.yaml"
    write_meta_yaml(meta, meta_yaml)
    cmd = [
        "pandoc", str(input_path), "-f", fmt, "-t", "epub3",
        "--math-method=mathml",
        "--css", str(HERE / "epub.css"),
        "--metadata-file", str(meta_yaml),
        "--toc", "--toc-depth=2", "--split-level=1",
        "--resource-path", ":".join(str(d) for d in resource_dirs),
        "-o", str(out),
    ]
    cmd += list(extra)
    if lua:
        cmd += ["--lua-filter", str(HERE / "filter.lua")]
    cover = WORK / "cover.png"
    if make_cover(meta, cover):
        cmd += ["--epub-cover-image", str(cover)]
    run(cmd)


def convert_html(meta, html_path: Path, base: Path, out: Path):
    html = extract_article(html_path.read_text(encoding="utf-8", errors="replace"))
    html = rasterize_svgs(html, base)
    clean = base / "article.html"
    clean.write_text(html, encoding="utf-8")
    pandoc_to_epub(clean, "html", meta, out, [base])


def convert_tex(meta, src: Path, out: Path):
    dest_dir = WORK / "latexml"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / "paper.html"
    cmd = [
        "latexml_oxide", str(src), "--dest", str(dest), "--format", "html5",
        "--nodefaultresources", "--timeout", str(meta.get("timeout", 900)),
        "--log", str(WORK / "latexml.log"),
    ]
    # latexml-oxide auto-detects .tar.gz/.zip archives and directories.
    result = subprocess.run(cmd, text=True)
    if not dest.exists():
        sys.exit(f"latexml-oxide produced no output (exit {result.returncode}); see latexml.log")
    if result.returncode != 0:
        log(f"latexml-oxide exited {result.returncode}; continuing with partial output")
    convert_html(meta, dest, dest_dir, out)


def strip_running_headers(pages: list[str], min_repeats: int = 3, edge: int = 2) -> str:
    """Join per-page Markdown, dropping short lines that recur at the top or
    bottom of several pages (journal name, running title, page numbers).
    Digits are ignored when comparing so "Nature | Vol 631 | 755" matches
    "... | 756". Only page edges nominate candidates, so body text is never
    mistaken for chrome."""
    def key(line):
        return re.sub(r"\d+", "#", re.sub(r"[*_#]", "", line).strip()).lower()

    def edge_lines(page):
        idx = [i for i, l in enumerate(page) if l.strip()]
        return set(idx[:edge] + idx[-edge:])

    split = [pg.splitlines() for pg in pages]
    counts = {}
    for page in split:
        for i in edge_lines(page):
            k = key(page[i])
            # Prose ends in punctuation; page chrome ("Article", "Nature |
            # Vol 631 | 755") does not.
            if 0 < len(k) < 100 and not k.endswith((".", ",", ";", ":")):
                counts[k] = counts.get(k, 0) + 1
    chrome = {k for k, n in counts.items() if n >= min_repeats}
    # A line confirmed as chrome at the page edges is chrome wherever it
    # appears (pymupdf4llm sometimes merges pages into one chunk).
    return "\n\n".join("\n".join(l for l in page if key(l) not in chrome) for page in split)


def drop_title_heading(md: str, title: str) -> str:
    """Remove the first top-level heading when it is (a prefix of) the paper
    title; it may sit a few lines down, after journal chrome."""
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    lines = md.splitlines(keepends=True)
    for i, line in enumerate(lines[:20]):
        if line.startswith("# "):
            text = norm(line.lstrip("#"))
            if text and (text in norm(title) or norm(title) in text):
                del lines[i]
            break
    return "".join(lines)


def convert_pdf(meta, pdf: Path, out: Path):
    import pymupdf4llm  # noqa: WPS433 (only needed on this path)

    img_dir = WORK / "pdfimg"
    img_dir.mkdir(exist_ok=True)
    chunks = pymupdf4llm.to_markdown(
        str(pdf), write_images=True, image_path=str(img_dir),
        image_format="png", dpi=150, show_progress=False, page_chunks=True,
    )
    md = strip_running_headers([c["text"] for c in chunks])
    # pymupdf4llm emits absolute image paths; make them relative to /work.
    md = md.replace(str(img_dir) + "/", "pdfimg/")
    # Figures are exported as PNGs; the OCR-ish dump of their axis labels
    # that follows each one is just noise on an e-reader.
    md = re.sub(r"<!-- Start of picture text -->.*?<!-- End of picture text -->", "", md, flags=re.S)
    md = re.sub(r"<br\s*/?>", " ", md)
    # The first heading is the paper title (already in the EPUB metadata);
    # drop it, then shift the remaining headings so the top level is 1 and
    # pandoc splits chapters per section.
    md = drop_title_heading(md, meta["title"])
    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,6}) ", md, flags=re.M)]
    shift = 1 - min(levels) if levels else 0
    md_path = WORK / "paper.md"
    md_path.write_text(md, encoding="utf-8")
    # raw_html is disabled: pymupdf4llm emits stray <br>/<sup> tags that would
    # end up as non-XML in the XHTML.
    pandoc_to_epub(md_path, "gfm-raw_html+tex_math_dollars", meta, out, [WORK],
                   lua=False, extra=[f"--shift-heading-level-by={shift}"])


def main():
    meta = json.loads((WORK / "meta.json").read_text(encoding="utf-8"))
    mode = meta["mode"]
    src = WORK / meta["input"]
    out = WORK / "out.epub"
    if mode == "html":
        convert_html(meta, src, src.parent, out)
    elif mode == "tex":
        convert_tex(meta, src, out)
    elif mode == "pdf":
        convert_pdf(meta, src, out)
    else:
        sys.exit(f"unknown mode {mode!r}")
    if meta.get("kepub", True):
        sys.path.insert(0, str(HERE))
        from kepub import kepubify
        kepubify(out, WORK / "out.kepub.epub")
    log("done")


if __name__ == "__main__":
    main()
