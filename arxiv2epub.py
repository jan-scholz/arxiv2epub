#!/usr/bin/env python3
"""Download an arXiv paper and turn it into a Kobo-friendly (k)epub.

Sources are tried in order of fidelity:

  1. arXiv's own HTML view (LaTeXML output with MathML + figures)
  2. the TeX e-print, converted with latexml-oxide inside Docker
  3. the PDF, text-extracted inside Docker (last resort; math will be rough)

The host side (this file) only needs the Python standard library and Docker;
everything TeX-related runs in the ``arxiv2epub`` image (see docker/).
The output file is named after the paper's title.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

USER_AGENT = "arxiv2epub/0.1 (personal e-reader conversion)"
DEFAULT_IMAGE = "arxiv2epub"
ATOM = "{http://www.w3.org/2005/Atom}"

ARXIV_ID_RE = re.compile(
    r"(?:(?<![\w.])(?P<new>\d{4}\.\d{4,5})(?P<newv>v\d+)?)"
    r"|(?:(?P<old>[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?P<oldv>v\d+)?)"
)
ARTICLE_RE = re.compile(r"<article\b.*?</article>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
ASSET_RE = re.compile(r'(<(?:img|object)\b[^>]*?\b(?:src|data)=")([^"]+)(")', re.S)


@dataclass
class Paper:
    arxiv_id: str  # with version, e.g. 1611.03530v2
    title: str
    authors: list[str]
    date: str
    abstract: str
    url: str

    @property
    def bare_id(self) -> str:
        return re.sub(r"v\d+$", "", self.arxiv_id)


@dataclass
class Source:
    mode: str  # html | tex | pdf
    input: Path  # relative to the work dir
    note: str = ""


# --------------------------------------------------------------------------- #
# arXiv access
# --------------------------------------------------------------------------- #

def parse_arxiv_id(text: str) -> str:
    text = text.strip()
    m = ARXIV_ID_RE.search(text)
    if not m:
        raise SystemExit(f"cannot find an arXiv identifier in {text!r}")
    if m.group("new"):
        return m.group("new") + (m.group("newv") or "")
    return m.group("old") + (m.group("oldv") or "")


def http_get(url: str) -> tuple[str, bytes, str] | None:
    """Return (final_url, body, content_type), or None on 404."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.geturl(), resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch_metadata(arxiv_id: str) -> Paper:
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id})
    got = http_get(url)
    if not got:
        raise SystemExit(f"arXiv API returned nothing for {arxiv_id}")
    root = ET.fromstring(got[1])
    entry = root.find(ATOM + "entry")
    if entry is None or entry.find(ATOM + "title") is None:
        raise SystemExit(f"no arXiv entry found for {arxiv_id}")
    title = entry.findtext(ATOM + "title") or ""
    if title.strip() == "Error":
        raise SystemExit(f"arXiv API error for {arxiv_id}: {entry.findtext(ATOM + 'summary')}")
    versioned = (entry.findtext(ATOM + "id") or "").rsplit("/abs/", 1)[-1]
    return Paper(
        arxiv_id=versioned or arxiv_id,
        title=" ".join(title.split()),
        authors=[" ".join((a.findtext(ATOM + "name") or "").split()) for a in entry.findall(ATOM + "author")],
        date=(entry.findtext(ATOM + "published") or "")[:10],
        abstract=" ".join((entry.findtext(ATOM + "summary") or "").split()),
        url=f"https://arxiv.org/abs/{versioned or arxiv_id}",
    )


# --------------------------------------------------------------------------- #
# Source acquisition
# --------------------------------------------------------------------------- #

def looks_substantial(article_html: str) -> bool:
    """arXiv serves a near-empty page for PDF-only submissions (e.g. a
    ``\\includepdf`` wrapper); don't mistake that for a real conversion."""
    text = " ".join(TAG_RE.sub(" ", article_html).split())
    if re.search(r"See pages .{0,40}\.pdf", text):
        return False
    return len(text) > 1500 and article_html.count("ltx_para") >= 3


