import type { ImageEntry, Report } from "./types";

/** A frame of a collage in source pixels: left, top, right, bottom. */
export type Box = [number, number, number, number];
/** A frame that exists only in the draft until the next save. */
export interface DraftFrame {
  cut: string;
  box: Box;
  /** The card whose caption, variant and preview the frame starts from. */
  template: number;
  number: number;
}
export interface DraftCut {
  collage: string | null;
  /** A picture that is not a collage yet, cut by hand. */
  source: number | null;
  pieces: number[];
  /** Saved cards the cut replaces: earlier frames or the whole picture. */
  replaced: number[];
  trim: boolean;
}
interface DraftData {
  sourceCount: number;
  copies: Record<number, number>;
  frames: Record<number, DraftFrame>;
  cuts: Record<string, DraftCut>;
  collageChecked: string[];
  collageDismissed: string[];
  variants: Record<string, number[]>;
  /** Removals made since the report was loaded; saved removals are separate. */
  removed: number[];
  order: number[];
  savedRemoved: number[];
  restored: number[];
  misc: number[];
  sourceVariants: Record<string, string | null>;
  removedFrom: Record<string, { variant: string | null; before?: number }>;
}
export interface Draft extends DraftData {
  past: DraftData[];
}
export type Edit =
  | { type: "copy"; ids: number[]; target: string }
  | { type: "remove"; ids: number[] }
  | { type: "move"; ids: number[]; target: string; before?: number }
  | { type: "restore"; ids: number[] }
  | { type: "return-to-misc" | "return"; ids: number[] }
  | { type: "move-edge"; ids: number[]; edge: "start" | "end" }
  | { type: "cut"; key: string; collage: string | null; source: number | null; replace: number[];
      boxes: Box[]; targets: (string | null | "keep")[]; trim: boolean }
  | { type: "collage-check"; id: string }
  | { type: "collage-dismiss"; id: string }
  | { type: "undo" };

export function createDraft(report: Report): Draft {
  const variants: Record<string, number[]> = {};
  const order = report.images.map((_, id) => id);
  const savedRemoved = order.filter((id) => report.images[id].removed);
  const sourceVariants: DraftData["sourceVariants"] = {};
  const removedFrom: DraftData["removedFrom"] = {};
  for (const id of order) {
    const image = report.images[id];
    const variant = image.variant == null ? null : String(image.variant);
    sourceVariants[id] = variant;
    if (image.removed) removedFrom[id] = { variant };
    else if (image.variant != null)
      (variants[String(image.variant)] ??= []).push(id);
  }
  for (const ids of Object.values(variants))
    ids.sort((a, b) => report.images[a].pdf_page - report.images[b].pdf_page);
  return { sourceCount: report.images.length, copies: {}, frames: {}, cuts: {}, collageChecked: [], collageDismissed: [],
    variants, removed: [], order, savedRemoved, sourceVariants, removedFrom, restored: [], misc: [], past: [] };
}

function remember(previous: Draft, data: DraftData): Draft {
  const { past: _, ...snapshot } = previous;
  if (JSON.stringify(snapshot) === JSON.stringify(data)) return previous;
  return {
    ...data,
    past: [
      ...previous.past.slice(-49),
      snapshot,
    ],
  };
}

/** «3» is a variant, «3.1» its subvariant; a comma is accepted as in Russian decimals. */
export function variantKey(value: string): string | null {
  const match = /^\s*(\d{1,3})(?:[.,](\d{1,3}))?\s*$/.exec(value);
  if (!match) return null;
  const major = Number(match[1]);
  const minor = match[2] === undefined ? 0 : Number(match[2]);
  if (major < 1 || (match[2] !== undefined && minor < 1)) return null;
  return minor ? `${major}.${minor}` : String(major);
}
export const VARIANT_KEY_ERROR = "Номер варианта — от 1 до 999, подварианта — например 3.1.";
export function compareVariants(a: string, b: string): number {
  const [aMajor, aMinor = 0] = a.split(".").map(Number);
  const [bMajor, bMinor = 0] = b.split(".").map(Number);
  return aMajor - bMajor || aMinor - bMinor;
}
/** The whole variant a key belongs to: «3» for «3.1». */
export function variantParent(key: string): string {
  return key.split(".")[0];
}
/** The first unused subvariant of a variant: 3.1, then 3.2 and so on. */
export function nextSubvariant(variants: string[], key: string): string {
  const parent = variantParent(key);
  for (let minor = 1; minor <= 999; minor++)
    if (!variants.includes(`${parent}.${minor}`)) return `${parent}.${minor}`;
  return parent;
}
export function nextVariant(variants: string[]): string {
  return String(Math.max(0, ...variants.map((key) => Number(variantParent(key)))) + 1);
}

