# arxiv2epub

Turn arXiv papers into Kobo-friendly `.kepub.epub` files, named after the
paper's title so they are easy to spot when syncing via Google Drive.

```sh
make install                       # uv sync + docker build (one-off, ~2 GB image)
uv run arxiv2epub https://arxiv.org/abs/2609.13443 https://arxiv.org/abs/1611.03530
# -> ./Learning to Solve Hard Problems in RL for LLMs by Never Giving Up.kepub.epub
# -> ./Understanding deep learning requires rethinking generalization.kepub.epub
```

Accepts abs/pdf/html URLs or bare identifiers. `-o DIR` chooses the output
directory (point it at your Drive sync folder).

## How it works

The host script (`arxiv2epub.py`, standard library only) fetches metadata from
the arXiv API and picks the best available source, in this order:

| source | when | converter |
|---|---|---|
| **arXiv HTML** (`arxiv.org/html/<id>`) | exists and is not an empty stub | pandoc → EPUB3 |
| **TeX e-print** | no HTML | [latexml-oxide](https://github.com/dginev/latexml-oxide) (the Rust LaTeXML rewrite arXiv itself uses) → HTML → pandoc |
| **PDF** | source is only an `\includepdf` wrapper, or PDF-only submission | PyMuPDF text/figure extraction → Markdown → pandoc (math will be rough) |

All conversion runs inside the `arxiv2epub` Docker image (`docker/`), so no
TeX ends up on the host. `--source html|tex|pdf` forces a route.

Output is a **kepub** by default: Kobo renders `.kepub.epub` with its own
engine, which — unlike the Adobe renderer used for plain `.epub` — supports
MathML, pop-up footnotes and per-chapter progress. Sentence spans are added
the same way kepubify does. Use `--plain-epub` for a standard EPUB.

## Debugging a conversion

```sh
uv run arxiv2epub --work-dir ./.work <id>   # keeps html/, src/, latexml.log, out.epub …
```

## Development

```sh
make test          # pytest (host-side helpers + kepub post-processor)
make clean         # venv/caches/outputs
make clean-all     # also removes the docker images
```

## License

AGPL-3.0-or-later (see `LICENSE`). The PDF fallback imports
[PyMuPDF](https://pymupdf.readthedocs.io/)/pymupdf4llm, which are AGPL-3.0, so
the project is licensed to match. The other conversion tools are only invoked
as subprocesses: latexml-oxide (CC0) and pandoc (GPL).
