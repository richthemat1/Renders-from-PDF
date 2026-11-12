import { describe, expect, it } from "vitest";
import { CROP_MIN, movedBox } from "./Crop";

const size: [number, number] = [600, 400];

describe("the crop frame", () => {
  it("moves one edge with a side handle and two with a corner", () => {
    expect(movedBox([0, 0, 600, 400], "e", -200, 50, size)).toEqual([0, 0, 400, 400]);
    expect(movedBox([0, 0, 600, 400], "nw", 100.4, 49.6, size)).toEqual([100, 50, 600, 400]);
  });

  it("stays inside the picture and never gets thinner than the server allows", () => {
    expect(movedBox([100, 50, 400, 300], "se", 900, 900, size)).toEqual([100, 50, 600, 400]);
    expect(movedBox([100, 50, 400, 300], "w", 900, 0, size)).toEqual([400 - CROP_MIN, 50, 400, 300]);
    expect(movedBox([100, 50, 400, 300], "n", 0, -900, size)).toEqual([100, 0, 400, 300]);
  });

  it("moves whole without changing its size, up to the edge of the picture", () => {
    expect(movedBox([100, 50, 400, 300], null, 50, 20, size)).toEqual([150, 70, 450, 320]);
    expect(movedBox([100, 50, 400, 300], null, 900, -900, size)).toEqual([300, 0, 600, 250]);
  });
});
