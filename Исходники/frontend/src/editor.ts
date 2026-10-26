import type { Report } from "./types";

interface DraftData {
  variants: Record<string, number[]>;
  removed: number[];
  order: number[];
}
export interface Draft extends DraftData {
  past: DraftData[];
}
export type Edit =
  | { type: "remove"; ids: number[] }
  | { type: "move"; ids: number[]; target: string; before?: number }
  | { type: "undo" };

export function createDraft(report: Report): Draft {
  const variants: Record<string, number[]> = {};
  const order = report.images.flatMap((image, id) =>
    image.removed ? [] : [id],
  );
  for (const id of order) {
    const image = report.images[id];
    if (image.variant != null)
      (variants[String(image.variant)] ??= []).push(id);
  }
  for (const ids of Object.values(variants))
    ids.sort((a, b) => report.images[a].pdf_page - report.images[b].pdf_page);
  return { variants, removed: [], order, past: [] };
}

function remember(previous: Draft, data: DraftData): Draft {
  return {
    ...data,
    past: [
      ...previous.past.slice(-49),
      {
        variants: previous.variants,
        removed: previous.removed,
        order: previous.order,
      },
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
      (id) => !draft.order.includes(id) || draft.removed.includes(id),
    )
  )
    throw new Error("Изображение недоступно.");
  if (!selected.length) return draft;
  // Checkbox click order must not silently reorder the selected PDF pages.
  const chosen = new Set(selected);
  selected = orderedIds(draft).filter((id) => chosen.has(id));
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
    variants,
    removed: draft.removed,
    order: draft.order,
  });
}

export function editDraft(draft: Draft, edit: Edit): Draft {
  if (edit.type === "undo") {
    const last = draft.past.at(-1);
    return last ? { ...last, past: draft.past.slice(0, -1) } : draft;
  }
  if (edit.type === "move")
    return moveSelection(draft, edit.ids, edit.target, edit.before);
  const ids = edit.ids.filter((id) => draft.order.includes(id));
  if (!ids.length) return draft;
  return remember(draft, {
    variants: Object.fromEntries(
      Object.entries(draft.variants).map(([key, values]) => [
        key,
        values.filter((id) => !ids.includes(id)),
      ]),
    ),
    removed: [...new Set([...draft.removed, ...ids])],
    order: draft.order,
  });
}

export function payload(draft: Draft, revision: number) {
  return {
    revision,
    variants: draft.variants,
    removed: [...draft.removed].sort((a, b) => a - b),
  };
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
  return [
    ...variants,
    ...draft.order.filter((id) => !variants.includes(id)),
  ].filter((id) => !draft.removed.includes(id));
}
