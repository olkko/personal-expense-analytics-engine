default:
    @just --list

install:
    python -m pip install -e ".[dev]"

test:
    python -m pytest

typecheck:
    pyright

authorize BANK COUNTRY FROM TO:
    python -m personal_expense_analytics_engine.enablebanking_cli --bank "{{BANK}}" --country "{{COUNTRY}}" --date-from "{{FROM}}" --date-to "{{TO}}"

test-file FILE:
    python -m pytest "{{FILE}}"
