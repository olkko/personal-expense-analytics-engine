from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class TransactionDirection(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class CategoryType(str, Enum):
    FIXED = "fixed"
    VARIABLE = "variable"
    SAVINGS = "savings"
    INCOME = "income"
    TRANSFER = "transfer"


@dataclass(frozen=True)
class Category:
    name: str
    category_type: CategoryType = CategoryType.VARIABLE

    def __post_init__(self) -> None:
        normalized_name = self.name.strip()
        if not normalized_name:
            raise ValueError("Category name cannot be empty.")
        object.__setattr__(self, "name", normalized_name)


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    date: date
    description: str
    amount: Decimal
    currency: str = "PLN"
    category: str | None = None
    merchant: str | None = None
    direction: TransactionDirection = TransactionDirection.DEBIT

    def __post_init__(self) -> None:
        transaction_id = self.transaction_id.strip()
        if not transaction_id:
            raise ValueError("Transaction ID cannot be empty.")
        object.__setattr__(self, "transaction_id", transaction_id)

        description = self.description.strip()
        if not description:
            raise ValueError("Transaction description cannot be empty.")
        object.__setattr__(self, "description", description)

        if not isinstance(self.amount, Decimal):
            raise TypeError("Transaction amount must be a Decimal.")
        if not self.amount.is_finite():
            raise ValueError("Transaction amount must be finite.")

        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("Currency must be a three-letter code.")
        object.__setattr__(self, "currency", currency)

        if self.category is not None:
            category = self.category.strip()
            object.__setattr__(self, "category", category or None)

        if self.merchant is not None:
            merchant = self.merchant.strip()
            object.__setattr__(self, "merchant", merchant or None)

    @property
    def is_expense(self) -> bool:
        return self.direction == TransactionDirection.DEBIT

    @property
    def signed_amount(self) -> Decimal:
        if self.direction == TransactionDirection.CREDIT:
            return self.amount
        return -self.amount
