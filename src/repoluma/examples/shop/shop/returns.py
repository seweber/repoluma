"""Customer returns and refunds."""

from datetime import date, timedelta

from .inventory import Inventory
from .money import Money
from .orders import Order
from .payments import PaymentService

RETURN_WINDOW = timedelta(days=30)


class ReturnRejected(Exception):
    pass


def accept_return(
    order: Order, delivered_on: date, today: date, inventory: Inventory, payments: PaymentService
) -> Money:
    """Accept a full return within the return window and refund the order total."""
    if order.status != "shipped":
        raise ReturnRejected("only shipped orders can be returned")
    if today - delivered_on > RETURN_WINDOW:
        raise ReturnRejected("return window of 30 days has passed")
    for sku, qty in order.lines.items():
        inventory.restock(sku, qty)
    payments.refund(order.authorization, order.total)
    order.status = "returned"
    order.history.append("returned")
    return order.total
