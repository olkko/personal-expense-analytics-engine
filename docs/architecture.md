# Architecture overview

This project is structured around a simple data-processing pipeline for personal finance analysis.

## Components

1. Banking API integration
   - Connects to a supported sandbox or mock Open Banking API.
   - Retrieves transactions, merchant names, dates, and account metadata.

2. Normalization layer
   - Converts transaction payloads into a consistent internal schema.
   - Handles duplicate records, date parsing, and currency normalization.

3. Categorisation engine
   - Uses keyword-based rules and recurring patterns to map transactions to categories.
   - Separates expenses, income, transfers, and savings.

4. Storage and analytics
   - Persists transactions in SQLite and calculates trends, averages, and anomalies.
   - Prepares data for recurring-payment detection and spending summaries.

5. Web dashboard
   - Presents visualizations and AI-backed recommendations in a Streamlit interface.
   - Targets both desktop and mobile browsers.

## Core data structures

- Category: defines a label and spending type.
- Transaction: stores a dated transaction, its non-negative integer amount in minor currency units, currency, category, and debit/credit direction. Signed arithmetic uses `signed_amount_minor`; `amount` and `signed_amount` are Decimal display helpers derived from minor units. The current minor-unit exponent is 2 for PLN, UAH, USD, and EUR.

For example, `amount_minor=4250` represents 42.50 PLN.

## Planned flow

`Banking data -> normalization -> categorization -> SQLite -> analytics -> dashboard`
