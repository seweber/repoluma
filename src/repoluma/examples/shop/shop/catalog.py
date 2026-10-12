"""Product catalog: prices and availability flags."""

from dataclasses import dataclass

from .money import Money


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Money
    active: bool = True


class Catalog:
    def __init__(self, products: list[Product]):
        self._by_sku = {p.sku: p for p in products}

    def get(self, sku: str) -> Product:
        product = self._by_sku.get(sku)
        if product is None or not product.active:
            raise KeyError(f"unknown or inactive product {sku}")
        return product
