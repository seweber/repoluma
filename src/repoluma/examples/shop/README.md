# shop (repoluma example)

A deliberately small, fictional online shop used to demonstrate repoluma.
It is not a real product. Customers place orders; payment is authorized with an
external provider, stock is reserved, and orders are fulfilled or returned.

Explore it with `repoluma serve --root <this directory>`.

## Ordering

`OrderService.place_order` validates the cart, reserves stock, authorizes payment,
and confirms the order. If authorization fails, the reservation is released.

## Returns

Returns are accepted within 30 days of delivery and refunded to the original payment.
