import pytest

import arxiv2epub as a


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("https://arxiv.org/abs/1611.03530", "1611.03530"),
        ("https://arxiv.org/pdf/2609.13443v1", "2609.13443v1"),
        ("arxiv.org/html/2609.13443v2/", "2609.13443v2"),
        ("1611.03530", "1611.03530"),
        ("hep-th/9901001v2", "hep-th/9901001v2"),
        ("https://arxiv.org/abs/math.GT/0309136", "math.GT/0309136"),
    ],
)
def test_parse_arxiv_id(ref, expected):
    assert a.parse_arxiv_id(ref) == expected


def test_parse_arxiv_id_rejects_garbage():
    with pytest.raises(SystemExit):
        a.parse_arxiv_id("not a paper")


def test_safe_filename_strips_unsafe_characters():
    assert a.safe_filename('Deep Learning: A/B "Test"?  ') == "Deep Learning A B Test"
    assert a.safe_filename("x" * 400).__len__() == 180
    assert a.safe_filename("...") == "paper"


def test_pdf_wrapper_detection():
    wrapper = r"""\documentclass{article}
\usepackage{pdfpages}
\begin{document}
% arXiv's TeX is too old, so ship the PDF
\includepdf[pages=1-last]{paper.pdf}
\end{document}"""
    assert a._is_pdf_wrapper(wrapper)
    real = r"\begin{document}\section{Intro}" + "Lots of prose. " * 100 + r"\end{document}"
    assert not a._is_pdf_wrapper(real)


def test_find_main_tex_prefers_document_with_inputs():
    files = {
        "intro.tex": r"\section{Intro} text",
        "main.tex": r"\documentclass{article}\begin{document}\input{intro}\end{document}",
        "old_main.tex": r"\documentclass{article}\begin{document}unused draft\end{document}",
    }
    assert a._find_main_tex(files) == "main.tex"
    assert a._find_main_tex({"notes.tex": "no document here"}) is None


def test_looks_substantial_rejects_includepdf_stub():
    stub = '<article><div class="ltx_para"><p>See pages 1-last of <a href="x.pdf">x.pdf</a></p></div></article>'
    assert not a.looks_substantial(stub)
    body = "".join(f'<div class="ltx_para"><p>{"word " * 100}</p></div>' for _ in range(5))
    assert a.looks_substantial(f"<article>{body}</article>")
