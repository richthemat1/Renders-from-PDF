import { describe, expect, it } from "vitest";
import { cellsFromLines, heldIn, linesFromBoxes, trimmedBoxes, turnedLines } from "./Collages";

describe("cut editor lines", () => {
  const size: [number, number] = [1376, 768];
  const columns: [number, number, number, number][] = [[0, 0, 344, 768], [344, 0, 688, 768], [688, 0, 1032, 768], [1032, 0, 1376, 768]];

  it("puts lines on joints and in the middle of gaps, and cells follow them in reading order", () => {
    expect(linesFromBoxes(columns, size)).toEqual({ xs: [344, 688, 1032], ys: [] });
    expect(linesFromBoxes([[0, 0, 1376, 360], [0, 400, 1376, 768]], size)).toEqual({ xs: [], ys: [380] });
    expect(cellsFromLines({ xs: [688], ys: [384] }, size)).toEqual(
      [[0, 0, 688, 384], [688, 0, 1376, 384], [0, 384, 688, 768], [688, 384, 1376, 768]]);
  });
  it("turns lines to the chosen direction with as many frames, on found seams when they fit", () => {
    const none = { xs: [], ys: [] };
    expect(turnedLines({ xs: [344, 688, 1032], ys: [] }, "y", size, none)).toEqual({ xs: [], ys: [192, 384, 576] });
    expect(turnedLines({ xs: [], ys: [384] }, "x", size, { xs: [700], ys: [384] })).toEqual({ xs: [700], ys: [] });
    // Already that way, a grid or no lines: nothing to turn.
    expect(turnedLines({ xs: [344], ys: [] }, "x", size, none)).toBeNull();
    expect(turnedLines({ xs: [344], ys: [384] }, "y", size, none)).toBeNull();
    expect(turnedLines(none, "y", size, none)).toBeNull();
    // A low strip cannot hold as many rows as it had columns.
    expect(turnedLines({ xs: [100, 200, 300, 400, 500], ys: [] }, "y", [600, 40], none)).toEqual({ xs: [], ys: [20] });
  });
  it("a new cell keeps the place of the one earlier frame under it", () => {
    const frames = columns.map((box, index) => ({ box, variant: index < 2 ? "1" : null }));
    expect(heldIn([0, 0, 344, 768], frames)?.variant).toBe("1");
    expect(heldIn([700, 0, 1376, 768], frames)).toBeUndefined();
    expect(heldIn([0, 0, 1376, 384], frames)).toBeUndefined();
    expect(heldIn([1032, 0, 1376, 768], frames)).toEqual({ box: columns[3], variant: null });
  });
  it("knows a cut trimmed light margins only when its frames do not touch", () => {
    expect(trimmedBoxes(columns, size)).toBe(false);
    expect(trimmedBoxes([[0, 0, 1376, 360], [0, 400, 1376, 768]], size)).toBe(true);
    expect(trimmedBoxes([[8, 8, 1368, 760]], size)).toBe(true);
    expect(trimmedBoxes([], size)).toBe(false);
  });
});
