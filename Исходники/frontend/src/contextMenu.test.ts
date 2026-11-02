import { describe, expect, it } from "vitest";
import { contextMenuPlace } from "./contextMenu";

// A window of 1366 × 900 and a menu of 300 × 400.
const place = (x: number, y: number, height = 400) => contextMenuPlace(x, y, { width: 300, height }, { width: 1366, height: 900 });

describe("where a context menu opens", () => {
  it("opens below and to the right of the pointer", () => {
    expect(place(200, 100)).toEqual({ left: 202, top: 102, maxHeight: 782, side: "bottom" });
  });
  it("opens to the left of the pointer at the right edge of the window", () => {
    expect(place(1300, 100).left).toBe(998);
  });
  it("opens above the pointer near the bottom edge when there is more room there", () => {
    expect(place(200, 800)).toMatchObject({ top: 398, side: "top" });
  });
  it("stays inside the window and scrolls when it is taller than the room on both sides", () => {
    const tall = place(200, 450, 1200);
    expect(tall.top).toBe(16);
    expect(tall.maxHeight).toBe(868);
  });
});
