import { describe, expect, it } from "vitest";
import {
  createDraft,
  editDraft,
  isDirty,
  moveSelection,
  draftLocation,
  draftImage,
  draftVariant,
  folderLabel,
  removedIds,
  payload,
  selectRange,
  variantLabel,
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
  it("rejects an invalid variant and unknown indices", () => {
    expect(() => moveSelection(createDraft(report), [0], "1000")).toThrow();
    expect(() => moveSelection(createDraft(report), [99], "3")).toThrow();
  });
  it("lists saved and pending removals, and restores one without undoing later edits", () => {
    const initial = createDraft(report);
    let next = editDraft(initial, { type: "remove", ids: [0] });
    next = editDraft(next, { type: "move", ids: [3], target: "2" });
    expect(removedIds(next)).toEqual([0, 4]);
    next = editDraft(next, { type: "restore", ids: [0, 4] });
    expect(removedIds(next)).toEqual([]);
    expect(next.variants["2"]).toEqual([2, 3]);
    expect(new Set(next.variants["1"])).toEqual(new Set([0, 1, 4]));
    expect(payload(next, 4).restored).toEqual([4]);
  });
  it("returns a saved variant member to its original caption and can undo that step", () => {
    const original = {
      ...report,
      images: report.images.map((image, index) => index === 0 ? {
        ...image, folder: "Вариант 1", status: "named",
        original_location: { folder: "Без подписи", status: "unsigned" },
      } : image),
    } as Report;
    const initial = createDraft(original);
    const next = editDraft(initial, { type: "return-to-misc", ids: [0] });
    expect(draftVariant(next, 0)).toBeNull();
    expect(draftLocation(next, original, 0)).toEqual({ folder: "Без подписи", status: "unsigned", variant: null });
    expect(payload(next, 4).misc).toEqual([0]);
    expect(editDraft(next, { type: "undo" })).toEqual(initial);
  });
  it("preserves group order when moving multiple selected pages to either edge", () => {
    const ordered = { ...report, images: [
      { variant: 1, pdf_page: 1 }, { variant: 1, pdf_page: 2 },
      { variant: 1, pdf_page: 3 }, { variant: 1, pdf_page: 4 },
    ] } as Report;
    const initial = createDraft(ordered);
    expect(editDraft(initial, { type: "move-edge", ids: [3, 1], edge: "start" }).variants["1"]).toEqual([1, 3, 0, 2]);
    expect(editDraft(initial, { type: "move-edge", ids: [2, 0], edge: "end" }).variants["1"]).toEqual([1, 3, 0, 2]);
    expect(editDraft(initial, { type: "move-edge", ids: [99], edge: "end" })).toEqual(initial);
  });
  it("moving a saved removed page explicitly restores it at the chosen destination", () => {
    const next = moveSelection(createDraft(report), [4], "2");
    expect(next.variants["2"]).toEqual([2, 4]);
    expect(removedIds(next)).toEqual([]);
    expect(payload(next, 4).restored).toEqual([4]);
  });
  it("restores a removed draft page to the folder it had before removal", () => {
    let next = editDraft(createDraft(report), { type: "return-to-misc", ids: [0] });
    next = editDraft(next, { type: "remove", ids: [0] });
    next = editDraft(next, { type: "restore", ids: [0] });
    expect(draftVariant(next, 0)).toBeNull();
    expect(payload(next, 4).misc).toEqual([0]);
  });
  it("shows the last draft location for a removal made after moving a page", () => {
    let next = moveSelection(createDraft(report), [0], "2");
    next = editDraft(next, { type: "remove", ids: [0] });
    expect(draftLocation(next, report, 0)).toEqual({ folder: "Вариант 2", status: "named", variant: 2 });
  });
});

describe("copies are independent draft entries until the ordinary save", () => {
  it("copies selected pages in document order to a new variant without moving originals", () => {
    const initial = createDraft(report);
    const next = editDraft(initial, { type: "copy", ids: [3, 1, 0], target: "3" });
    expect(payload(next, 4)).toEqual({ revision: 4,
      variants: { "1": [0, 1], "2": [2], "3": [5, 6, 7] }, removed: [], copies: [0, 1, 3] });
    expect(draftImage(next, report, 5).copy_of).toBe(0);
    expect(draftImage(next, report, 5).pdf_page).toBe(1);
    expect(editDraft(next, { type: "undo" })).toEqual(initial);
  });
  it("keeps copies beside their originals and can remove a pending copy without saving it", () => {
    const initial = createDraft(report);
    let next = editDraft(initial, { type: "copy", ids: [0, 3], target: "same" });
    expect(payload(next, 4)).toEqual({ revision: 4,
      variants: { "1": [0, 5, 1], "2": [2] }, removed: [], misc: [6], copies: [0, 3] });
    next = editDraft(next, { type: "remove", ids: [5, 6] });
    expect(isDirty(next, initial)).toBe(false);
  });
  it("compacts deleted pending copies and flattens copying a copy to its saved source", () => {
    let next = editDraft(createDraft(report), { type: "copy", ids: [0, 1], target: "3" });
    next = editDraft(next, { type: "remove", ids: [5] });
    next = editDraft(next, { type: "copy", ids: [6], target: "2" });
    expect(payload(next, 4)).toEqual({ revision: 4,
      variants: { "1": [0, 1], "2": [2, 6], "3": [5] }, removed: [], copies: [1, 1] });
  });
  it("keeps the source readable when it is removed in the same draft, and restores copies", () => {
    let next = editDraft(createDraft(report), { type: "copy", ids: [0], target: "3" });
    next = editDraft(next, { type: "remove", ids: [0, 5] });
    next = editDraft(next, { type: "restore", ids: [5] });
    expect(payload(next, 4)).toEqual({ revision: 4,
      variants: { "1": [1], "2": [2], "3": [5] }, removed: [0], copies: [0] });
  });
  it("copies the current full variant order and rejects removed or unknown sources", () => {
    let next = editDraft(createDraft(report), { type: "move", ids: [1], target: "1", before: 0 });
    next = editDraft(next, { type: "copy", ids: next.variants["1"], target: "3" });
    expect(payload(next, 4).copies).toEqual([1, 0]);
    expect(() => editDraft(next, { type: "copy", ids: [4], target: "3" })).toThrow();
    expect(() => editDraft(next, { type: "copy", ids: [999], target: "3" })).toThrow();
  });
  it("keeps a copy of a restored page when that source is removed again before save", () => {
    let next = editDraft(createDraft(report), { type: "restore", ids: [4] });
    next = editDraft(next, { type: "copy", ids: [4], target: "same" });
    next = editDraft(next, { type: "remove", ids: [4] });
    expect(payload(next, 4)).toEqual({ revision: 4,
      variants: { "1": [0, 1, 5], "2": [2] }, removed: [], copies: [4] });
    expect(removedIds(next)).toEqual([4]);
  });
});

