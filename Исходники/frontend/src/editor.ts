import type { ImageEntry, Report } from "./types";

interface DraftData {
  sourceCount: number;
  copies: Record<number, number>;
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
  return { sourceCount: report.images.length, copies: {}, variants, removed: [], order, savedRemoved, sourceVariants, removedFrom,
    restored: [], misc: [], past: [] };
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

export function moveSelection(
  draft: Draft,
  ids: number[],
  target: string,
  before?: number,
): Draft {
  if (!/^\d+$/.test(target) || Number(target) < 1 || Number(target) > 999)
    throw new Error("Номер варианта должен быть от 1 до 999.");
  target = String(Number(target));
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

export function editDraft(draft: Draft, edit: Edit): Draft {
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
    if (edit.target !== "same") (variants[String(+edit.target)] ??= []).push(...additions.values());
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
  const wire = (id: number) => wireIds.get(id) ?? id;
  const value: { revision: number; variants: Record<string, number[]>; removed: number[];
    restored?: number[]; misc?: number[]; copies?: number[] } = {
    revision,
    variants: Object.fromEntries(Object.entries(draft.variants).filter(([, ids]) => ids.length)
      .map(([key, ids]) => [key, ids.map(wire)])),
    removed: draft.removed.filter((id) => !(id in draft.copies)).sort((a, b) => a - b),
  };
  const restored = draft.restored.filter((id) => !isRemoved(draft, id)).sort((a, b) => a - b);
  const misc = draft.misc.filter((id) => !isRemoved(draft, id)).map(wire).sort((a, b) => a - b);
  if (restored.length) value.restored = restored;
  if (misc.length) value.misc = misc;
  if (copies.length) value.copies = copies.map((id) => draft.copies[id]);
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
  return draft.copies[id] ?? id;
}
export function draftImage(draft: Draft, report: Report, id: number): ImageEntry {
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
    report.images.find((image) => image.variant === Number(variant) && image.folder)?.folder ||
    report.documents?.find((document) => document.variant === Number(variant))?.folder ||
    `Вариант ${variant}`;
}

export function variantLabel(report: Report, variant: number | string): string {
  return folderLabel(report, variantFolder(report, variant));
}

export function draftLocation(draft: Draft, report: Report, id: number): {
  folder: string; status: "named" | "review" | "unsigned"; variant: number | null;
} {
  const image = draftImage(draft, report, id);
  const removedVariant = isRemoved(draft, id) ? draft.removedFrom[id]?.variant : null;
  const variant = removedVariant == null ? draftVariant(draft, id) : Number(removedVariant);
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
export function draftVariant(draft: Draft, id: number): number | null {
  const key = Object.keys(draft.variants).find((key) =>
    draft.variants[key].includes(id),
  );
  return key === undefined ? null : Number(key);
}
export function orderedIds(draft: Draft): number[] {
  const variants = Object.keys(draft.variants)
    .sort((a, b) => Number(a) - Number(b))
    .flatMap((key) => draft.variants[key]);
  const members = new Set(variants);
  return [
    ...variants,
    ...draft.order.filter((id) => !members.has(id)),
  ].filter((id) => !isRemoved(draft, id));
}
