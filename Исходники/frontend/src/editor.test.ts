import { describe, expect, it } from "vitest";
import {
  createDraft,
  editDraft,
  isDirty,
  moveSelection,
  payload,
  selectRange,
} from "./editor";
import type { Report } from "./types";

const report = {
  revision: 4,
  images: [
    { variant: 1, pdf_page: 1 },
    { variant: 1, pdf_page: 2 },
    { variant: 2, pdf_page: 1 },
    { variant: null, pdf_page: 1 },
    { variant: 1, pdf_page: 3, removed: true },
  ],
} as Report;

describe("editor preserves stable report indices", () => {
  it("keeps displayed page order when checkboxes were selected in reverse order", () => {
    const next = moveSelection(createDraft(report), [1, 0], "2");
    expect(next.variants["2"]).toEqual([2, 0, 1]);
  });
  it("moves a caption image and the selected variant pages as one group", () => {
    const initial = createDraft(report);
    const next = moveSelection(initial, [1, 3], "2", 2);
    expect(payload(next, 4)).toEqual({
      revision: 4,
      variants: { "1": [0], "2": [1, 3, 2] },
      removed: [],
    });
    expect(isDirty(initial, initial)).toBe(false);
    expect(isDirty(next, initial)).toBe(true);
  });
  it("reorders within a variant without duplicating pages", () => {
    const next = moveSelection(createDraft(report), [0], "1");
    expect(next.variants["1"]).toEqual([1, 0]);
    expect(new Set(Object.values(next.variants).flat()).size).toBe(3);
  });
  it("removes images from every variant and supports complete undo before save", () => {
    const initial = createDraft(report);
    const next = editDraft(initial, { type: "remove", ids: [0, 3] });
    expect(payload(next, 4)).toEqual({
      revision: 4,
      variants: { "1": [1], "2": [2] },
      removed: [0, 3],
    });
    expect(editDraft(next, { type: "undo" }).variants).toEqual(
      initial.variants,
    );
    expect(editDraft(next, { type: "undo" }).removed).toEqual([]);
  });
  it("selects a range only from the visible search/filter results", () => {
    expect(selectRange([0, 2, 5, 6], 2, 6)).toEqual([2, 5, 6]);
    expect(selectRange([0, 2, 5], 99, 5)).toEqual([5]);
  });
  it("rejects an invalid variant and never resurrects saved removals", () => {
    expect(() => moveSelection(createDraft(report), [0], "1000")).toThrow();
    expect(() => moveSelection(createDraft(report), [4], "3")).toThrow();
  });
});
