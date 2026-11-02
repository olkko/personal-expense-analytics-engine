from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

MINOR_UNIT_EXPONENT = 2


class TransactionDirection(StrEnum):
    """Direction of money movement for a transaction."""

    DEBIT = "debit"
    CREDIT = "credit"


class CategoryType(StrEnum):
    """Spending classification assigned to a category."""

    FIXED = "fixed"
    VARIABLE = "variable"
    SAVINGS = "savings"
    INCOME = "income"
    TRANSFER = "transfer"


@dataclass(frozen=True, slots=True)
class Category:
    """A normalized name and type for a transaction category."""

    name: str
    category_type: CategoryType = CategoryType.VARIABLE

    def __post_init__(self) -> None:
        """Validate and normalize the category after initialization."""
        self._normalize_name()

    def _normalize_name(self) -> None:
        """Trim the category name and reject an empty result."""
        normalized_name = self.name.strip()
        if not normalized_name:
            raise ValueError("Category name cannot be empty.")
        object.__setattr__(self, "name", normalized_name)


@dataclass(frozen=True, slots=True)
class Transaction:
    """A normalized transaction with currency and direction information.

    Money is stored as an integer number of minor units (e.g. grosze, kopiykas).
    """

    transaction_id: str
    date: date
    description: str
    amount_minor: int
    currency: str = "PLN"
    category: str | None = None
    merchant: str | None = None
    direction: TransactionDirection = TransactionDirection.DEBIT

    def __post_init__(self) -> None:
        """Validate and normalize transaction fields after initialization."""
        self._normalize_transaction_id()
        self._normalize_description()
        self._validate_amount()
        self._normalize_currency()
        object.__setattr__(self, "category", self._normalize_optional_text(self.category))
        object.__setattr__(self, "merchant", self._normalize_optional_text(self.merchant))

    def _normalize_transaction_id(self) -> None:
        """Trim the transaction ID and reject an empty result."""
        transaction_id = self.transaction_id.strip()
        if not transaction_id:
            raise ValueError("Transaction ID cannot be empty.")
        object.__setattr__(self, "transaction_id", transaction_id)

    def _normalize_description(self) -> None:
        """Trim the transaction description and reject an empty result."""
        description = self.description.strip()
        if not description:
            raise ValueError("Transaction description cannot be empty.")
        object.__setattr__(self, "description", description)

    def _validate_amount(self) -> None:
        """Require the amount to be a non-negative int (bool is rejected)."""
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise TypeError("Transaction amount_minor must be an int.")
        if self.amount_minor < 0:
            raise ValueError("Transaction amount_minor cannot be negative.")

    def _normalize_currency(self) -> None:
        """Uppercase the currency code and require three alphabetic letters."""
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("Currency must be a three-letter code.")
        object.__setattr__(self, "currency", currency)

    def _normalize_optional_text(self, value: str | None) -> str | None:
        """Trim optional text and convert an empty value to None."""
        if value is None:
            return None
        return value.strip() or None

    @property
    def is_expense(self) -> bool:
        """Return whether this transaction represents a debit."""
        return self.direction == TransactionDirection.DEBIT

    @property
    def signed_amount_minor(self) -> int:
        """Return the amount in minor units, negative for debits."""
        if self.direction == TransactionDirection.CREDIT:
            return self.amount_minor
        return -self.amount_minor

    @property
    def amount(self) -> Decimal:
        """Return the amount as a Decimal. For display only, not for calculations."""
        return Decimal(self.amount_minor) / Decimal(10**MINOR_UNIT_EXPONENT)

    @property
    def signed_amount(self) -> Decimal:
        """Return the signed amount as a Decimal. For display only."""
        return Decimal(self.signed_amount_minor) / Decimal(10**MINOR_UNIT_EXPONENT)
