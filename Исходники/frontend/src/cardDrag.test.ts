import { describe, expect, it } from "vitest";
import { beforeAt, insertionIndex } from "./cardDrag";
import type { Slot } from "./cardDrag";

// Two rows of a grid: three cards of 100 × 100 with gaps of 20, then two more.
const slots: Slot[] = [[0, 0], [120, 0], [240, 0], [0, 120], [120, 120]]
  .map(([left, top], id) => ({ id, left, top, width: 100, height: 100 }));

describe("where dragged cards land", () => {
  it("goes before a card over its left half and after it over its right half", () => {
    expect(insertionIndex(slots, 30, 50)).toBe(0);
    expect(insertionIndex(slots, 70, 50)).toBe(1);
    expect(insertionIndex(slots, 330, 50)).toBe(3);
    expect(insertionIndex(slots, 160, 170)).toBe(4);
  });
  it("takes a gap between cards as the place between them", () => {
    expect(insertionIndex(slots, 110, 50)).toBe(1);
    // Between rows the nearer row counts.
    expect(insertionIndex(slots, 40, 105)).toBe(0);
    expect(insertionIndex(slots, 40, 115)).toBe(3);
  });
  it("puts the cards at the end after the last one, on the empty rest of its row and below the grid", () => {
    expect(insertionIndex(slots, 300, 170)).toBe(5);
    expect(insertionIndex(slots, 50, 400)).toBe(5);
    expect(insertionIndex(slots, 50, -30)).toBe(0);
    expect(insertionIndex([], 50, 50)).toBe(0);
  });
});

describe("the card of the variant the dropped cards go before", () => {
  it("is the card shown at the place, or nothing at the end", () => {
    const dragged = new Set([2]);
    expect(beforeAt([1, 2, 3, 4], [1, 3, 4], dragged, 0)).toBe(1);
    expect(beforeAt([1, 2, 3, 4], [1, 3, 4], dragged, 2)).toBe(4);
    expect(beforeAt([1, 2, 3, 4], [1, 3, 4], dragged, 3)).toBeUndefined();
  });
  it("keeps cards hidden by a search where they were: after the last card shown comes the next one of the variant", () => {
    expect(beforeAt([1, 2, 3, 4, 5], [1, 3], new Set([2, 4]), 2)).toBe(5);
    expect(beforeAt([1, 2], [], new Set([1]), 0)).toBeUndefined();
  });
});
