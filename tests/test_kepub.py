import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker"))
from kepub import kepubify_xhtml  # noqa: E402

DOC = b"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>
<body><p>First sentence. Second one! <em>Emph</em> tail.</p>
<p><math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math></p>
<pre>a. b.
c.</pre></body></html>"""


def test_kepubify_wraps_sentences_and_keeps_math():
    out = kepubify_xhtml(DOC).decode()
    assert 'id="book-columns"' in out and 'id="book-inner"' in out
    assert '<span class="koboSpan" id="kobo.4.1">First sentence. </span>' in out
    assert '<span class="koboSpan" id="kobo.4.2">Second one! </span>' in out
    assert '<span class="koboSpan" id="kobo.4.4"> tail.</span>' in out
    # MathML is left untouched, and <pre> content is not split.
    assert "<mi>x</mi>" in out and "kobo." not in out.split("<math")[1].split("</math>")[0]
    assert '<span class="koboSpan" id="kobo.6.1">a. b.\nc.</span>' in out


def test_kepubify_is_idempotent():
    once = kepubify_xhtml(DOC)
    assert kepubify_xhtml(once) == once
