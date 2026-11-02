import { describe, expect, it } from "vitest";
import { refractionMap } from "./glass";

// The floating dock: 1040 × 68, corners of 18, the rim bends over 22 pixels.
const width = 1040, height = 68;
const map = refractionMap(width, height, 18, 22);
const pixel = (x: number, y: number) => {
  const i = (y * width + x) * 4;
  return { dx: map[i] - 128, dy: map[i + 1] - 128, blue: map[i + 2], alpha: map[i + 3] };
};

describe("the refraction map of liquid glass", () => {
  it("is an opaque RGBA picture of the panel's size", () => {
    expect(map.length).toBe(width * height * 4);
    expect(pixel(0, 0)).toMatchObject({ blue: 128, alpha: 255 });
    expect(pixel(520, 34)).toMatchObject({ blue: 128, alpha: 255 });
  });
  it("leaves the middle of the panel undistorted", () => {
    expect(pixel(520, 34)).toMatchObject({ dx: 0, dy: 0 });
    expect(pixel(40, 34)).toMatchObject({ dx: 0, dy: 0 });
  });
  it("pulls the backdrop inwards at each straight edge", () => {
    expect(pixel(0, 34).dx).toBeGreaterThan(0);
    expect(pixel(width - 1, 34).dx).toBeLessThan(0);
    expect(pixel(520, 0).dy).toBeGreaterThan(0);
    expect(pixel(520, height - 1).dy).toBeLessThan(0);
    expect(pixel(0, 34).dy).toBe(0);
    expect(pixel(520, 0).dx).toBe(0);
  });
  it("bends most at the very rim and not at all past the bezel", () => {
    const at = (x: number) => pixel(x, 34).dx;
    expect(at(0)).toBeGreaterThan(at(5));
    expect(at(5)).toBeGreaterThan(at(15));
    expect(at(15)).toBeGreaterThan(0);
    expect(at(22)).toBe(0);
  });
  it("bends diagonally in a rounded corner and leaves the cut-off corner alone", () => {
    const corner = pixel(6, 6);
    expect(corner.dx).toBeGreaterThan(0);
    expect(corner.dx).toBe(corner.dy);
    expect(pixel(0, 0)).toMatchObject({ dx: 0, dy: 0 });
  });
});
