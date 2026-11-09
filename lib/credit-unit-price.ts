/** Base-function equivalent; annual credits are issued monthly, not once. */
export function creditUnitPrice(product: { amount_fen: number; credits: number; kind: string; months: number }) {
  const periods = product.kind === "membership" ? Math.max(1, product.months) : 1;
  const totalCredits = product.credits * periods;
  if (totalCredits <= 0) return "—";
  // Integer cents -> two decimal yuan; conventional rounding without float ties.
  const cents = Math.round(product.amount_fen * 5 / totalCredits);
  return (cents / 100).toFixed(2);
}