def try_html(paper: Paper, work: Path) -> Source | None:
    got = http_get(f"https://arxiv.org/html/{paper.arxiv_id}")
    if not got:
        return None
    final_url, body, _ = got
    html = body.decode("utf-8", errors="replace")
    m = ARTICLE_RE.search(html)
    if not m or not looks_substantial(m.group(0)):
        return None
    article = m.group(0)

    html_dir = work / "html"
    html_dir.mkdir(exist_ok=True)
    downloaded: dict[str, str] = {}

    def fetch_asset(m: re.Match) -> str:
        src = m.group(2)
        if src.startswith("data:"):
            return m.group(0)
        if src not in downloaded:
            asset_url = urllib.parse.urljoin(final_url, src)
            name = Path(urllib.parse.urlparse(asset_url).path).name or "asset"
            local = html_dir / f"{len(downloaded)}_{name}"
            try:
                asset = http_get(asset_url)
            except urllib.error.URLError as e:
                asset = None
                print(f"  ! could not fetch {asset_url}: {e}", file=sys.stderr)
            if asset:
                local.write_bytes(asset[1])
                downloaded[src] = local.name
            else:
                downloaded[src] = src
        return f"{m.group(1)}{downloaded[src]}{m.group(3)}"

    article = ASSET_RE.sub(fetch_asset, article)
    (html_dir / "paper.html").write_text(article, encoding="utf-8")
    return Source("html", Path("html/paper.html"), f"arXiv HTML ({len(downloaded)} assets)")


def _find_main_tex(tex_files: dict[str, str]) -> str | None:
    candidates = [n for n, t in tex_files.items() if r"\begin{document}" in t]
    if not candidates:
        return None
    # Prefer the one that \input's others, then a conventional name, then the largest.
    def score(name: str) -> tuple[int, int, int]:
        text = tex_files[name]
        inputs = len(re.findall(r"\\(?:input|include)\b", text))
        conventional = int(Path(name).stem.lower() in {"main", "paper", "ms", "article", "manuscript"})
        return (inputs, conventional, len(text))
    return max(candidates, key=score)


def _is_pdf_wrapper(main_tex: str) -> bool:
    body = main_tex.split(r"\begin{document}", 1)[-1].split(r"\end{document}", 1)[0]
    body = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("%"))
    return r"\includepdf" in body and len(body.strip()) < 400


def try_tex(paper: Paper, work: Path) -> Source | None:
    got = http_get(f"https://arxiv.org/e-print/{paper.arxiv_id}")
    if not got:
        return None
    _, body, ctype = got
    src_dir = work / "src"
    src_dir.mkdir(exist_ok=True)

    if body[:5] == b"%PDF-":
        (work / "paper.pdf").write_bytes(body)
        return Source("pdf", Path("paper.pdf"), "e-print is PDF only")

    try:
        raw = gzip.decompress(body)
    except OSError:
        raw = body  # arXiv occasionally serves an uncompressed tar

    tex_files: dict[str, str] = {}
    pdf_members: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            tar.extractall(src_dir, filter="data")
            for m in tar.getmembers():
                if m.isfile() and m.name.lower().endswith(".tex"):
                    tex_files[m.name] = (src_dir / m.name).read_text(encoding="utf-8", errors="replace")
                elif m.isfile() and m.name.lower().endswith(".pdf"):
                    pdf_members.append(m.name)
        archive = work / "src.tar.gz"
        archive.write_bytes(body if body[:2] == b"\x1f\x8b" else gzip.compress(raw))
        source_input = Path("src.tar.gz")
    except tarfile.ReadError:
        # Single gzipped .tex file.
        if raw[:5] == b"%PDF-":
            (work / "paper.pdf").write_bytes(raw)
            return Source("pdf", Path("paper.pdf"), "e-print is PDF only")
        single = src_dir / "main.tex"
        single.write_bytes(raw)
        tex_files["main.tex"] = raw.decode("utf-8", errors="replace")
        source_input = Path("src/main.tex")

    main = _find_main_tex(tex_files)
    if main is None:
        return None
    if _is_pdf_wrapper(tex_files[main]):
        if pdf_members:
            return Source("pdf", Path("src") / pdf_members[0], "TeX source is only an \\includepdf wrapper")
        return None
    return Source("tex", source_input, f"TeX source, main file {main}")


def try_pdf(paper: Paper, work: Path) -> Source | None:
    got = http_get(f"https://arxiv.org/pdf/{paper.arxiv_id}")
    if not got or got[1][:5] != b"%PDF-":
        return None
    (work / "paper.pdf").write_bytes(got[1])
    return Source("pdf", Path("paper.pdf"), "arXiv PDF")


