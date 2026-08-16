import { describe, expect, it } from "vitest";
import { allocate, formatAmount, parseAmount, taxOf, toBase, RATE_ONE } from "./money";

describe("minor-unit arithmetic", () => {
  it("parses decimal input into minor units", () => {
    expect(parseAmount("12.34", 2)).toBe(1234);
    expect(parseAmount("12", 2)).toBe(1200);
    expect(parseAmount("12.5", 2)).toBe(1250);
    expect(parseAmount("1,250.75", 2)).toBe(125075);
    expect(parseAmount("-8.05", 2)).toBe(-805);
    expect(parseAmount("", 2)).toBe(0);
  });

  it("parses zero-decimal currencies without inventing precision", () => {
    expect(parseAmount("1500", 0)).toBe(1500);
    expect(parseAmount("1500.9", 0)).toBe(1500);
  });

  it("formats minor units back to a grouped string", () => {
    expect(formatAmount(1234, 2)).toBe("12.34");
    expect(formatAmount(125075, 2)).toBe("1,250.75");
    expect(formatAmount(5, 2)).toBe("0.05");
    expect(formatAmount(-805, 2)).toBe("-8.05");
    expect(formatAmount(1500, 0)).toBe("1,500");
  });

  it("round-trips every value it formats", () => {
    for (const value of [0, 1, 99, 100, 12345, 999999, -4200]) {
      expect(parseAmount(formatAmount(value, 2), 2)).toBe(value);
    }
  });
});

describe("currency conversion", () => {
  it("is the identity at rate 1.0", () => {
    expect(toBase(1234, RATE_ONE)).toBe(1234);
  });

  it("rounds half away from zero, symmetrically", () => {
    // 0.5 minor units in each direction must not land on the same side.
    expect(toBase(1, RATE_ONE / 2)).toBe(1);
    expect(toBase(-1, RATE_ONE / 2)).toBe(-1);
  });

  it("converts at a non-unit rate", () => {
    expect(toBase(10_000, 1_500_000)).toBe(15_000); // rate 1.5
    expect(toBase(10_000, 500_000)).toBe(5_000); // rate 0.5
  });
});

describe("tax", () => {
  it("computes basis points", () => {
    expect(taxOf(10_000, 1_500)).toBe(1_500); // 15% of 100.00
    expect(taxOf(10_000, 0)).toBe(0);
    expect(taxOf(333, 1_500)).toBe(50); // 49.95 -> 50
  });
});

describe("allocate", () => {
  it("always sums back to the total", () => {
    for (const total of [100, 101, 1, 999, 12_345]) {
      const parts = allocate(total, [1, 1, 1]);
      expect(parts.reduce((a, b) => a + b, 0)).toBe(total);
    }
  });

  it("splits proportionally", () => {
    expect(allocate(100, [1, 3])).toEqual([25, 75]);
  });

  it("gives the rounding remainder to the largest weight", () => {
    // 10 across weights 2 and 1 is 6.67/3.33 -> 7/3, remainder to the bigger share.
    expect(allocate(10, [2, 1])).toEqual([7, 3]);
  });

  it("handles zero weights without dividing by zero", () => {
    expect(allocate(50, [0, 0])).toEqual([0, 0]);
  });
});
