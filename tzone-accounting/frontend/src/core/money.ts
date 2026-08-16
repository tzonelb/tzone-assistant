/**
 * Money is an integer count of minor units. Always.
 *
 * `1234` is 12.34 USD; `1500` is 1500 IQD, because IQD is configured with 0 decimals. No
 * monetary value is ever stored, summed or transmitted as a float — a ledger that drifts by a
 * cent is a broken ledger, and floating point drifts.
 */

export interface Currency {
  code: string;
  decimals: number;
  symbol: string;
}

export const DEFAULT_CURRENCIES: Currency[] = [
  { code: "USD", decimals: 2, symbol: "$" },
  { code: "IQD", decimals: 0, symbol: "د.ع" },
  { code: "LBP", decimals: 0, symbol: "ل.ل" },
  { code: "EUR", decimals: 2, symbol: "€" },
];

/** FX rates are micro-units: 1_000_000 === 1.0. Integer, for the same reason. */
export const RATE_ONE = 1_000_000;

export function findCurrency(currencies: Currency[], code: string): Currency {
  return currencies.find((c) => c.code === code) ?? { code, decimals: 2, symbol: code };
}

/** Parse user input ("12.34", "١٢٫٣٤" is not handled — inputs are Latin digits) to minor units. */
export function parseAmount(input: string, decimals: number): number {
  const cleaned = input.replace(/[\s,]/g, "").trim();
  if (!cleaned) return 0;
  const negative = cleaned.startsWith("-");
  const [whole, fraction = ""] = cleaned.replace("-", "").split(".");
  const padded = (fraction + "0".repeat(decimals)).slice(0, decimals);
  const value = Number(whole || "0") * 10 ** decimals + Number(padded || "0");
  return negative ? -value : value;
}

export function formatAmount(minor: number, decimals: number): string {
  const negative = minor < 0;
  const digits = Math.abs(minor).toString().padStart(decimals + 1, "0");
  const whole = digits.slice(0, digits.length - decimals) || "0";
  const fraction = decimals ? `.${digits.slice(digits.length - decimals)}` : "";
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}${fraction}`;
}

export function formatMoney(minor: number, currency: Currency): string {
  return `${formatAmount(minor, currency.decimals)} ${currency.symbol}`;
}

/**
 * Convert a transaction amount to base currency.
 *
 * Rounds half away from zero so that a converted debit and its matching credit land on the same
 * value — the alternative (banker's rounding, or truncation) can make a balanced entry
 * unbalanced in base currency by one minor unit.
 */
export function toBase(minor: number, rate: number): number {
  const scaled = (minor * rate) / RATE_ONE;
  return scaled < 0 ? -Math.round(-scaled) : Math.round(scaled);
}

/** Tax in basis points: 1500 bp === 15%. Rounded the same way, for the same reason. */
export function taxOf(net: number, rateBp: number): number {
  const scaled = (net * rateBp) / 10_000;
  return scaled < 0 ? -Math.round(-scaled) : Math.round(scaled);
}

/**
 * Split an amount across weights so the parts always sum back to the whole.
 *
 * Used when a rounding remainder has to land somewhere (allocating a payment across invoices,
 * spreading a discount over lines). The remainder goes to the largest parts first, which is
 * both conventional and stable.
 */
export function allocate(total: number, weights: number[]): number[] {
  const sum = weights.reduce((a, b) => a + b, 0);
  if (sum === 0) return weights.map(() => 0);

  const parts = weights.map((w) => Math.floor((total * w) / sum));
  let remainder = total - parts.reduce((a, b) => a + b, 0);
  const order = weights
    .map((w, index) => ({ w, index }))
    .sort((a, b) => b.w - a.w || a.index - b.index);

  let cursor = 0;
  while (remainder > 0 && order.length) {
    parts[order[cursor % order.length].index] += 1;
    remainder -= 1;
    cursor += 1;
  }
  return parts;
}
