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
