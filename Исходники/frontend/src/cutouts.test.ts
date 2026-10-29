import { describe, expect, it } from "vitest";
import { byPlace, contains, snapTo, sourceFor } from "./Cutouts";
import type { CutoutSource } from "./Cutouts";
import { boxRect, createDraft, editDraft, isDirty, payload, rectBox } from "./editor";
import type { CutoutChange } from "./editor";
import type { ImageEntry, Rect, Report } from "./types";

function entry(extra: Partial<ImageEntry> = {}): ImageEntry {
  return { page: 1, sequence: 1, title: "Перспективное изображение №1", folder: "Вариант 2", file: "Вариант 2/Вариант 2.pdf",
    preview: "p.png", variant: 2, pdf_page: 1, status: "named", size: [800, 600], ...extra };
}
function report(images: ImageEntry[], extra: Partial<Report> = {}): Report {
  return { version: "3.35", revision: 0, source: "a.pdf", output: "a", pages: 2, images, documents: [],
    counts_by_status: {}, warnings: [], errors: [], ...extra };
}
const area: Rect = [0.1, 0.1, 0.5, 0.5];
const left: Rect = [0.1, 0.1, 0.3, 0.5];

function source(extra: Partial<CutoutSource>): CutoutSource {
  return { key: "k", record: null, kind: "image", page: 1, source: 0, area, size: [800, 600], title: "", preview: null,
    pending: false, pieces: [], variant: "2", variantLabel: "Вариант 2", ...extra };
}

describe("cutout editor helpers", () => {
  it("cuts a frame from the smallest picture holding it, allowing the server's tolerance", () => {
    const whole = source({ key: "whole", area: [0, 0, 1, 1] });
    const picture = source({ key: "picture" });
    expect(sourceFor(left, [whole, picture])?.key).toBe("picture");
    expect(sourceFor([0.095, 0.1, 0.3, 0.5], [picture])?.key).toBe("picture");
    expect(sourceFor([0.05, 0.1, 0.3, 0.5], [picture])).toBeUndefined();
    expect(contains(area, [0.1, 0.1, 0.505, 0.5])).toBe(true);
  });
  it("sticks to the nearest edge within the limit and orders frames as they are read", () => {
    expect(snapTo(0.31, [0, 0.3, 0.32], 0.02)).toBe(0.32);
    expect(snapTo(0.5, [0, 0.3], 0.02)).toBeNull();
    const frames: Rect[] = [[0.5, 0.1, 0.6, 0.2], [0.1, 0.5, 0.2, 0.6], [0.1, 0.1, 0.2, 0.2]];
    expect([...frames].sort(byPlace)).toEqual([[0.1, 0.1, 0.2, 0.2], [0.5, 0.1, 0.6, 0.2], [0.1, 0.5, 0.2, 0.6]]);
  });
  it("turns shares of the page into pixels of the source and back", () => {
    expect(rectBox(left, area, [800, 600])).toEqual([0, 0, 400, 600]);
    // A frame a little past the picture is clamped to it.
    expect(rectBox([0.09, 0.1, 0.3, 0.51], area, [800, 600])).toEqual([0, 0, 400, 600]);
    boxRect([0, 0, 400, 600], area, [800, 600]).forEach((value, index) => expect(value).toBeCloseTo(left[index], 9));
  });
});

describe("cutouts in the draft", () => {
  const change = (extra: Partial<CutoutChange>): CutoutChange => ({ key: "cutout:img:0", record: null, kind: "image", page: 1,
    source: 0, area, size: [800, 600], replace: [0], rects: [left], targets: ["keep"], ...extra });

  it("puts a cutout in the place of its picture, and without cutouts brings the picture back", () => {
    const initial = createDraft(report([entry()]));
    const cut = editDraft(initial, { type: "cutouts", changes: [change({})] });
    expect(cut.variants["2"]).toEqual([1]);
    expect(cut.frames[1]).toMatchObject({ box: [0, 0, 400, 600], template: 0, rect: left });
    expect(payload(cut, 0)).toEqual({ revision: 0, variants: { 2: [1] }, removed: [0],
      cutouts: [{ source: 0, rects: [left] }] });
    const back = editDraft(cut, { type: "cutouts", changes: [change({ replace: [], rects: [], targets: [] })] });
    expect(back.variants["2"]).toEqual([0]);
    expect(back.cuts).toEqual({});
    expect(isDirty(back, initial)).toBe(false);
    // One step back undoes the whole change of the editor.
    expect(editDraft(cut, { type: "undo" }).variants["2"]).toEqual([0]);
  });
  it("keeps a page snapshot without a variant unless one is chosen", () => {
    const initial = createDraft(report([entry()]));
    const snapshot = editDraft(initial, { type: "cutouts", changes: [change({ key: "cutout:page:2", kind: "page", page: 2,
      source: null, area: [0, 0, 1, 1], size: [4167, 3125], replace: [], rects: [[0.7, 0.2, 0.94, 0.53]], targets: [null] })] });
    expect(snapshot.misc).toEqual([1]);
    expect(snapshot.frames[1].template).toBe(-1);
    expect(payload(snapshot, 0)).toEqual({ revision: 0, variants: { 2: [0] }, removed: [], misc: [1],
      cutouts: [{ page: 2, rects: [[0.7, 0.2, 0.94, 0.53]] }] });
  });
  it("returns a picture whose cutouts were saved and drops them", () => {
    const saved = report([
      entry({ removed: true, cutout_replaced: "1-v0", file: "_Служебное/Резервные копии/x/Вариант 2.pdf" }),
      entry({ cutout: "1-v0", crop_px: [0, 0, 400, 600], size: [400, 600], cut: "cutout" }),
    ], { cutouts: [{ id: "1-v0", page: 1, kind: "image", image: 0, area, size: [800, 600], file: "s.pdf" }] });
    const initial = createDraft(saved);
    expect(initial.variants["2"]).toEqual([1]);
    const back = editDraft(initial, { type: "cutouts", changes: [change({ key: "cutout:1-v0", record: "1-v0",
      replace: [1], rects: [], targets: [] })] });
    expect(back.variants["2"]).toEqual([0]);
    expect(payload(back, 0)).toEqual({ revision: 0, variants: { 2: [0] }, removed: [1], restored: [0],
      cutouts: [{ record: "1-v0", rects: [] }] });
  });
});
