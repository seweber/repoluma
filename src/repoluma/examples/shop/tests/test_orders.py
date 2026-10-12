import pytest
from shop.catalog import Catalog, Product
from shop.events import EventBus
from shop.inventory import Inventory
from shop.money import Money
from shop.orders import CheckoutError, OrderService
from shop.payments import PaymentService


class FakeProvider:
    def __init__(self, approve=True):
        self.approve = approve

    def authorize(self, token, amount_cents, currency):
        if self.approve:
            return {"status": "approved", "id": "auth-1"}
        return {"status": "declined", "reason": "insufficient funds"}

    def capture(self, authorization_id):
        return {}

    def refund(self, authorization_id, amount_cents):
        return {}


def make_service(approve=True):
    catalog = Catalog([Product("mug", "Mug", Money(1200))])
    inventory = Inventory(on_hand={"mug": 3})
    return OrderService(catalog, inventory, PaymentService(FakeProvider(approve)), EventBus())


def test_place_order_reserves_and_authorizes():
    service = make_service()
    order = service.place_order("ada", {"mug": 2}, "tok")
    assert order.status == "confirmed"
    assert service.inventory.available("mug") == 1


def test_declined_payment_releases_reservation():
    service = make_service(approve=False)
    with pytest.raises(CheckoutError):
        service.place_order("ada", {"mug": 2}, "tok")
    assert service.inventory.available("mug") == 3


def test_out_of_stock_is_rejected():
    service = make_service()
    with pytest.raises(CheckoutError):
        service.place_order("ada", {"mug": 5}, "tok")