export function moveSelection(
  draft: Draft,
  ids: number[],
  target: string,
  before?: number,
): Draft {
  const key = variantKey(target);
  if (key === null) throw new Error(VARIANT_KEY_ERROR);
  target = key;
  let selected = [...new Set(ids)];
  if (
    selected.some(
      (id) => !Number.isInteger(id) || !draft.order.includes(id),
    )
  )
    throw new Error("Изображение недоступно.");
  if (!selected.length) return draft;
  // Checkbox click order must not silently reorder the selected PDF pages.
  const chosen = new Set(selected);
  selected = [...orderedIds(draft), ...removedIds(draft)].filter((id) => chosen.has(id));
  const variants = Object.fromEntries(
    Object.entries(draft.variants).map(([key, values]) => [
      key,
      values.filter((id) => !selected.includes(id)),
    ]),
  );
  const members = variants[target] ?? [];
  const position =
    before === undefined ? members.length : members.indexOf(before);
  members.splice(position < 0 ? members.length : position, 0, ...selected);
  variants[target] = members;
  return remember(draft, {
    ...dataOf(draft),
    variants,
    removed: draft.removed.filter((id) => !chosen.has(id)),
    restored: [...new Set([...draft.restored, ...selected.filter((id) => draft.savedRemoved.includes(id))])],
    misc: draft.misc.filter((id) => !chosen.has(id)),
  });
}

function cutDraft(draft: Draft, edit: Extract<Edit, { type: "cut" }>): Draft {
  if (!edit.boxes.length) throw new Error("В склейке должен остаться хотя бы один кадр.");
  if (edit.targets.length !== edit.boxes.length) throw new Error("Для каждого кадра укажите, куда его поместить.");
  const previous = draft.cuts[edit.key];
  const dropped = new Set(previous?.pieces ?? []);
  const replaced = [...new Set([...(previous?.replaced ?? []), ...edit.replace])];
  if (!replaced.length) throw new Error("Картинка для разрезания не найдена.");
  // New frames take the place of the first earlier frame or of the whole picture.
  const anchor = previous?.pieces.find((id) => draft.order.includes(id)) ?? replaced[0];
  const anchorVariant = draftVariant(draft, anchor) ?? (isRemoved(draft, anchor) ? draft.removedFrom[anchor]?.variant ?? null : null);
  const anchorMembers = anchorVariant === null ? [] : draft.variants[anchorVariant] ?? [];
  const anchorAt = anchorMembers.indexOf(anchor);
  const gone = new Set([...dropped, ...replaced]);
  const variants = Object.fromEntries(Object.entries(draft.variants).map(([key, members]) =>
    [key, members.filter((id) => !gone.has(id))]));
  let misc = draft.misc.filter((id) => !gone.has(id));
  const order = draft.order.filter((id) => !dropped.has(id));
  const frames = Object.fromEntries(Object.entries(draft.frames).filter(([id]) => !dropped.has(Number(id))));
  const sourceVariants = { ...draft.sourceVariants };
  const template = replaced[0];
  const start = Math.max(draft.sourceCount - 1, ...draft.order) + 1;
  const kept: number[] = [];
  edit.boxes.forEach((box, index) => {
    const id = start + index;
    order.push(id);
    frames[id] = { cut: edit.key, box, template, number: index + 1 };
    sourceVariants[id] = draft.sourceVariants[template] ?? null;
    const target = edit.targets[index];
    const key = target === "keep" ? anchorVariant : target === null ? null : variantKey(target);
    if (target !== "keep" && target !== null && key === null) throw new Error(VARIANT_KEY_ERROR);
    if (key === null) misc.push(id);
    else if (key === anchorVariant) kept.push(id);
    else (variants[key] ??= []).push(id);
  });
  if (anchorVariant !== null && kept.length) {
    const members = variants[anchorVariant] ??= [];
    const before = anchorMembers.slice(anchorAt + 1).find((id) => !gone.has(id));
    const at = before === undefined ? members.length : members.indexOf(before);
    members.splice(at < 0 ? members.length : at, 0, ...kept);
  }
  misc = [...new Set(misc)];
  const removedFrom = { ...draft.removedFrom };
  for (const id of edit.replace)
    if (!isRemoved(draft, id)) removedFrom[id] = { variant: draftVariant(draft, id) };
  return remember(draft, {
    ...dataOf(draft), order, frames, variants, misc, sourceVariants, removedFrom,
    removed: [...new Set([...draft.removed.filter((id) => !dropped.has(id)), ...edit.replace.filter((id) => !draft.savedRemoved.includes(id))])],
    restored: draft.restored.filter((id) => !gone.has(id)),
    cuts: { ...draft.cuts, [edit.key]: { collage: edit.collage, source: edit.source, replaced,
      pieces: edit.boxes.map((_, index) => start + index), trim: edit.trim } },
    collageDismissed: draft.collageDismissed.filter((id) => id !== edit.collage),
  });
}

