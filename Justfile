default:
    @just --list

install:
    python -m pip install -e ".[dev]"

test:
    python -m pytest

typecheck:
    pyright

test-file FILE:
    python -m pytest "{{FILE}}"
