# Conversion toolbox for arxiv2epub. Everything that needs TeX lives here so
# the host only needs Docker + uv.
#
#   latexml-oxide : the Rust LaTeXML rewrite arXiv itself uses for its HTML
#                   view (ships with a full TeX Live + graphics tools)
#   pandoc        : HTML/Markdown -> EPUB3 with MathML (pinned upstream build;
#                   Ubuntu's 3.1 is too old for --math-method/--split-level)
#   pymupdf4llm   : last-resort PDF -> Markdown for papers with no usable source
# Pinned: latexml-oxide is in public beta, and 0.7.6 is the release arXiv's own
# HTML view is generated with. Bump deliberately, then re-test both routes.
ARG LATEXML_OXIDE_VERSION=0.7.6
FROM ghcr.io/dginev/latexml-oxide:${LATEXML_OXIDE_VERSION}

ARG PANDOC_VERSION=3.11

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      curl ca-certificates python3-pip python3-lxml librsvg2-bin fonts-dejavu-core \
 && arch="$(dpkg --print-architecture)" \
 && curl -fsSL -o /tmp/pandoc.deb \
      "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${arch}.deb" \
 && dpkg -i /tmp/pandoc.deb && rm /tmp/pandoc.deb \
 && rm -rf /var/lib/apt/lists/* \
 && pip3 install --no-cache-dir --break-system-packages pymupdf4llm

COPY convert.py filter.lua epub.css kepub.py /opt/arxiv2epub/

WORKDIR /work
ENTRYPOINT ["python3", "/opt/arxiv2epub/convert.py"]