export function editDraft(draft: Draft, edit: Edit): Draft {
  if (edit.type === "cut") return cutDraft(draft, edit);
  if (edit.type === "collage-check")
    return remember(draft, { ...dataOf(draft), collageChecked: [...new Set([...draft.collageChecked, edit.id])] });
  if (edit.type === "collage-dismiss")
    return remember(draft, { ...dataOf(draft), collageDismissed: [...new Set([...draft.collageDismissed, edit.id])] });
  if (edit.type === "undo") {
    const last = draft.past.at(-1);
    return last ? { ...last, past: draft.past.slice(0, -1) } : draft;
  }
  if (edit.type === "move")
    return moveSelection(draft, edit.ids, edit.target, edit.before);
  if (edit.type === "copy") {
    if (edit.target !== "same" && (!/^\d+$/.test(edit.target) || +edit.target < 1 || +edit.target > 999))
      throw new Error("Номер варианта должен быть от 1 до 999.");
    if (edit.ids.some((id) => !Number.isInteger(id) || !draft.order.includes(id) || isRemoved(draft, id)))
      throw new Error("Для копирования выберите доступные изображения.");
    const chosen = new Set(edit.ids);
    const ids = orderedIds(draft).filter((id) => chosen.has(id));
    if (!ids.length) return draft;
    const start = Math.max(draft.sourceCount - 1, ...draft.order) + 1;
    const copies = { ...draft.copies };
    const sourceVariants = { ...draft.sourceVariants };
    const additions = new Map<number, number>();
    const misc = [...draft.misc];
    ids.forEach((id, position) => {
      const copyId = start + position;
      copies[copyId] = sourceId(draft, id);
      sourceVariants[copyId] = draft.sourceVariants[id];
      additions.set(id, copyId);
      if (edit.target === "same" && draftVariant(draft, id) === null) misc.push(copyId);
    });
    const variants = Object.fromEntries(Object.entries(draft.variants).map(([key, members]) =>
      [key, edit.target === "same" ? members.flatMap((id) => additions.has(id) ? [id, additions.get(id)!] : [id]) : [...members]]));
    if (edit.target !== "same") {
      const key = variantKey(edit.target);
      if (key === null) throw new Error(VARIANT_KEY_ERROR);
      (variants[key] ??= []).push(...additions.values());
    }
    return remember(draft, { ...dataOf(draft), copies, variants, sourceVariants, misc,
      order: [...draft.order, ...additions.values()] });
  }
  if (edit.type === "move-edge") {
    const chosen = new Set(edit.ids);
    const variants = Object.fromEntries(Object.entries(draft.variants).map(([key, members]) => {
      const selected = members.filter((id) => chosen.has(id));
      const rest = members.filter((id) => !chosen.has(id));
      return [key, edit.edge === "start" ? [...selected, ...rest] : [...rest, ...selected]];
    }));
    return remember(draft, { ...dataOf(draft), variants });
  }
  if (edit.type === "restore") {
    const ids = removedIds(draft).filter((id) => edit.ids.includes(id));
    if (!ids.length) return draft;
    const variants = Object.fromEntries(Object.entries(draft.variants).map(([key, values]) => [key, [...values]]));
    const misc = new Set(draft.misc);
    for (const id of ids) {
      const origin = draft.removedFrom[id] ?? { variant: draft.sourceVariants[id] };
      if (origin.variant !== null) {
        const members = variants[origin.variant] ??= [];
        const at = origin.before === undefined ? -1 : members.indexOf(origin.before);
        members.splice(at < 0 ? members.length : at, 0, id);
      } else if (draft.savedRemoved.includes(id)) misc.add(id);
    }
    return remember(draft, { ...dataOf(draft), variants, misc: [...misc],
      removed: draft.removed.filter((id) => !ids.includes(id)),
      restored: [...new Set([...draft.restored, ...ids.filter((id) => draft.savedRemoved.includes(id))])],
    });
  }
  const ids = [...new Set(edit.ids)].filter((id) => draft.order.includes(id) &&
    (edit.type === "remove" ? !isRemoved(draft, id) : true));
  if (!ids.length) return draft;
  if (edit.type === "return-to-misc" || edit.type === "return") {
    const misc = [...new Set([...draft.misc, ...ids.filter((id) =>
      draft.sourceVariants[id] !== null || draft.savedRemoved.includes(id) || id in draft.copies)])];
    return remember(draft, { ...dataOf(draft),
      variants: Object.fromEntries(Object.entries(draft.variants).map(([key, values]) =>
        [key, values.filter((id) => !ids.includes(id))])),
      removed: draft.removed.filter((id) => !ids.includes(id)),
      restored: [...new Set([...draft.restored, ...ids.filter((id) => draft.savedRemoved.includes(id))])],
      misc,
    });
  }
  const removedFrom = { ...draft.removedFrom };
  for (const id of ids) {
    const key = draftVariant(draft, id);
    const members = key === null ? [] : draft.variants[String(key)];
    const before = members.slice(members.indexOf(id) + 1).find((value) => !ids.includes(value));
    removedFrom[id] = { variant: key === null ? null : String(key), before };
  }
  return remember(draft, {
    ...dataOf(draft),
    variants: Object.fromEntries(
      Object.entries(draft.variants).map(([key, values]) => [
        key,
        values.filter((id) => !ids.includes(id)),
      ]),
    ),
    removed: [...new Set([...draft.removed, ...ids.filter((id) => !draft.savedRemoved.includes(id))])],
    restored: draft.restored.filter((id) => !ids.includes(id)),
    removedFrom,
  });
}

