# personal-expense-analytics-engine
Python application for automated expense tracking, categorization, and financial analytics.


The goal is to develop a Python-based web application that helps users understand their spending habits and identify potential savings opportunities. The application will retrieve transaction data through a Polish bank's Open Banking API, process and categorize expenses, detect recurring payments and unusual spending patterns, and provide AI-powered financial insights and personalized savings recommendations. Users will be able to access the application through a web browser on both computers and smartphones.

The main technologies we plan to use are Python, Streamlit, pandas, SQLite, Plotly, pytest, and an AI model or API for generating spending insights. We will also investigate the availability of banking API sandbox environments(as direct access to real transaction data is restricted) and their access requirements.

**Proposed implementation plan:**

- **Session 1 – Project setup and architecture:** Define requirements, create the GitHub repository, design the application architecture, and implement the transaction data model.
- **Session 2 – Banking API integration:** Investigate available Polish Open Banking APIs, configure a suitable sandbox environment, and implement transaction retrieval and authentication where possible.
- **Session 3 – Transaction processing:** Normalize transaction data, detect duplicates, categorize expenses, and identify recurring payments.
- **Session 4 – Database and analytics:** Implement SQLite storage, calculate spending statistics, analyze monthly trends, and detect unusual spending patterns.
- **Session 5 – Technical presentation:** Present the project architecture, technologies, API integration approach, AI components, and implementation progress.
- **Session 6 – Web application and AI features:** Develop the browser interface, implement interactive visualizations, and integrate AI-generated spending insights and personalized savings recommendations based on calculated financial statistics.
- **Session 7 – Testing and finalization:** Add automated tests, improve error handling and logging, validate the AI-generated insights, finalize the documentation, and demonstrate the completed application.

## Current milestone: Session 1 completion

The initial project skeleton is now in place.

- Project structure and Python packaging are configured.
- Core architecture is documented in [docs/architecture.md](docs/architecture.md).
- Transaction and category data models are implemented in [src/personal_expense_analytics_engine/models.py](src/personal_expense_analytics_engine/models.py).
- A first automated test suite validates the transaction behavior.

## Development setup

With [Just](https://just.systems/) installed, list the available project commands
with `just`. Install the package and development dependencies, then run tests:

```shell
just install
just test
just typecheck
```

Run one test file with `just test-file tests/test_models.py`.
`just typecheck` runs Pyright's static type checks on `src` and `tests`.

## Enable Banking integration

Enable Banking supplies the bank list for each country and handles the bank-specific
consent flow. Register a sandbox application in the Enable Banking Control Panel,
configure its redirect URL, and keep the downloaded private key outside source control.
Copy `.env.example` to `.env` and fill in the application ID, private-key path, and
the exact redirect URL registered for the application.

The integration is implemented in [src/personal_expense_analytics_engine/enablebanking.py](src/personal_expense_analytics_engine/enablebanking.py).
It lists supported banks, starts authorization, exchanges the callback code for an
account session, and fetches paginated transactions. Map transactions with
[enablebanking_item_to_transaction](src/personal_expense_analytics_engine/enablebanking_mapper.py)
and store them with
[sync_account](src/personal_expense_analytics_engine/sync.py).
For local consent testing, the `enablebanking_cli` command generates and retains a
random OAuth `state`, opens the authorization URL, verifies the returned state, and
exchanges the callback code for a session. It listens only on `127.0.0.1`, uses the
selected account UID to fetch the requested transactions, and stores them in SQLite.
It does not persist the Enable Banking session response.

The live authorization flow requires an Enable Banking application and a registered
redirect URL; automated tests use mocked API responses and do not contact a bank.

For the local command, register a loopback HTTP redirect URL such as
`http://127.0.0.1:8765/callback` in the Enable Banking application and set the exact
same URL as `ENABLE_BANKING_REDIRECT_URL`. From the project root, pass the date range
to fetch explicitly. For the synthetic sample account, use dates matching its data:

```shell
python -m personal_expense_analytics_engine.enablebanking_cli \
  --bank "Mock ASPSP" --country PL \
  --date-from 2020-09-01 --date-to 2020-09-30
```

The command requests one day of consent, waits up to five minutes for the browser
callback, and creates `data/expenses.db` if it does not exist. It selects the only
linked account automatically or prompts when there are multiple accounts. The same
flow is available as
`just authorize "Mock ASPSP" PL 2020-09-01 2020-09-30`.

Example usage:

```python
from datetime import date

from personal_expense_analytics_engine.models import Transaction

transaction = Transaction(
    transaction_id="txn-100",
    date=date(2026, 10, 14),
    description="Tesco supermarket",
    amount_minor=4250,
    currency="PLN",
    category="Groceries",
)

print(transaction.signed_amount_minor)
```

Transaction amounts are stored as integer minor units (for example, 4250 grosze
is 42.50 PLN). The `amount` and `signed_amount` properties are Decimal values
for display only; use the minor-unit fields for storage and arithmetic.