describe("human folder labels keep the disk identifiers intact", () => {
  const folder = "Материа — 041ccedd";
  const label = "Материалы благоустройства и озеленения территории комплекса";
  const variant = "Вариан — a43b6a12";
  const variantName = "Вариант 1. Административно-деловой комплекс со стороны площади";
  const labeled = {
    ...report,
    folder_labels: { [folder]: label, [variant]: variantName },
    variant_names: { "1": variant },
    images: report.images.map((image, index) => ({
      ...image,
      folder: index === 3 ? folder : image.variant === 1 ? variant : "Вариант 2",
      status: "named",
    })),
  } as Report;

  it("resolves the caption while filtering and edits retain the short disk key", () => {
    expect(folderLabel(labeled, folder)).toBe(label);
    expect(draftLocation(createDraft(labeled), labeled, 3)).toEqual({
      folder, status: "named", variant: null,
    });
    expect(payload(createDraft(labeled), 4).variants["1"]).toEqual([0, 1]);
  });

  it("uses the full variant label for a short variant name, including draft moves", () => {
    expect(variantLabel(labeled, "1")).toBe(variantName);
    const moved = moveSelection(createDraft(labeled), [3], "1");
    expect(draftLocation(moved, labeled, 3)).toEqual({
      folder: variant, status: "named", variant: 1,
    });
    expect(payload(moved, 4).variants["1"]).toEqual([0, 1, 3]);
  });

  it("keeps old reports readable without requiring label metadata", () => {
    expect(folderLabel(report, "Западный двор")).toBe("Западный двор");
    expect(variantLabel(report, 1)).toBe("Вариант 1");
    expect(folderLabel({ ...report, folder_labels: { [folder]: " " } }, folder)).toBe(folder);
    expect(variantLabel({ ...report, variant_names: { "1": "Северный двор" } }, 1)).toBe("Северный двор");
  });

  it("recovers a consistent full caption from a legacy automatic hash or ellipsis", () => {
    const legacy = { ...labeled, folder_labels: undefined,
      images: [{ ...labeled.images[3], title: label }] } as Report;
    expect(folderLabel(legacy, folder)).toBe(label);
    expect(draftLocation(createDraft(legacy), legacy, 0).folder).toBe(folder);
    const abbreviated = "Материалы … территории комплекса";
    expect(folderLabel({ ...legacy, images: [{ ...legacy.images[0], folder: abbreviated }] }, abbreviated)).toBe(label);
  });

  it("does not guess labels for manual names, mixed captions, variants or different prefixes", () => {
    const legacy = { ...labeled, folder_labels: undefined };
    expect(folderLabel(legacy, folder)).toBe(folder);
    expect(variantLabel(legacy, 1)).toBe(variant);
    const manual = { ...legacy, images: [{ ...legacy.images[3], folder: "Новые материалы", title: label }] };
    expect(folderLabel(manual, "Новые материалы")).toBe("Новые материалы");
    const mixed = { ...legacy, images: [
      { ...legacy.images[3], title: label }, { ...legacy.images[3], title: "Материалы фасадов" },
    ] };
    expect(folderLabel(mixed, folder)).toBe(folder);
    const unrelated = { ...legacy, images: [{ ...legacy.images[3], title: "Северный двор" }] };
    expect(folderLabel(unrelated, folder)).toBe(folder);
  });

  it("treats new label metadata as authoritative over legacy inference", () => {
    const image = { ...labeled.images[3], title: label };
    expect(folderLabel({ ...labeled, images: [image], folder_labels: {} }, folder)).toBe(folder);
    expect(folderLabel({ ...labeled, folder_labels: {} }, "constructor")).toBe("constructor");
    expect(folderLabel({ ...labeled, images: [image], folder_labels: { [folder]: "Проверенная подпись" } }, folder))
      .toBe("Проверенная подпись");
  });
});
