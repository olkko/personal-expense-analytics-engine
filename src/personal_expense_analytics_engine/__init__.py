"""Personal expense analytics engine.

Public API: everything listed in ``__all__`` is meant to be imported from the package
root by the UI, the AI layer and scripts. Modules whose names start with an underscore
are internal and may change without notice.
"""

from ._categorization import category_type
from ._duplicates import find_probable_duplicates
from ._enablebanking import (
    EnableBankingClient,
    EnableBankingConfig,
    EnableBankingError,
)
from ._models import Category, CategoryType, Transaction, TransactionDirection
from ._monobank import MonobankClient, MonobankError
from ._processing import categorize_stored, find_recurring_stored
from ._recurring import RecurringPayment
from ._storage import connect, load_transactions, set_category
from ._sync import sync_account, sync_account_range

__all__ = [
    "Category",
    "CategoryType",
    "EnableBankingClient",
    "EnableBankingConfig",
    "EnableBankingError",
    "MonobankClient",
    "MonobankError",
    "RecurringPayment",
    "Transaction",
    "TransactionDirection",
    "categorize_stored",
    "category_type",
    "connect",
    "find_probable_duplicates",
    "find_recurring_stored",
    "load_transactions",
    "set_category",
    "sync_account",
    "sync_account_range",
]
