"""Shipping confirmed orders."""

from .events import EventBus
from .inventory import Inventory
from .orders import Order
from .payments import PaymentService


class Fulfillment:
    def __init__(self, inventory: Inventory, payments: PaymentService, bus: EventBus):
        self.inventory = inventory
        self.payments = payments
        self.bus = bus

    def ship(self, order: Order, tracking_code: str) -> None:
        """Commit stock, capture payment, and announce shipment."""
        if order.status != "confirmed":
            raise ValueError(f"cannot ship an order in status {order.status}")
        self.inventory.commit(order.reservation)
        self.payments.capture(order.authorization)
        order.status = "shipped"
        order.history.append(f"shipped:{tracking_code}")
        self.bus.publish("order.shipped", {"order_id": order.id, "tracking": tracking_code})
