import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker"))
import convert  # noqa: E402


def test_strip_running_headers_removes_page_chrome_but_keeps_body():
    pages = [
        f"Nature | Vol 631 | 25 July 2024 | {750 + n}\n# Article\n\nBody text of page {n}.\n\n"
        f"Step {n}: a short body line that only differs by a digit.\n" + "More body.\n" * 5
        + f"Nature | Vol 631 | 25 July 2024 | {750 + n}\n"
        for n in range(1, 5)
    ]
    out = convert.strip_running_headers(pages)
    assert "Nature | Vol" not in out
    assert "# Article" not in out
    for n in range(1, 5):
        assert f"Body text of page {n}." in out
        assert f"Step {n}:" in out  # not at a page edge -> never treated as chrome


def test_drop_title_heading_only_when_it_matches_the_title():
    md = "Journal chrome\n\n# **AI models collapse when trained on recursively generated data**\n\ntext\n# Real section\n"
    out = convert.drop_title_heading(md, "AI models collapse when trained on recursively generated data")
    assert "collapse" not in out.split("# Real section")[0]
    assert "# Real section" in out
    unrelated = "# Some other heading\ntext\n"
    assert convert.drop_title_heading(unrelated, "Paper title") == unrelated


def test_stash_mathml_keeps_latexml_markup_and_adds_namespace():
    html = ('<p>see (<math id="m1" alttext="\\lx@sectionsign" display="inline">'
            '<semantics><mi>§</mi><annotation encoding="application/x-tex">\\lx@sectionsign</annotation>'
            '</semantics></math> 2.1)</p>')
    out, stash = convert.stash_mathml(html)
    assert out == '<p>see (<span class="mathph" data-i="0">​</span> 2.1)</p>'
    assert len(stash) == 1
    assert stash[0].startswith('<math xmlns="http://www.w3.org/1998/Math/MathML" id="m1"')
    assert "annotation" not in stash[0] and "alttext" not in stash[0]
    assert "<mi>§</mi>" in stash[0]


def test_stash_mathml_leaves_malformed_math_to_pandoc():
    html = "<p><math><mi>x</math></p>"
    out, stash = convert.stash_mathml(html)
    assert out == html and stash == []
