import { describe, expect, it } from "vitest";
import { plural } from "./api";

describe("Russian counter endings", () => {
  it.each([
    [0, "файлов"], [1, "файл"], [2, "файла"], [4, "файла"],
    [5, "файлов"], [11, "файлов"], [12, "файлов"], [14, "файлов"],
    [21, "файл"], [22, "файла"], [25, "файлов"], [101, "файл"],
    [111, "файлов"], [114, "файлов"], [-2, "файла"],
  ])("uses the correct word for %i", (count, expected) => {
    expect(plural(count as number, ["файл", "файла", "файлов"])).toBe(expected);
  });
});
