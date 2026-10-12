"""Placing orders: the checkout transaction."""

import uuid
from dataclasses import dataclass, field

from .catalog import Catalog
from .events import EventBus
from .inventory import Inventory, OutOfStock, Reservation
from .money import Money
from .payments import Authorization, PaymentDeclined, PaymentService

MAX_LINES_PER_ORDER = 50


class CheckoutError(Exception):
    pass


@dataclass
class Order:
    id: str
    customer: str
    lines: dict[str, int]
    total: Money
    status: str = "pending"
    reservation: Reservation | None = None
    authorization: Authorization | None = None
    history: list[str] = field(default_factory=list)


class OrderService:
    def __init__(
        self, catalog: Catalog, inventory: Inventory, payments: PaymentService, bus: EventBus
    ):
        self.catalog = catalog
        self.inventory = inventory
        self.payments = payments
        self.bus = bus
        self.orders: dict[str, Order] = {}

    def price(self, lines: dict[str, int]) -> Money:
        total = Money(0)
        for sku, qty in lines.items():
            total = total + self.catalog.get(sku).price.times(qty)
        return total

    def place_order(self, customer: str, lines: dict[str, int], payment_token: str) -> Order:
        """Validate, reserve stock, authorize payment, then confirm.

        Either the order is confirmed with stock reserved and funds authorized, or
        nothing is held: a failed authorization releases the reservation.
        """
        if not lines or len(lines) > MAX_LINES_PER_ORDER:
            raise CheckoutError("an order needs between 1 and 50 lines")
        if any(qty <= 0 for qty in lines.values()):
            raise CheckoutError("quantities must be positive")
        total = self.price(lines)
        try:
            reservation = self.inventory.reserve(lines)
        except OutOfStock as exc:
            raise CheckoutError(f"out of stock: {exc}") from exc
        try:
            authorization = self.payments.authorize(payment_token, total)
        except PaymentDeclined as exc:
            self.inventory.release(reservation)
            raise CheckoutError(f"payment declined: {exc.reason}") from exc
        order = Order(
            str(uuid.uuid4()),
            customer,
            dict(lines),
            total,
            "confirmed",
            reservation,
            authorization,
            ["confirmed"],
        )
        self.orders[order.id] = order
        self.bus.publish("order.confirmed", {"order_id": order.id, "customer": customer})
        return order
