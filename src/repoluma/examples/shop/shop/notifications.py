"""Customer e-mails, driven by order events."""

from .events import EventBus


class Mailer:
    def __init__(self):
        self.outbox: list[tuple[str, str]] = []

    def send(self, to: str, subject: str) -> None:
        self.outbox.append((to, subject))


def register(bus: EventBus, mailer: Mailer, email_for) -> None:
    def on_confirmed(event: dict) -> None:
        mailer.send(email_for(event["customer"]), "Your order is confirmed")

    def on_shipped(event: dict) -> None:
        # TODO: we do not know the customer here; shipment e-mails are not sent yet.
        pass

    bus.subscribe("order.confirmed", on_confirmed)
    bus.subscribe("order.shipped", on_shipped)
