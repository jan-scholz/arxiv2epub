.PHONY: install run test clean clean-all

IMAGE ?= arxiv2epub
URL ?= https://arxiv.org/abs/2609.13443

install:
	uv sync
	docker build -t $(IMAGE) docker

run:
	uv run arxiv2epub $(URL)

test:
	uv run pytest -q

clean:
	rm -rf .venv .pytest_cache build *.egg-info __pycache__ tests/__pycache__ .work out

clean-all: clean
	-docker rmi $(IMAGE)
	-docker rmi ghcr.io/dginev/latexml-oxide:0.7.6
