[windows]
set shell := ["powershell.exe", "-NoLogo", "-Command"]

# Prefer the project virtualenv so recipes work without activating it.
python := if path_exists(".venv/bin/python") == "true" {
    ".venv/bin/python"
} else if path_exists(".venv/Scripts/python.exe") == "true" {
    ".venv/Scripts/python.exe"
} else if os_family() == "windows" {
    "python"
} else {
    "python3"
}

default:
    @just --list

install:
    {{python}} -X pycache_prefix=.artifact/pycache -m pip install -e ".[dev,web]"

test:
    {{python}} -X pycache_prefix=.artifact/pycache -m pytest

typecheck:
    pyright --pythonpath {{python}}

authorize BANK COUNTRY FROM TO:
    {{python}} -X pycache_prefix=.artifact/pycache -m personal_expense_analytics_engine._enablebanking_cli --bank "{{BANK}}" --country "{{COUNTRY}}" --date-from "{{FROM}}" --date-to "{{TO}}"

analyze MONTH CURRENCY:
    {{python}} -X pycache_prefix=.artifact/pycache -m personal_expense_analytics_engine._analyst_cli --month "{{MONTH}}" --currency "{{CURRENCY}}"

dashboard:
    {{python}} -X pycache_prefix=.artifact/pycache -m streamlit run src/personal_expense_analytics_engine/_dashboard.py

import-json FILE DB="data/synthetic.db":
    {{python}} -X pycache_prefix=.artifact/pycache -m personal_expense_analytics_engine._import_json "{{FILE}}" --database "{{DB}}"

categorize:
    {{python}} -X pycache_prefix=.artifact/pycache -m personal_expense_analytics_engine._categorization_cli

test-file FILE:
    {{python}} -X pycache_prefix=.artifact/pycache -m pytest "{{FILE}}"
