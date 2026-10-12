"""Payment authorization, capture and refunds against an external provider."""

from dataclasses import dataclass
from typing import Protocol

from .money import Money


class PaymentDeclined(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ProviderClient(Protocol):
    """What we need from the external payment provider's API."""

    def authorize(self, token: str, amount_cents: int, currency: str) -> dict: ...
    def capture(self, authorization_id: str) -> dict: ...
    def refund(self, authorization_id: str, amount_cents: int) -> dict: ...


@dataclass
class Authorization:
    id: str
    amount: Money


class PaymentService:
    def __init__(self, provider: ProviderClient):
        self.provider = provider

    def authorize(self, payment_token: str, amount: Money) -> Authorization:
        """Hold funds for an order. Raises PaymentDeclined; never captures money."""
        if amount.cents <= 0:
            raise ValueError("amount must be positive")
        response = self.provider.authorize(payment_token, amount.cents, amount.currency)
        if response.get("status") != "approved":
            raise PaymentDeclined(response.get("reason", "declined"))
        return Authorization(id=response["id"], amount=amount)

    def capture(self, authorization: Authorization) -> None:
        """Collect previously authorized funds; called when the order ships."""
        self.provider.capture(authorization.id)

    def refund(self, authorization: Authorization, amount: Money) -> None:
        if amount.cents > authorization.amount.cents:
            raise ValueError("cannot refund more than was authorized")
        self.provider.refund(authorization.id, amount.cents)