export function payload(draft: Draft, revision: number) {
  // Removed pending copies were never written. Compact only their wire IDs;
  // selection, undo and the preview keep stable local IDs until the save finishes.
  const copies = draft.order.filter((id) => id in draft.copies && !isRemoved(draft, id));
  const wireIds = new Map(copies.map((id, index) => [id, draft.sourceCount + index]));
  // Frames follow the copies, cut by cut and frame by frame, exactly as the server appends them.
  const cuts: { collage?: string; source?: number; boxes: Box[]; trim?: boolean }[] = [];
  for (const cut of Object.values(draft.cuts)) {
    const pieces = cut.pieces.filter((id) => draft.order.includes(id) && !isRemoved(draft, id));
    if (!pieces.length) continue;
    for (const id of pieces) wireIds.set(id, draft.sourceCount + wireIds.size);
    cuts.push({ ...(cut.collage === null ? { source: cut.source! } : { collage: cut.collage }),
      boxes: pieces.map((id) => draft.frames[id].box), ...(cut.trim ? { trim: true } : {}) });
  }
  const wire = (id: number) => wireIds.get(id) ?? id;
  const value: { revision: number; variants: Record<string, number[]>; removed: number[];
    restored?: number[]; misc?: number[]; copies?: number[]; cuts?: typeof cuts;
    collage_checked?: string[]; collage_dismissed?: string[] } = {
    revision,
    variants: Object.fromEntries(Object.entries(draft.variants).filter(([, ids]) => ids.length)
      .map(([key, ids]) => [key, ids.filter((id) => !isRemoved(draft, id) || !(id in draft.frames)).map(wire)])),
    removed: draft.removed.filter((id) => !(id in draft.copies) && !(id in draft.frames)).sort((a, b) => a - b),
  };
  const restored = draft.restored.filter((id) => !isRemoved(draft, id)).sort((a, b) => a - b);
  const misc = draft.misc.filter((id) => !isRemoved(draft, id)).map(wire).sort((a, b) => a - b);
  if (restored.length) value.restored = restored;
  if (misc.length) value.misc = misc;
  if (copies.length) value.copies = copies.map((id) => draft.copies[id]);
  if (cuts.length) value.cuts = cuts;
  if (draft.collageChecked.length) value.collage_checked = draft.collageChecked;
  if (draft.collageDismissed.length) value.collage_dismissed = draft.collageDismissed;
  return value;
}
function dataOf(draft: Draft): DraftData {
  const { past: _, ...data } = draft;
  return data;
}
export function removedIds(draft: Draft): number[] {
  const removed = new Set([...draft.removed,
    ...draft.savedRemoved.filter((id) => !draft.restored.includes(id))]);
  return draft.order.filter((id) => removed.has(id));
}
export function isRemoved(draft: Draft, id: number): boolean {
  return draft.removed.includes(id) || (draft.savedRemoved.includes(id) && !draft.restored.includes(id));
}
export function sourceId(draft: Draft, id: number): number {
  return draft.copies[id] ?? draft.frames[id]?.template ?? id;
}
export function draftImage(draft: Draft, report: Report, id: number): ImageEntry {
  const frame = draft.frames[id];
  if (frame) {
    const template = report.images[frame.template];
    const [left, top, right, bottom] = frame.box;
    return { ...template, size: [right - left, bottom - top], crop_px: frame.box, collage: frame.cut, cut: "manual",
      removed: undefined, collage_replaced: undefined,
      original_location: template.original_location ?? { folder: template.folder, status: template.status } };
  }
  const source = sourceId(draft, id);
  return source === id ? report.images[id] : { ...report.images[source], copy_of: source };
}
export function folderLabel(report: Report, folder: string): string {
  if (report.folder_labels !== undefined) {
    const label = report.folder_labels?.[folder];
    return typeof label === "string" && label.trim() ? label.trim() : folder;
  }
  // Recover only unambiguous captions from old reports with automatic truncation.
  const hash = folder.match(/^(.*?)\s+—\s+[\da-f]{6,8}$/i);
  const ellipsis = folder.match(/^(.*?)\s*…\s*(.*)$/);
  const prefix = (hash?.[1] ?? ellipsis?.[1])?.trim().toLocaleLowerCase("ru");
  if (!prefix) return folder;
  const members = report.images.filter((image) => image.folder === folder);
  if (!members.length || members.some((image) => image.variant !== null || !image.title)) return folder;
  const titles = [...new Set(members.map((image) => image.title!.trim()))];
  if (titles.length !== 1) return folder;
  const safeTitle = titles[0].replace(/[<>:"/\\|?*\u0000-\u001f]/g, " — ")
    .replace(/\s+/g, " ").replace(/^[ .]+|[ .]+$/g, "").toLocaleLowerCase("ru");
  const suffix = ellipsis?.[2].trim().toLocaleLowerCase("ru");
  return safeTitle.length > prefix.length && safeTitle.startsWith(prefix) &&
    (!suffix || safeTitle.endsWith(suffix)) ? titles[0] : folder;
}

function variantFolder(report: Report, variant: number | string): string {
  return report.variant_names?.[String(variant)] ||
    report.images.find((image) => image.variant != null && String(image.variant) === String(variant) && image.folder)?.folder ||
    report.documents?.find((document) => document.variant != null && String(document.variant) === String(variant))?.folder ||
    `Вариант ${variant}`;
}

export function variantLabel(report: Report, variant: number | string): string {
  return folderLabel(report, variantFolder(report, variant));
}

export function draftLocation(draft: Draft, report: Report, id: number): {
  folder: string; status: "named" | "review" | "unsigned"; variant: string | null;
} {
  const image = draftImage(draft, report, id);
  const removedVariant = isRemoved(draft, id) ? draft.removedFrom[id]?.variant : null;
  const variant = removedVariant == null ? draftVariant(draft, id) : removedVariant;
  if (variant !== null) return {
    folder: variantFolder(report, variant), status: "named", variant,
  };
  if (draft.misc.includes(id)) {
    const original = (image as typeof image & { original_location?: { folder: string;
      status: "named" | "review" | "unsigned" }; auto_folder?: string }).original_location;
    const fallback = image as typeof image & { auto_folder?: string };
    const folder = original?.folder || fallback.auto_folder || image.title || "Без подписи";
    const status = original?.status ?? (folder === "Без подписи" ? "unsigned" : folder === "На проверку" ? "review" : "named");
    return { folder, status, variant: null };
  }
  return { folder: image.folder, status: image.status, variant: null };
}
export function isDirty(draft: Draft, initial: Draft): boolean {
  return (
    JSON.stringify(payload(draft, 0)) !== JSON.stringify(payload(initial, 0))
  );
}
export function selectRange(
  visible: number[],
  anchor: number,
  clicked: number,
): number[] {
  const start = visible.indexOf(anchor),
    end = visible.indexOf(clicked);
  return start < 0 || end < 0
    ? [clicked]
    : visible.slice(Math.min(start, end), Math.max(start, end) + 1);
}
export function draftVariant(draft: Draft, id: number): string | null {
  const key = Object.keys(draft.variants).find((key) =>
    draft.variants[key].includes(id),
  );
  return key ?? null;
}
export function orderedIds(draft: Draft): number[] {
  const variants = Object.keys(draft.variants)
    .sort(compareVariants)
    .flatMap((key) => draft.variants[key]);
  const members = new Set(variants);
  return [
    ...variants,
    ...draft.order.filter((id) => !members.has(id)),
  ].filter((id) => !isRemoved(draft, id));
}