def acquire(paper: Paper, work: Path, preference: str) -> Source:
    order = {
        "auto": [try_html, try_tex, try_pdf],
        "html": [try_html],
        "tex": [try_tex],
        "pdf": [try_pdf],
    }[preference]
    for attempt in order:
        print(f"  trying {attempt.__name__[4:]} ...", file=sys.stderr)
        src = attempt(paper, work)
        if src:
            return src
    raise SystemExit(f"no usable source for {paper.arxiv_id} (tried: {preference})")


# --------------------------------------------------------------------------- #
# Conversion and output
# --------------------------------------------------------------------------- #

def safe_filename(title: str, limit: int = 180) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title)
    name = " ".join(name.split()).strip(" .")
    return name[:limit].rstrip(" .") or "paper"


def ensure_docker_image(image: str) -> None:
    """Fail fast, before any downloads, if the conversion image is missing."""
    try:
        probe = subprocess.run(["docker", "image", "inspect", image],
                               capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit("docker is not installed or not on PATH; it is needed for the conversion step")
    if probe.returncode != 0:
        raise SystemExit(
            f"Docker image {image!r} not found locally.\n"
            f"Build it with `make install` or point --image at an existing image."
        )


def run_docker(work: Path, image: str, timeout: int, verbose: bool) -> None:
    cmd = ["docker", "run", "--rm", "-v", f"{work}:/work", image]
    if verbose:
        print("  $ " + " ".join(cmd), file=sys.stderr)
        subprocess.run(cmd, check=True, timeout=timeout)
        return
    # Quiet mode: keep the converter's chatter unless it actually fails.
    result = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])
        raise SystemExit(f"conversion failed (docker exit {result.returncode}); last output:\n{tail}")


def convert_one(ref: str, args: argparse.Namespace) -> Path:
    arxiv_id = parse_arxiv_id(ref)
    paper = fetch_metadata(arxiv_id)
    print(f"{paper.arxiv_id}: {paper.title}", file=sys.stderr)

    work = Path(args.work_dir) / paper.arxiv_id if args.work_dir else Path(tempfile.mkdtemp(prefix="arxiv2epub-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        src = acquire(paper, work, args.source)
        print(f"  using {src.mode}: {src.note}", file=sys.stderr)
        meta = {
            "mode": src.mode,
            "input": str(src.input),
            "kepub": not args.plain_epub,
            "timeout": args.timeout,
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "authors": paper.authors,
            "date": paper.date,
            "abstract": paper.abstract,
            "url": paper.url,
        }
        (work / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        run_docker(work.resolve(), args.image, args.timeout + 120, args.verbose)

        suffix = ".epub" if args.plain_epub else ".kepub.epub"
        produced = work / ("out.epub" if args.plain_epub else "out.kepub.epub")
        if not produced.exists():
            raise SystemExit("conversion produced no output; rerun with --work-dir to inspect")
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / (safe_filename(paper.title) + suffix)
        shutil.copyfile(produced, dest)
        return dest
    finally:
        if not args.work_dir:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("papers", nargs="+", help="arXiv URLs or identifiers")
    p.add_argument("-o", "--output-dir", default=".", help="where to put the .epub (default: cwd)")
    p.add_argument("--source", choices=["auto", "html", "tex", "pdf"], default="auto",
                   help="force a particular source instead of html -> tex -> pdf fallback")
    p.add_argument("--plain-epub", action="store_true",
                   help="write a plain .epub instead of a Kobo .kepub.epub (no MathML on Kobo)")
    p.add_argument("--image", default=DEFAULT_IMAGE, help="docker image to use (default: %(default)s)")
    p.add_argument("--timeout", type=int, default=900, help="TeX conversion timeout in seconds")
    p.add_argument("--work-dir", help="keep intermediate files under this directory (for debugging)")
    p.add_argument("-v", "--verbose", action="store_true", help="show the docker command and the converter's output")
    args = p.parse_args(argv)
    ensure_docker_image(args.image)

    failures = 0
    for ref in args.papers:
        try:
            dest = convert_one(ref, args)
            print(dest)
        except (SystemExit, subprocess.SubprocessError, urllib.error.URLError) as e:
            failures += 1
            print(f"FAILED {ref}: {e}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
