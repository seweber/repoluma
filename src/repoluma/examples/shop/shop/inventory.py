"""Stock levels and reservations.

A reservation holds stock for an order until it is committed (shipped) or released.
"""

import uuid
from dataclasses import dataclass, field


class OutOfStock(Exception):
    pass


@dataclass
class Reservation:
    id: str
    lines: dict[str, int]


@dataclass
class Inventory:
    on_hand: dict[str, int] = field(default_factory=dict)
    reserved: dict[str, int] = field(default_factory=dict)

    def available(self, sku: str) -> int:
        return self.on_hand.get(sku, 0) - self.reserved.get(sku, 0)

    def reserve(self, lines: dict[str, int]) -> Reservation:
        """Reserve all lines or none (no partial reservations)."""
        for sku, qty in lines.items():
            if self.available(sku) < qty:
                raise OutOfStock(sku)
        for sku, qty in lines.items():
            self.reserved[sku] = self.reserved.get(sku, 0) + qty
        return Reservation(id=str(uuid.uuid4()), lines=dict(lines))

    def release(self, reservation: Reservation) -> None:
        for sku, qty in reservation.lines.items():
            self.reserved[sku] -= qty

    def commit(self, reservation: Reservation) -> None:
        """Turn a reservation into a stock decrement when goods leave the warehouse."""
        for sku, qty in reservation.lines.items():
            self.reserved[sku] -= qty
            self.on_hand[sku] -= qty

    def restock(self, sku: str, qty: int) -> None:
        self.on_hand[sku] = self.on_hand.get(sku, 0) + qty
