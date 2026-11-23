"""Personal expense analytics engine.

Public API: everything listed in ``__all__`` is meant to be imported from the package
root by the UI, the AI layer and scripts. Modules whose names start with an underscore
are internal and may change without notice.
"""

from ._analyst import (
    CategoryInsight,
    MonthlySpendingInsight,
    monthly_spending_insight,
)
from ._analytics import (
    CategoryTotal,
    MonthChange,
    MonthlyTotal,
    UnusualTransaction,
    category_totals,
    detect_unusual_stored,
    format_minor,
    month_over_month,
    monthly_totals,
    partial_months,
)
from ._categorization import category_type
from ._duplicates import find_probable_duplicates
from ._enablebanking import (
    EnableBankingClient,
    EnableBankingConfig,
    EnableBankingError,
)
from ._models import Category, CategoryType, Transaction, TransactionDirection
from ._ollama import OllamaAnalystError
from ._processing import (
    categorize_stored,
    categorize_stored_with_ollama,
    find_recurring_stored,
)
from ._recurring import RecurringPayment
from ._storage import connect, load_transactions, set_category
from ._sync import sync_account, sync_account_range

__all__ = [
    "CategoryInsight",
    "CategoryTotal",
    "MonthChange",
    "MonthlyTotal",
    "UnusualTransaction",
    "category_totals",
    "detect_unusual_stored",
    "format_minor",
    "month_over_month",
    "monthly_spending_insight",
    "monthly_totals",
    "partial_months",
    "Category",
    "CategoryType",
    "EnableBankingClient",
    "EnableBankingConfig",
    "EnableBankingError",
    "MonthlySpendingInsight",
    "OllamaAnalystError",
    "RecurringPayment",
    "Transaction",
    "TransactionDirection",
    "categorize_stored",
    "categorize_stored_with_ollama",
    "category_type",
    "connect",
    "find_probable_duplicates",
    "find_recurring_stored",
    "load_transactions",
    "set_category",
    "sync_account",
    "sync_account_range",
]
