"""Money as integer minor units to avoid floating point rounding."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    cents: int
    currency: str = "EUR"

    def __add__(self, other: "Money") -> "Money":
        if other.currency != self.currency:
            raise ValueError("currency mismatch")
        return Money(self.cents + other.cents, self.currency)

    def times(self, quantity: int) -> "Money":
        return Money(self.cents * quantity, self.currency)
