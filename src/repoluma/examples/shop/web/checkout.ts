// Browser checkout form: collects a payment token from the provider's widget
// and submits the cart to the backend.

export interface CartLine {
  sku: string;
  quantity: number;
}

export async function submitCheckout(lines: CartLine[], paymentToken: string): Promise<string> {
  if (lines.length === 0) {
    throw new Error("cart is empty");
  }
  const response = await fetch("/api/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lines, paymentToken }),
  });
  if (response.status === 402) {
    throw new Error("payment declined");
  }
  const body = await response.json();
  return body.orderId;
}
