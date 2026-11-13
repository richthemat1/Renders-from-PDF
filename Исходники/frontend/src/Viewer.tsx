import { useCallback, useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import {
  ScanEye,
  ArrowDown,
  ArrowDownToLine,
  ArrowLeft,
  ArrowUp,
  ArrowUpRight,
  ArrowUpToLine,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardCopy,
  Copy,
  CopyCheck,
  Crop,
  SquareScissors,
  FileText,
  FolderInput,
  FolderOpen,
  FolderOutput,
  FolderSearch,
  GripVertical,
  ImageOff,
  Images,
  Info,
  Layers2,
  Layers3,
  ListChecks,
  Maximize2,
  Minus,
  LoaderCircle,
  Pencil,
  Plus,
  RotateCcw,
  RotateCw,
  Save,
  Scan,
  Scissors,
  Shapes,
  Search,
  Tag,
  Tags,
  Trash2,
  TriangleAlert,
  Undo2,
} from "lucide-react";
import { ApiError, errorText, fileUrl, native, plural, request } from "./api";
import { ActionMenu, Dialog, Empty, NewVariantDialog, Select, Shell, StatusNote, VariantSuggestions } from "./components";
import type { ActionMenuItem } from "./components";
import { useContextMenu } from "./contextMenu";
import type { HelpTopic } from "./Help";
import {
  boxRect,
  createDraft,
  draftVariant,
  draftLocation,
  draftImage,
  folderLabel,
  editDraft,
  isDirty,
  isRemoved,
  orderedIds,
  payload,
  removedIds,
  selectRange,
  sourceId,
  compareVariants,
  nextVariant,
  namedReport,
  namedSuggestions,
  variantChoice,
  variantLabel,
  variantParent,
} from "./editor";
import type { CutoutChange, Draft } from "./editor";
import { repeatsLabel } from "./editor";
import { beforeAt, useCardDrag } from "./cardDrag";
import type { DropPlace } from "./cardDrag";
import { EditorToolbar } from "./EditorToolbar";
import { CollagesPage, CroppedImage, CutEditor, needsCheck } from "./Collages";
import type { CollageView } from "./Collages";
import { CropDialog } from "./Crop";
import { byPlace, cutoutNumbers, CutoutEditor, CutoutsPage, pageUrl } from "./Cutouts";
import type { CutoutResult, CutoutSource, PictureInfo } from "./Cutouts";
import type { Box } from "./editor";
import { ReviewSummary } from "./ReviewSummary";
import { Collapsible, Presence } from "./motion";
import type { NoticeAction, Rect, Result, VariantExport } from "./types";
import "./viewer-ux.css";
import "./preview-visual.css";
import "./review-fixes.css";

/** Clockwise degrees a card turns by. */
type Turn = 90 | 180 | 270;
const TURN_ITEMS: ActionMenuItem[] = [
  { value: "turn:90", label: "Вправо на 90°" },
  { value: "turn:270", label: "Влево на 90°" },
  { value: "turn:180", label: "На 180°" },
];

type Rename = {
  kind: "variant" | "folder" | "file";
  target: string | number;
  name: string;
};
const STATUS = {
  named: "С подписью",
  review: "На проверку",
  unsigned: "Без подписи",
};
let dirtySequence = Date.now() * 1000;
function textEntry(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && !!target.closest(
    'input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]),textarea,select,[contenteditable]:not([contenteditable="false"])',
  );
}
/** Why a service element was left out, short enough for the badge on its card; the full reason is in the viewer. */
const SERVICE_REASONS: Record<string, string> = {
  small_fragment: "мелкий фрагмент", repeated_resource: "повторяется", repeated_static: "повторяется",
  fully_occluded: "закрыт другим", blank_image: "пустой", blank_layer: "пустой", text_fill: "фон текста",
  cover_graphic: "оформление",
};
function serviceLabel(code?: string): string {
  const reason = code ? SERVICE_REASONS[code] : undefined;
  return reason ? `Служебный · ${reason}` : "Служебный";
}

export function Viewer({
  result,
  footer,
  notify,
  onResult,
  onDirty,
  onBack,
  onSaveReady,
  onHelp,
  onHelpTopic,
}: {
  result: Result;
  footer: ReactNode;
  notify(text: string, error?: boolean, action?: NoticeAction): void;
  onResult(result: Result): void;
  onDirty(dirty: boolean): void;
  onBack(): void;
  onSaveReady?(save: (() => Promise<boolean>) | null): void;
  /** Open the help window on a section: the editor's «?» opens «Редактирование». */
  onHelp?(topic: HelpTopic): void;
  /** The help section about what is on screen now, for «Как пользоваться» in the menu. */
  onHelpTopic?(topic: HelpTopic): void;
}) {
  const initial = useMemo(() => createDraft(result.report), [result]);
  const [draftState, setDraft] = useState<Draft>(initial);
  const [draftSource, setDraftSource] = useState(initial);
  // Save/undo can change the number of report entries. Render the matching draft
  // immediately rather than resolving stale virtual IDs during the reset effect.
  const draft = draftSource === initial ? draftState : initial;
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [filter, setFilter] = useState("all");
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  // The field shows each letter at once; the gallery follows a moment later and skips letters typed meanwhile.
  const searched = useDeferredValue(query);
  const [saving, setSaving] = useState(false);
  // «Изменения сохранены» reports a save made here, not a result that was only opened.
  const [savedHere, setSavedHere] = useState<"" | "saved" | "undone">("");
  // «Ещё → Отменить последнее сохранение» asks first: unlike a step of the draft, the undone save cannot be redone.
  const [confirmUndoSave, setConfirmUndoSave] = useState(false);
  // «Варианты в папку…» / «В папку…» with unsaved edits or images still to review: what to do first.
  const [exportAsk, setExportAsk] = useState<{ keys?: string[]; dirty: boolean; open: number } | null>(null);
  const [conflict, setConflict] = useState(false);
  const [rename, setRename] = useState<Rename | null>(null);
  /** The images «Новый вариант…» moves: the selection, or one card from its context menu. */
  const [newVariant, setNewVariant] = useState<number[] | null>(null);
  const [cutting, setCutting] = useState<CollageView | null>(null);
  /** «Обрезать…»: the card being cropped in place, the whole picture to draw the frame on and its crop so far. */
  const [cropping, setCropping] = useState<{ view: CollageView; src: string; box: Box | null } | null>(null);
  const [cutoutEditor, setCutoutEditor] = useState<{ page: number; picture?: number } | null>(null);
  const [copying, setCopying] = useState<{ ids: number[]; whole: boolean } | null>(null);
  const [copyTarget, setCopyTarget] = useState("same");
  const [number, setNumber] = useState("1");
  const [preview, setPreview] = useState<number | null>(null);
  const [sourcePage, setSourcePage] = useState(false);
  const [sourcePreview, setSourcePreview] = useState<{ key: string; url?: string; error?: string }>({ key: "" });
  const [variantsOpen, setVariantsOpen] = useState(true);
  const [foldersOpen, setFoldersOpen] = useState(() => new Set(result.report.images
    .filter((image) => !image.removed && image.variant === null && image.status === "named")
    .map((image) => image.folder)).size <= 8);
  const [previewOrder, setPreviewOrder] = useState<number[]>([]);
  const [zoom, setZoom] = useState(1);
  const [previewBounds, setPreviewBounds] = useState({ width: 0, height: 0 });
  const [previewNatural, setPreviewNatural] = useState<{ key: string; width: number; height: number } | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [reset, setReset] = useState(false);
  const finishEditing = useRef(false);
  const anchor = useRef<number | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const renameField = useRef<HTMLTextAreaElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const previewViewport = useRef<HTMLDivElement>(null);
  const previewPan = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const savingRef = useRef(false);
  const mounted = useRef(true);
  const dirty = isDirty(draft, initial);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const savingState = useRef(saving);
  savingState.current = saving;
  const { report } = result;
  // Labels read with the names of variants made in the draft.
  const labelled = useMemo(() => namedReport(report, draft), [report, draft]);
  useEffect(() => {
    if (filter.startsWith("variant:")) setVariantsOpen(true);
    if (filter.startsWith("folder:")) setFoldersOpen(true);
  }, [filter]);
  useLayoutEffect(() => {
    document.querySelector<HTMLElement>("nav .project-link.active")?.scrollIntoView({ block: "nearest" });
  }, [filter, variantsOpen, foldersOpen]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useLayoutEffect(() => {
    const field = renameField.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight + 2}px`;
  }, [rename]);
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);
  useEffect(() => {
    setDraft(initial);
    setDraftSource(initial);
    setSelected([]);
    setConflict(false);
    setFilter((current) => {
      if (current.startsWith("variant:") && !initial.variants[current.slice(8)]) return "all";
      if (current.startsWith("folder:") && !result.report.images.some((image) =>
        !image.removed && image.variant === null && image.folder === current.slice(7))) return "all";
      return current;
    });
  }, [initial]);
  useLayoutEffect(() => {
    onDirty(dirty);
    const bridge = window.pywebview?.api;
    if (bridge && "set_unsaved" in bridge)
      void (
        bridge as typeof bridge & {
          set_unsaved(value: boolean, sequence: number): Promise<boolean>;
        }
      )
        .set_unsaved(dirty, ++dirtySequence)
        .catch(() => {});
    return () => {
      onDirty(false);
      if (bridge && "set_unsaved" in bridge)
        void (
          bridge as typeof bridge & {
            set_unsaved(value: boolean, sequence: number): Promise<boolean>;
          }
        )
          .set_unsaved(false, ++dirtySequence)
          .catch(() => {});
    };
  }, [dirty, onDirty]);
  useEffect(() => {
    // A range belongs to one view; the selection belongs to the whole result.
    anchor.current = null;
  }, [filter, status, query]);
  const allRows = useMemo(
    () =>
      draft.order.map((id) => {
        const image = draftImage(draft, report, id);
        const location = draftLocation(draft, report, id);
        const label = location.variant === null
          ? folderLabel(report, location.folder)
          : variantLabel(labelled, location.variant);
        return {
          id,
          image,
          ...location,
          label,
          title: image.title ? folderLabel(report, image.title) : label,
        };
      }),
    [draft, report],
  );
  const removedAll = removedIds(draft);
  const removedSet = new Set(removedAll);
  // «На проверку» or «Без подписи» and not checked yet: «Оставить как есть» can settle it where it is.
  const waitsForDecision = (id: number) => !removedSet.has(id) && draftLocation(draft, report, id).status !== "named";
  // Service elements were set aside by the automatic filter, not by the user.
  const isService = (id: number) => !!report.images[id]?.suppressed;
  // Frames replaced by a newer cut, and a picture cut into frames, live on in the collage, not among removed cards.
  const replacedIds = new Set([...Object.values(draft.cuts).flatMap((cut) => cut.replaced),
    ...removedAll.filter((id) => report.images[id]?.collage_replaced || report.images[id]?.cutout_replaced)]);
  const removed = removedAll.filter((id) => !isService(id) && !replacedIds.has(id));
  const service = removedAll.filter(isService);
  const rows = orderedIds(draft).map((id) => allRows[id]);
  const removedRows = removed.map((id) => allRows[id]);
  const serviceRows = service.map((id) => allRows[id]);
  const showStatusTabs = filter === "all" || filter.startsWith("variant:");
  const collageViews = useMemo((): CollageView[] => {
    const live = (id: number) => draft.order.includes(id) && !isRemoved(draft, id);
    const url = (path?: string | null) => path ? fileUrl(result.base_url, path) : null;
    const view = (key: string, collage: string | null, source: number | null, record: {
      page: number; size: [number, number]; title?: string | null; preview?: string | null; image?: number;
      state: CollageView["state"]; checked: boolean; boxes?: Box[]; suggestion?: Box[]; reason?: string;
    }, saved: number[], known = new Map<number, Box>()): CollageView => {
      const cut = draft.cuts[key];
      const members = cut ? cut.pieces.filter(live) : saved;
      const boxes = cut ? members.map((id) => draft.frames[id].box)
        : record.state === "suspected" || record.state === "whole" ? [] : record.boxes ?? [];
      const frames = members.flatMap((id) => {
        const box = draft.frames[id]?.box ?? report.images[id]?.crop_px ?? known.get(id);
        return box ? [{ box, variant: draftLocation(draft, report, id).variant }] : [];
      });
      const whole = boxes.length === 1 && boxes[0][0] === 0 && boxes[0][1] === 0 &&
        boxes[0][2] === record.size[0] && boxes[0][3] === record.size[1];
      const first = members[0];
      const location = first === undefined ? null : draftLocation(draft, report, first);
      return {
        key, collage, source, page: record.page, size: record.size,
        title: record.title || (first === undefined ? "" : draftImage(draft, report, first).title) || `Страница ${record.page}`,
        preview: url(record.preview) ?? (record.image === undefined ? null : url(report.images[record.image]?.preview)),
        state: cut ? whole ? "whole" : "manual" : record.state,
        checked: record.checked || (collage !== null && draft.collageChecked.includes(collage)) || !!cut,
        pending: !!cut, boxes: whole ? [] : boxes, suggestion: record.suggestion ?? [], members, frames, trim: cut?.trim,
        variant: location?.variant ?? null,
        variantLabel: location === null ? null : location.variant !== null ? variantLabel(labelled, location.variant)
          : folderLabel(report, location.folder),
        // Why the program cut it says nothing once a person cut it again or kept it whole.
        reason: cut || record.state === "whole" || record.state === "manual" ? undefined : record.reason,
      };
    };
    const views: CollageView[] = [];
    for (const record of report.collages ?? []) {
      if (record.state === "dismissed" || draft.collageDismissed.includes(record.id)) continue;
      const member = (id: number) => report.images[id]?.collage === record.id && report.images[id].copy_of === undefined;
      const saved = draft.order.filter((id) => !(id in draft.frames) && live(id) && member(id));
      // Automatic frames carry no box of their own: the record lists their boxes in the order of the frames.
      const own = report.images.flatMap((entry, id) => member(id) && !entry.collage_replaced ? [id] : []);
      const known = new Map(record.state === "auto" && own.length === record.boxes?.length
        ? own.map((id, at) => [id, record.boxes![at]] as const) : []);
      views.push(view(record.id, record.id, null, { ...record, boxes: record.boxes, suggestion: record.suggestion }, saved, known));
    }
    for (const [key, cut] of Object.entries(draft.cuts)) {
      if (cut.cutout || cut.collage !== null || cut.source === null) continue;
      const image = report.images[cut.source];
      views.push(view(key, null, cut.source, { page: image.page, size: image.size, title: image.title,
        preview: image.preview, state: "manual", checked: true }, []));
    }
    return views.sort((a, b) => a.page - b.page || a.key.localeCompare(b.key, "ru", { numeric: true }));
  }, [draft, report, result.base_url]);
  const collageByKey = new Map(collageViews.map((item) => [item.key, item]));
  const collageCheck = collageViews.filter(needsCheck).length;
  function collageOf(id: number): CollageView | undefined {
    const frame = draft.frames[id];
    if (frame) return collageByKey.get(frame.cut);
    const image = report.images[id];
    // A copy is a picture of its own even if an earlier version gave it the collage id.
    return image?.collage === undefined || image.copy_of !== undefined ? undefined : collageByKey.get(image.collage);
  }
  function startCut(view: CollageView) {
    if (saving) return;
    if (!editing) setEditing(true);
    setCutting(view);
  }
  function cutImage(id: number) {
    const known = collageOf(id);
    if (known) return startCut(known);
    if (id in draft.frames || id in draft.copies || id >= draft.sourceCount) {
      notify("Сначала сохраните изменения: разрезать можно сохранённое изображение.", true);
      return;
    }
    startCut(handCut(id));
  }
  /** A saved picture that is no collage yet, as the cut it starts. */
  function handCut(id: number): CollageView {
    const image = report.images[id];
    const location = draftLocation(draft, report, id);
    return { key: `m${id}`, collage: null, source: id, page: image.page, size: image.size,
      title: image.title || allRows[id]?.title || `Страница ${image.page}`,
      preview: image.preview ? fileUrl(result.base_url, image.preview) : null, state: "manual", checked: true,
      pending: false, boxes: [], suggestion: [], members: [id], frames: [], variant: location.variant,
      variantLabel: location.variant !== null ? variantLabel(labelled, location.variant) : folderLabel(report, location.folder) };
  }
  /** «Обрезать…»: crop one card where it stands. The crop is written at once, so nothing else may wait to be saved. */
  function cropImage(id: number) {
    if (saving) return;
    if (dirty || id in draft.frames || id in draft.copies || id >= draft.sourceCount) {
      notify("Сначала сохраните или отмените правки: обрезка сразу записывается в PDF.", true);
      return;
    }
    const known = collageOf(id);
    // A picture cut into several frames is changed by its lines, in the editor of the cut.
    if (known && (known.boxes.length > 1 || known.members.length > 1)) return startCut(known);
    const whole = new URL(`api/results/${encodeURIComponent(result.id)}/preview/${id}?revision=${report.revision}`, document.baseURI).href;
    setCropping(known
      ? { view: known, src: known.preview ?? whole, box: known.boxes[0] ?? null }
      : { view: handCut(id), src: whole, box: null });
  }
  async function applyCrop(box: Box) {
    if (!cropping) return;
    const { view } = cropping;
    let next: Draft;
    try {
      next = editDraft(draft, { type: "cut", key: view.key, collage: view.collage, source: view.source,
        replace: view.members.filter((id) => !(id in draft.frames)), boxes: [box], targets: ["keep"], trim: false });
    } catch (error) {
      notify(errorText(error), true);
      return;
    }
    if (await persist(next, "Изображение обрезано и записано в PDF. Резервная копия создана.")) setCropping(null);
  }
  /** «Повернуть»: saved cards turn where they stand. Like a crop, the turn is written at once: the PDF page
   * turns, its pixels stay as they were. */
  async function turnImages(ids: number[], turn: Turn) {
    if (saving || !ids.length) return;
    if (dirty || ids.some((id) => id in draft.frames || id in draft.copies || id >= draft.sourceCount)) {
      notify("Сначала сохраните или отмените правки: поворот сразу записывается в PDF.", true);
      return;
    }
    await persist(draft, ids.length > 1
      ? `Повёрнуто: ${ids.length} ${plural(ids.length, ["изображение", "изображения", "изображений"])}. PDF записаны, резервная копия создана.`
      : "Изображение повёрнуто и записано в PDF. Резервная копия создана.",
    { turns: Object.fromEntries(ids.map((id) => [String(id), turn])) });
  }
  function applyCut(view: CollageView, boxes: Box[], targets: (string | null | "keep")[], trim: boolean,
    names: Record<string, string> = {}) {
    const replace = view.members.filter((id) => !(id in draft.frames));
    change({ type: "cut", key: view.key, collage: view.collage, source: view.source, replace, boxes, targets, trim, names });
    setCutting(null);
  }
  const liveCard = useCallback((id: number) => draft.order.includes(id) && !isRemoved(draft, id), [draft]);
  // Every source of cutouts: saved records, merged with the draft, and sources cut only in the draft.
  const cutoutViews = useMemo((): CutoutSource[] => {
    const url = (path?: string | null) => path ? fileUrl(result.base_url, path) : null;
    const view = (key: string, record: string | null, from: { kind: "image" | "page"; page: number; area: Rect;
      size: [number, number]; image?: number | null; title?: string | null }, ids: number[]): CutoutSource => {
      const pieces = ids.map((id) => ({ id, rect: draft.frames[id]?.rect ?? boxRect(report.images[id].crop_px!, from.area, from.size),
        variant: draftLocation(draft, report, id).variant, marks: !!(draft.frames[id] ?? report.images[id]).marks }))
        .sort((a, b) => byPlace(a.rect, b.rect));
      const picture = from.kind === "image" && from.image != null ? from.image : null;
      const first = pieces[0]?.id ?? picture;
      const location = first === null ? null : draftLocation(draft, report, first);
      return {
        key, record, kind: from.kind, page: from.page, source: picture, area: from.area, size: from.size,
        title: from.kind === "page" ? `Страница ${from.page}`
          : (picture === null ? null : allRows[picture]?.title) || from.title || `Страница ${from.page}`,
        preview: from.kind === "page" ? pageUrl(result.id, from.page) : picture === null ? null : url(report.images[picture]?.preview),
        pending: key in draft.cuts, pieces, variant: location?.variant ?? null,
        variantLabel: location === null ? null : location.variant !== null ? variantLabel(labelled, location.variant)
          : folderLabel(report, location.folder),
      };
    };
    const views: CutoutSource[] = [];
    for (const record of report.cutouts ?? []) {
      const key = `cutout:${record.id}`;
      const cut = draft.cuts[key];
      views.push(view(key, record.id, record, cut ? cut.pieces.filter(liveCard)
        : report.images.flatMap((entry, id) => entry.cutout === record.id && entry.copy_of === undefined && liveCard(id) ? [id] : [])));
    }
    for (const [key, cut] of Object.entries(draft.cuts))
      if (cut.cutout && cut.cutout.record === null) views.push(view(key, null, { ...cut.cutout, image: cut.source }, cut.pieces.filter(liveCard)));
    // Pictures of a page first, in reading order; its snapshots after them.
    return views.sort((a, b) => a.page - b.page || (a.kind === b.kind ? byPlace(a.area, b.area) : a.kind === "page" ? 1 : -1));
  }, [draft, report, result.base_url, result.id, allRows, liveCard]);
  const shownCutouts = cutoutViews.filter((view) => view.pieces.length);
  const cutoutCount = shownCutouts.reduce((sum, view) => sum + view.pieces.length, 0);
  // One numbering for the gallery, the section and the editor, so «Вырезка 2» is the same cutout everywhere.
  const cutoutNumber = useMemo(() => cutoutNumbers(cutoutViews), [cutoutViews]);
  function cutoutOf(id: number): { view: CutoutSource; number: number } | undefined {
    const view = shownCutouts.find((value) => value.pieces.some((piece) => piece.id === id));
    return view && { view, number: cutoutNumber.get(id)! };
  }
  const pictureInfo = useCallback((index: number): PictureInfo => {
    const location = draftLocation(draft, report, index);
    return { title: allRows[index]?.title || `Страница ${report.images[index]?.page}`,
      preview: report.images[index]?.preview ? fileUrl(result.base_url, report.images[index].preview!) : null,
      variant: location.variant,
      variantLabel: location.variant !== null ? variantLabel(labelled, location.variant) : folderLabel(report, location.folder) };
  }, [draft, report, allRows, result.base_url]);
  /** Open the page editor on a card's page; a picture of its own starts with a frame at its edges. */
  function startCutout(id?: number, page?: number) {
    if (saving) return;
    if (!editing) setEditing(true);
    if (id === undefined) {
      setCutoutEditor({ page: page ?? shownCutouts[0]?.page ?? 1 });
      return;
    }
    const frame = draft.frames[id];
    const image = report.images[frame && frame.template >= 0 ? frame.template : id];
    const from = frame ? draft.cuts[frame.cut]?.cutout?.page : undefined;
    const own = !frame && image && !image.cutout && image.copy_of === undefined;
    setCutoutEditor({ page: from ?? image?.page ?? 1, picture: own ? id : undefined });
  }
  function applyCutouts(results: CutoutResult[], names: Record<string, string> = {}) {
    const changes = results.map(({ source, rects, targets, marks }): CutoutChange => {
      const saved = source.pieces.map((piece) => piece.id).filter((id) => !(id in draft.frames));
      const replace = [...saved, ...(rects.length && source.source !== null && liveCard(source.source) ? [source.source] : [])];
      return { key: source.key, record: source.record, kind: source.kind, page: source.page, source: source.source,
        area: source.area, size: source.size, replace, rects, targets, marks };
    });
    change({ type: "cutouts", changes, names });
    setCutoutEditor(null);
  }
  const variantLabels = useMemo(() => Object.fromEntries(Object.keys(draft.variants)
    .map((key) => [key, variantLabel(labelled, key)])), [draft, labelled]);
  const variants = useMemo(
    () => Object.keys(draft.variants).sort(compareVariants),
    [draft],
  );
  const folders = useMemo(
    () =>
      [
        ...new Set(
          rows
            .filter(
              (row) => row.variant === null && row.status === "named",
            )
            .map((row) => row.folder),
        ),
      ].sort((a, b) => folderLabel(report, a).localeCompare(folderLabel(report, b), "ru", { numeric: true }) ||
        a.localeCompare(b, "ru", { numeric: true })),
    [rows, report],
  );
  const visible = (filter === "removed" ? removedRows : filter === "service" ? serviceRows : rows).filter((row) => {
    const matches =
      filter === "all" ||
      filter === "removed" ||
      filter === "service" ||
      (filter === "needs-review" && row.status !== "named") ||
      filter === row.status ||
      filter === `variant:${row.variant}` ||
      filter === `folder:${row.folder}`;
    const text =
      `${row.title} ${row.label} ${row.folder} ${row.image.page} ${row.image.file}`.toLocaleLowerCase(
        "ru",
      );
    return (
      matches &&
      (!showStatusTabs || status === "all" || status === row.status) &&
      text.includes(searched.trim().toLocaleLowerCase("ru"))
    );
  });
  if (!filter.startsWith("variant:")) visible.sort((a, b) =>
    a.image.page - b.image.page || a.image.sequence - b.image.sequence || a.id - b.id);
  const visibleIds = visible.map((row) => row.id);
  // The gallery is one stop of Tab: the card last focused (or the first one) takes it, arrows move between cards.
  const [cursor, setCursor] = useState<number | null>(null);
  const tabStop = cursor !== null && visibleIds.includes(cursor) ? cursor : visibleIds[0];
  function cardKey(event: React.KeyboardEvent<HTMLButtonElement>, row: { id: number; variant: string | null }) {
    const index = visibleIds.indexOf(row.id);
    const cards = [...(grid.current?.querySelectorAll<HTMLElement>(".asset-card") ?? [])];
    const columns = Math.max(1, cards.findIndex((card) => card.offsetTop !== cards[0]?.offsetTop) === -1
      ? cards.length : cards.findIndex((card) => card.offsetTop !== cards[0].offsetTop));
    const steps: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns };
    let next: number | undefined;
    if (event.key in steps) next = visibleIds[Math.min(visibleIds.length - 1, Math.max(0, index + steps[event.key]))];
    else if (event.key === "Home") next = visibleIds[0];
    else if (event.key === "End") next = visibleIds.at(-1);
    if (next !== undefined) {
      event.preventDefault();
      const target = grid.current?.querySelector<HTMLElement>(`.asset-card[data-id="${next}"] .cover-button`);
      target?.focus();
      target?.scrollIntoView({ block: "nearest" });
      return;
    }
    if (event.key === "Enter" && editing) {
      // Enter opens the card large in editing too; Space picks it.
      event.preventDefault();
      showPreview(row.id);
    } else if (event.key === " " && editing) {
      event.preventDefault();
      setSelected((ids) => ids.includes(row.id) ? ids.filter((value) => value !== row.id) : [...ids, row.id]);
      anchor.current = row.id;
    } else if (event.key === "F2" && editing && row.variant === null && !removedSet.has(row.id) && !dirty && !saving) {
      event.preventDefault();
      renameFile(row.id);
    }
  }
  const visibleIdSet = new Set(visibleIds);
  const selectedIdSet = new Set(selected);
  const visibleSelected = visibleIds.filter((id) => selectedIdSet.has(id)).length;
  const counts = {
    named: rows.filter((row) => row.status === "named").length,
    review: rows.filter((row) => row.status === "review").length,
    unsigned: rows.filter((row) => row.status === "unsigned").length,
  };
  const reviewComplete = filter === "needs-review" && !query.trim() && counts.review + counts.unsigned === 0;
  const activeTitle = filter.startsWith("variant:")
    ? variantLabel(labelled, filter.slice(8))
    : filter.startsWith("folder:")
      ? folderLabel(report, filter.slice(7))
      : filter === "needs-review"
        ? "Нужно проверить"
        : filter === "removed"
          ? "Убранные"
        : filter === "service"
          ? "Служебные элементы"
        : filter === "collages"
          ? "Склейки"
        : filter === "cutouts"
          ? "Вырезки"
        : filter === "all"
          ? "Изображения"
          : STATUS[filter as keyof typeof STATUS];
  function select(
    id: number,
    event?: MouseEvent | React.ChangeEvent<HTMLInputElement>,
  ) {
    const click = event as MouseEvent | undefined;
    if (click?.shiftKey) {
      const range = selectRange(visibleIds, anchor.current ?? id, id);
      setSelected((ids) => [...ids.filter((value) => !visibleIdSet.has(value)), ...range]);
    }
    else if (click?.ctrlKey || click?.metaKey || event?.type === "change")
      setSelected((ids) =>
        ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id],
      );
    else setSelected((ids) => (ids.length === 1 && ids[0] === id ? [] : [id]));
    anchor.current = id;
  }
  /** `keepSelection`: a card's context menu acted on that card alone, the selection was not part of it. */
  function change(edit: Parameters<typeof editDraft>[1], keepSelection = false) {
    try {
      setDraft((previous) => editDraft(previous, edit));
      if (!keepSelection) setSelected([]);
    } catch (error) {
      notify(errorText(error), true);
    }
  }
  function move(target: string, ids = selected, before?: number) {
    if (ids.length) {
      change({ type: "move", ids, target, before });
    }
  }
  function reorder(direction: -1 | 1, ids = selected) {
    if (ids.length !== 1) return;
    const id = ids[0],
      key = draftVariant(draft, id);
    if (key === null) return;
    const members = draft.variants[String(key)],
      position = members.indexOf(id);
    if (position + direction < 0 || position + direction >= members.length)
      return;
    const before =
      direction === -1 ? members[position - 1] : members[position + 2];
    setDraft((previous) =>
      editDraft(previous, {
        type: "move",
        ids: [id],
        target: String(key),
        before,
      }),
    );
  }
  const undoSaved = useCallback(async (saved: Result) => {
    if (!mounted.current) {
      notify("Откройте этот результат, чтобы отменить его последнее сохранение.", true);
      return;
    }
    if (savingRef.current || savingState.current || dirtyRef.current) {
      notify("Сначала сохраните или сбросьте текущие правки, затем отмените сохранение.", true);
      return;
    }
    savingRef.current = true;
    setSaving(true);
    try {
      const value = await request<{ result: Result; message: string }>(
        `results/${saved.id}/undo-save`, { revision: saved.report.revision },
      );
      onResult(value.result);
      setSelected([]);
      setSavedHere("undone");
      notify("Последнее сохранение отменено. Предыдущие файлы восстановлены.");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      notify(errorText(error), true);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [notify, onResult]);
  const persist = useCallback(async (data: Draft, done = "Правки сохранены. Резервная копия создана.",
    extra: Record<string, unknown> = {}): Promise<boolean> => {
    if (savingRef.current || savingState.current) return false;
    savingRef.current = true;
    setSaving(true);
    try {
      const value = await request<{ result: Result; message: string; delivery_errors?: string[] }>(
        `results/${result.id}/apply`,
        { ...payload(data, report.revision), ...extra },
      );
      onResult(value.result);
      setSelected([]);
      onDirty(false);
      setSavedHere("saved");
      // One message keeps «Отменить сохранение» within reach: a clean copy in the user's folder that could not be
      // refreshed is told in it, as an error that stays until closed, instead of a second toast replacing the first.
      // The undo asks the same question as in «Ещё»: it throws away what this save wrote.
      const failed = value.delivery_errors?.length ? ` ${value.delivery_errors.join(" ")}` : "";
      notify(failed ? `Правки сохранены.${failed}` : done, Boolean(failed), {
        label: "Отменить сохранение", icon: <Undo2 size={16} />, run: () => setConfirmUndoSave(true),
      });
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      notify(errorText(error), true);
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [report.revision, result.id, onResult, onDirty, notify, undoSaved]);
  const save = useCallback(async (): Promise<boolean> => {
    if (savingRef.current || savingState.current) return false;
    return dirty ? persist(draft) : true;
  }, [dirty, draft, persist]);
  useEffect(() => {
    onSaveReady?.(save);
    return () => onSaveReady?.(null);
  }, [onSaveReady, save]);
  async function renameItem(event: React.FormEvent) {
    event.preventDefault();
    if (!rename) return;
    setSaving(true);
    try {
      const value = await request<{ result: Result; message: string }>(
        `results/${result.id}/rename`,
        { revision: report.revision, ...rename },
      );
      onResult(value.result);
      if (rename.kind === "folder" && filter === `folder:${rename.target}`) {
        const id = report.images.findIndex((image) => image.folder === rename.target && image.variant === null);
        setFilter(`folder:${value.result.report.images[id]?.folder || rename.name.trim()}`);
      }
      setRename(null);
      setSavedHere("saved");
      notify(value.message);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      notify(errorText(error), true);
    } finally {
      setSaving(false);
    }
  }
  /** Variants that have a saved PDF: an empty or unsaved variant has nothing to put into a folder. */
  const savedVariants = useMemo(() => new Set(report.documents
    .filter((document) => document.variant !== null && document.variant !== undefined)
    .map((document) => String(document.variant))), [report.documents]);
  function revealVariant(key: string) {
    void native("reveal_variant", result.id, key).catch((error) => notify(errorText(error), true));
  }
  /** Ask for a folder and put the saved PDFs of these variants straight into it; without keys all
   *  variants go into a folder named after the source PDF, made inside the chosen one. */
  async function exportVariants(keys?: string[], confirmed = false) {
    if (savingRef.current || savingState.current) return;
    // Unsaved edits and images left for review are asked about before the PDFs go out, not after a click.
    const open = keys ? 0 : counts.review + counts.unsigned;
    if (!confirmed && (dirty || open)) {
      setExportAsk({ keys, dirty, open });
      return;
    }
    let value: VariantExport | null;
    try {
      value = await native("export_variants", result.id, keys ?? null);
    } catch (error) {
      notify(errorText(error), true);
      return;
    }
    if (!value) return;
    onResult(value.result);
    if (value.errors.length) {
      notify(value.errors.join(" "), true);
      return;
    }
    const name = (path: string) => path.slice(path.lastIndexOf("\\") + 1);
    const first = keys?.length === 1 ? keys[0] : Object.keys(value.result.delivery ?? {})[0];
    notify(value.saved.length === 1
      ? `«${name(value.saved[0])}» сохранён в папку «${value.folder}». После следующих сохранений он обновится сам.`
      : `Сохранено PDF: ${value.saved.length} — в папку «${value.folder}». После следующих сохранений они обновятся сами.`,
    false, first ? { label: "Показать в Проводнике", icon: <FolderSearch size={16} />, run: () => revealVariant(first) } : undefined);
  }
  function open(relative = "") {
    void native("open_result", result.id, relative).catch((error) =>
      notify(errorText(error), true),
    );
  }
  useEffect(() => {
    onHelpTopic?.(filter === "collages" ? "collages" : filter === "cutouts" ? "cutouts" : editing ? "edit" : "review");
  }, [filter, editing, onHelpTopic]);
  const reordering = filter.startsWith("variant:");
  const { drag, startDrag } = useCardDrag({
    enabled: editing && !saving,
    grid,
    reorder: reordering,
    pick: (id) => removedSet.has(id) ? [] : selected.includes(id) ? selected.filter((value) => !removedSet.has(value)) : [id],
    takes: (id) => draftVariant(draft, id) !== null,
    onStart: (ids) => { setSelected(ids); setVariantsOpen(true); },
    onDrop: dropCards,
  });
  /** The cards of the grid with the gap the dragged cards would fill. */
  function withSlot(cards: ReactNode[]): ReactNode[] {
    if (drag?.slot == null) return cards;
    const slot = <div key="drag-slot" className="asset-card drag-slot" style={{ minHeight: drag.height }} aria-hidden="true" />;
    return [...cards.slice(0, drag.slot), slot, ...cards.slice(drag.slot)];
  }
  /** Cards put down at a place among the cards shown, beside a card of a variant or on a variant in the menu. */
  function dropCards(ids: number[], place: DropPlace) {
    if (place.kind === "variant") { move(place.key, ids); return; }
    const key = place.kind === "card" ? String(draftVariant(draft, place.id)) : filter.slice("variant:".length);
    const members = draft.variants[key] ?? [];
    const dragged = new Set(ids);
    const shown = (place.kind === "card" ? members : visibleIds).filter((id) => !dragged.has(id));
    const index = place.kind === "card" ? shown.indexOf(place.id) + (place.after ? 1 : 0) : place.index;
    move(key, ids, beforeAt(members, shown, dragged, index));
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const input = textEntry(event.target);
      if (event.defaultPrevented || document.querySelector("dialog[open], [role=menu]")) return;
      // Ctrl+F as in Explorer and every Windows program, Ctrl+K as before.
      if ((event.ctrlKey || event.metaKey) && (event.code === "KeyK" || event.code === "KeyF")) {
        event.preventDefault();
        search.current?.focus();
        return;
      }
      // Outside editing Ctrl+A would select the text of the whole window; there is nothing to select there.
      if (!editing && !input && (event.ctrlKey || event.metaKey) && event.code === "KeyA") {
        event.preventDefault();
        return;
      }
      if (!editing || input || saving) return;
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyA") {
        event.preventDefault();
        setSelected((ids) => [...new Set([...ids, ...visibleIds])]);
      }
      // Ctrl+Shift+Z repeats a step as Ctrl+Y does; it must never take one more step back.
      if ((event.ctrlKey || event.metaKey) && (event.code === "KeyY" || (event.code === "KeyZ" && event.shiftKey))) {
        event.preventDefault();
        change({ type: "redo" });
      } else if ((event.ctrlKey || event.metaKey) && event.code === "KeyZ") {
        event.preventDefault();
        change({ type: "undo" });
      }
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyS") {
        event.preventDefault();
        if (dirty) void save();
      }
      if (event.code === "Delete" && selected.some((id) => !removedSet.has(id))) {
        event.preventDefault();
        change({ type: "remove", ids: selected.filter((id) => !removedSet.has(id)) });
      }
      if (event.code === "Escape") setSelected([]);
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });
  const previewRow = preview === null ? undefined : allRows[preview];
  const pendingCopy = preview !== null && preview in draft.copies;
  const pendingLocation = previewRow && String(previewRow.variant ?? "") !== String(previewRow.image.variant ?? "");
  const previewDetailHeading = pendingCopy ? "Копия в черновике"
    : previewRow && removedSet.has(previewRow.id) && isService(previewRow.id) ? "Служебный элемент"
    : previewRow && removedSet.has(previewRow.id) ? "Убрано из результата"
    : previewRow?.image.removed ? "Восстановление в черновике"
    : pendingLocation ? "Изменение в черновике"
    : previewRow?.image.copy_of !== undefined ? "Копия изображения"
    : previewRow?.image.manual ? "Распределение вручную" : "Распознавание из PDF";
  const previewDetail = pendingCopy ? `Копия будет сохранена в «${previewRow?.label}». Исходное изображение останется на месте.`
    : previewRow && removedSet.has(previewRow.id) && isService(previewRow.id)
      ? `${previewRow.image.reason || "Элемент пропущен автоматически."} Если это нужное изображение, верните его и сохраните правки.`
    : previewRow && removedSet.has(previewRow.id) ? "Изображение убрано из результата. Верните его и сохраните правки, чтобы открыть PDF."
    : previewRow?.image.removed ? "Изображение восстановлено в черновике. Сохраните правки, чтобы открыть его PDF."
    : pendingLocation ? `Изображение будет помещено в «${previewRow?.label}» после сохранения правок.`
    : [previewRow?.image.reason || "Изображение извлечено из PDF. Подпись и расположение можно проверить в редакторе.",
      // A doubtful card names the captions it was torn between, so the choice of variant has something to go on.
      previewRow?.image.status === "review" && previewRow.image.candidates?.length
        ? `Возможные подписи: ${previewRow.image.candidates.map((caption) => `«${caption}»`).join(", ")}.` : ""]
      .filter(Boolean).join(" ");
  const previewSourceId = preview === null ? -1 : sourceId(draft, preview);
  const sourceKey = `${result.id}:${previewSourceId}:${report.revision}`;
  const previewKey = `${sourceKey}:${sourcePage ? "source" : "image"}`;
  const source = sourcePreview.key === sourceKey ? sourcePreview : null;
  useEffect(() => {
    if (!sourcePage || previewSourceId < 0) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    const url = new URL(`api/results/${encodeURIComponent(result.id)}/source/${previewSourceId}?revision=${report.revision}`, document.baseURI);
    void fetch(url, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) {
        const value = await response.json();
        throw new Error(value.error || "Не удалось показать исходную страницу PDF.");
      }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setSourcePreview({ key: sourceKey, url: objectUrl });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setSourcePreview({ key: sourceKey, error: errorText(error) });
    });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [sourcePage, sourceKey, previewSourceId, result.id, report.revision]);
  const natural = previewNatural?.key === previewKey ? previewNatural : {
    width: previewRow?.image.size?.[0] || 1, height: previewRow?.image.size?.[1] || 1,
  };
  const fitScale = Math.min(previewBounds.width / natural.width, previewBounds.height / natural.height);
  const previewWidth = Math.max(1, natural.width * fitScale * zoom);
  const previewHeight = Math.max(1, natural.height * fitScale * zoom);
  useLayoutEffect(() => {
    const viewport = previewViewport.current;
    if (preview === null || !viewport) return;
    const measure = () => {
      // The content box is independent of the zoomed canvas and its scrollbars.
      const rect = viewport.getBoundingClientRect();
      setPreviewBounds({ width: Math.floor(rect.width), height: Math.floor(rect.height) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [preview]);
  function previewLoaded(event: React.SyntheticEvent<HTMLImageElement>) {
    const image = event.currentTarget;
    setPreviewNatural({ key: previewKey, width: image.naturalWidth, height: image.naturalHeight });
  }
  /** `source` opens on the page of the original PDF, as «Исходная страница» inside the preview does. */
  function showPreview(id: number, source = false) {
    if (source) setSourcePreview({ key: "" });
    setSourcePage(source);
    setPreviewOrder(visibleIds);
    setPreview(id);
  }
  function stepPreview(direction: number) {
    const index = previewOrder.indexOf(preview!);
    if (index >= 0 && previewOrder[index + direction] !== undefined)
      setPreview(previewOrder[index + direction]);
  }
  useEffect(() => {
    setZoom(1);
    setPreviewFailed(false);
    previewViewport.current?.scrollTo(0, 0);
  }, [preview, report.revision, sourcePage]);
  useEffect(() => {
    if (preview === null) return;
    const viewport = previewViewport.current;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setZoom((value) => Math.max(0.25, Math.min(5, Math.round((value - event.deltaY * 0.003) * 100) / 100)));
    };
    const key = (event: KeyboardEvent) => {
      if (textEntry(event.target) || event.defaultPrevented || document.querySelector("[role=menu]") || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const index = previewOrder.indexOf(preview);
        const id = previewOrder[index + (event.key === "ArrowLeft" ? -1 : 1)];
        if (id !== undefined) setPreview(id);
      } else if (editing && !saving && /^[1-9]$/.test(event.key) && variants.includes(event.key)) {
        event.preventDefault();
        change({ type: "move", ids: [preview], target: event.key });
      } else if (editing && !saving && event.key === "0" && waitsForDecision(preview)) {
        // 0 leaves the open image as it is, as the digits send it to a variant.
        event.preventDefault();
        change({ type: "keep", ids: [preview] });
      }
    };
    viewport?.addEventListener("wheel", wheel, { passive: false });
    document.addEventListener("keydown", key);
    return () => {
      viewport?.removeEventListener("wheel", wheel);
      document.removeEventListener("keydown", key);
    };
  }, [preview, previewOrder, editing, saving, variants]);
  function revealIssue(page: number, sequence: number | null) {
    const related = allRows.find((row) => row.image.page === page &&
      (sequence === null || row.image.sequence === sequence)) || allRows.find((row) => row.image.page === page);
    if (!related) {
      notify(`Для страницы ${page} нет извлечённого изображения. Откройте исходный PDF и проверьте страницу.`, true);
      return;
    }
    setFilter(removedSet.has(related.id) ? isService(related.id) ? "service" : "removed" : "all");
    setStatus("all");
    setQuery("");
    window.requestAnimationFrame(() => {
      const card = document.querySelector<HTMLElement>(`.asset-card[data-id="${related.id}"]`);
      card?.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      card?.classList.add("issue-highlight");
      window.setTimeout(() => card?.classList.remove("issue-highlight"), 2000);
    });
  }
  function renameGroup() {
    if (filter.startsWith("variant:"))
      setRename({
        kind: "variant",
        target: filter.slice(8),
        name: activeTitle,
      });
    else if (filter.startsWith("folder:"))
      setRename({ kind: "folder", target: filter.slice(7), name: activeTitle });
  }
  function renameFile(id: number) {
    setRename({
      kind: "file",
      target: id,
      name: allRows[id].image.file.replaceAll("\\", "/").split("/").at(-1)?.replace(/\.pdf$/i, "") || "",
    });
  }
  function copyCaption(text: string) {
    navigator.clipboard.writeText(text).then(() => notify("Подпись скопирована."),
      () => notify("Не удалось скопировать подпись.", true));
  }
  // The right button offers what the card, the menu and the preview already have as buttons, nothing of its own.
  const context = useContextMenu();
  /** A selected card speaks for the whole selection; another card only for itself, and the selection stays. */
  function openCardMenu(event: MouseEvent, id: number) {
    if (drag) return;
    const ids = selected.includes(id) ? selected : [id];
    const single = ids.length === 1;
    const row = allRows[id];
    const active = ids.filter((value) => !removedSet.has(value));
    const removedIds = ids.filter((value) => removedSet.has(value));
    const key = single ? draftVariant(draft, id) : null;
    const members = key === null ? [] : draft.variants[String(key)];
    const position = members.indexOf(id);
    const homes = new Set(active.map((value) => draftVariant(draft, value)));
    const sizes = new Set(active.map(sizeKey).filter(Boolean));
    const same = sizes.size
      ? rows.filter((other) => !removedSet.has(other.id) && !ids.includes(other.id) && sizes.has(sizeKey(other.id))).map((other) => other.id)
      : [];
    const collage = removedSet.has(id) ? undefined : collageOf(id);
    const keep = !selected.includes(id);
    const items: ActionMenuItem[] = [
      { value: "preview", label: "Просмотреть", icon: <Maximize2 size={16} />, disabled: !single },
      { value: "source", label: `Исходная страница ${row.image.page}`, icon: <Scan size={16} />, disabled: !single },
      { value: "pdf", label: "Открыть готовый PDF", icon: <FileText size={16} />,
        disabled: !single || removedSet.has(id) || !!row.image.removed },
      { value: "caption", label: "Копировать подпись", icon: <ClipboardCopy size={16} />, disabled: !single },
      // Both are written to the PDF at once: with edits waiting for «Сохранить» they are off and say why.
      { value: "crop", label: dirty ? "Обрезать… — сначала сохраните правки" : "Обрезать…", icon: <Crop size={16} />, separator: true,
        disabled: saving || dirty || !single || !active.length },
      { value: "turn", label: dirty ? "Повернуть — сначала сохраните правки" : "Повернуть", icon: <RotateCw size={16} />,
        disabled: saving || dirty || !active.length, children: TURN_ITEMS },
    ];
    if (editing) items.push(
      { value: "move", label: "Перенести в вариант", icon: <FolderInput size={16} />, separator: true,
        disabled: saving || !active.length,
        children: [...variants.map((variant) => ({ value: `move:${variant}`, label: variantLabel(labelled, variant) })),
          { value: "move:return-to-misc", label: "Вернуть на прежнее место", separator: true,
            disabled: !active.some((value) => draftVariant(draft, value) !== null) },
          { value: "move:keep", label: "Оставить как есть", disabled: !active.some(waitsForDecision) }] },
      { value: "new", label: "Новый вариант…", icon: <Plus size={16} />, disabled: saving || !active.length },
      { value: "copy", label: active.length > 1 ? "Создать копии…" : "Создать копию…", icon: <Copy size={16} />,
        disabled: saving || !active.length },
      { value: "same", label: same.length ? `Выбрать такие же по размеру (${same.length})` : "Выбрать такие же по размеру",
        icon: <CopyCheck size={16} />, disabled: saving || !same.length },
      { value: "up", label: "Раньше в варианте", icon: <ArrowUp size={16} />, separator: true,
        disabled: saving || !single || position <= 0 },
      { value: "down", label: "Позже в варианте", icon: <ArrowDown size={16} />,
        disabled: saving || !single || position < 0 || position >= members.length - 1 },
      { value: "start", label: "В начало варианта", icon: <ArrowUpToLine size={16} />,
        disabled: saving || !active.length || homes.size !== 1 || homes.has(null) },
      { value: "end", label: "В конец варианта", icon: <ArrowDownToLine size={16} />,
        disabled: saving || !active.length || homes.size !== 1 || homes.has(null) },
    );
    if (editing && single && row.variant === null && !removedSet.has(id))
      items.push({ value: "rename", label: "Переименовать PDF…", icon: <Pencil size={16} />, separator: true,
        disabled: dirty || saving });
    if (editing) items.push(
      { value: "cut", label: "Разрезать склейку…", icon: <Scissors size={16} />,
        separator: !(single && row.variant === null && !removedSet.has(id)), disabled: saving || !single || !active.length },
      { value: "cutout", label: "Вырезать со страницы…", icon: <SquareScissors size={16} />,
        disabled: saving || !single || !active.length || id in draft.frames },
    );
    if (removedIds.length && !active.length)
      items.push({ value: "restore", label: single ? "Вернуть изображение" : "Вернуть изображения",
        icon: <RotateCcw size={16} />, separator: true, disabled: saving });
    else if (editing)
      items.push({ value: "remove", label: "Убрать", icon: <Trash2 size={16} />, danger: true, separator: true,
        disabled: saving || !active.length });
    context.open(event, {
      heading: single ? row.title : `Выбрано: ${ids.length}`,
      items,
      onAction(value) {
        if (value === "preview") showPreview(id);
        else if (value === "source") showPreview(id, true);
        else if (value === "pdf") open(row.image.file);
        else if (value === "caption") copyCaption(row.title);
        else if (value.startsWith("turn:")) void turnImages(active, Number(value.slice(5)) as Turn);
        else if (value === "move:return-to-misc") change({ type: "return-to-misc", ids: active }, keep);
        else if (value === "move:keep") change({ type: "keep", ids: active.filter(waitsForDecision) }, keep);
        else if (value.startsWith("move:")) change({ type: "move", ids: active, target: value.slice(5) }, keep);
        else if (value === "new") setNewVariant(active);
        else if (value === "copy") startCopy(active);
        else if (value === "same") setSelected((chosen) => [...new Set([...chosen, ...ids, ...same])]);
        else if (value === "up" || value === "down") reorder(value === "up" ? -1 : 1, [id]);
        else if (value === "start" || value === "end") change({ type: "move-edge", ids: active, edge: value }, keep);
        else if (value === "rename") renameFile(id);
        else if (value === "cut") collage?.state === "suspected" ? startCut(collage) : cutImage(id);
        else if (value === "cutout") startCutout(id);
        else if (value === "crop") cropImage(id);
        else if (value === "remove") change({ type: "remove", ids: active }, keep);
        else if (value === "restore") { if (!editing) setEditing(true); change({ type: "restore", ids: removedIds }, keep); }
      },
    });
  }
  /** The picture in the preview: the view switch, the scale and the same edits as the row above it. */
  function openPreviewMenu(event: MouseEvent, id: number) {
    const row = allRows[id];
    const removedNow = removedSet.has(id);
    const items: ActionMenuItem[] = [
      sourcePage
        ? { value: "image", label: "Извлечённое изображение", icon: <Images size={16} /> }
        : { value: "source", label: `Исходная страница ${row.image.page}`, icon: <Scan size={16} /> },
      { value: "fit", label: "Вместить изображение", icon: <Scan size={16} />, disabled: zoom === 1 },
      { value: "zoom", label: "Увеличить вдвое", icon: <Plus size={16} />, disabled: zoom >= 5 },
      { value: "pdf", label: "Открыть готовый PDF", icon: <FileText size={16} />, separator: true,
        disabled: removedNow || !!row.image.removed },
      { value: "caption", label: "Копировать подпись", icon: <ClipboardCopy size={16} /> },
      { value: "turn", label: dirty ? "Повернуть — сначала сохраните правки" : "Повернуть", icon: <RotateCw size={16} />,
        disabled: saving || dirty || removedNow || !!row.image.removed, children: TURN_ITEMS },
    ];
    if (editing) items.push(
      { value: "move", label: "Перенести в вариант", icon: <FolderInput size={16} />, separator: true, disabled: saving,
        children: [...variants.map((variant) => ({ value: `move:${variant}`, label: variantLabel(labelled, variant) })),
          { value: "move:return", label: "Вернуть на прежнее место", separator: true,
            disabled: row.variant === null || removedNow }] },
      { value: "cut", label: "Разрезать склейку…", icon: <Scissors size={16} />, disabled: saving || removedNow },
      { value: "cutout", label: "Вырезать со страницы…", icon: <SquareScissors size={16} />, disabled: saving || removedNow },
      removedNow
        ? { value: "restore", label: "Вернуть изображение", icon: <RotateCcw size={16} />, disabled: saving }
        : { value: "remove", label: "Убрать изображение", icon: <Trash2 size={16} />, danger: true, disabled: saving },
    );
    context.open(event, {
      heading: row.title,
      items,
      onAction(value) {
        if (value === "image") setSourcePage(false);
        else if (value === "source") { setSourcePreview({ key: "" }); setSourcePage(true); }
        else if (value === "fit") { setZoom(1); previewViewport.current?.scrollTo(0, 0); }
        else if (value === "zoom") setZoom((current) => Math.min(5, current * 2));
        else if (value === "pdf") open(row.image.file);
        else if (value === "caption") copyCaption(row.title);
        else if (value.startsWith("turn:")) void turnImages([id], Number(value.slice(5)) as Turn);
        else if (value === "move:return") change({ type: "return-to-misc", ids: [id] });
        else if (value.startsWith("move:")) move(value.slice(5), [id]);
        else if (value === "cut") cutImage(id);
        else if (value === "cutout") { setPreview(null); startCutout(id); }
        else if (value === "restore" || value === "remove") change({ type: value, ids: [id] });
      },
    });
  }
  /** A variant or a caption in the menu: open it, take all its cards, bring the selection there, rename it. */
  function openGroupMenu(event: MouseEvent, key: string, title: string) {
    const variant = key.startsWith("variant:") ? key.slice(8) : null;
    const members = variant !== null ? draft.variants[variant] ?? []
      : rows.filter((row) => row.folder === key.slice(7) && row.variant === null).map((row) => row.id);
    const items: ActionMenuItem[] = [
      { value: "open", label: "Открыть", icon: <FolderOpen size={16} /> },
      { value: "all", label: "Выбрать все карточки", icon: <ListChecks size={16} />,
        disabled: saving || !members.some((id) => !removedSet.has(id)) },
    ];
    if (variant !== null && editing) items.push(
      { value: "here", label: selectedActive.length ? `Перенести выбранные сюда (${selectedActive.length})` : "Перенести выбранные сюда",
        icon: <FolderInput size={16} />, disabled: saving || !selectedActive.some((id) => draftVariant(draft, id) !== variant) },
      { value: "copy", label: "Создать копию PDF варианта…", icon: <Copy size={16} />, disabled: saving || !members.length },
    );
    items.push({ value: "rename", label: variant !== null ? "Переименовать вариант…" : "Переименовать подпись…",
      icon: <Pencil size={16} />, separator: true, disabled: dirty || saving });
    if (variant !== null) {
      items.push({ value: "export", label: "Сохранить PDF в папку…", icon: <FolderOutput size={16} />, separator: true,
        disabled: saving || !savedVariants.has(variant) });
      if (result.delivery?.[variant]?.length)
        items.push({ value: "reveal", label: "Показать копию в Проводнике", icon: <FolderSearch size={16} /> });
    }
    context.open(event, {
      heading: title,
      items,
      onAction(value) {
        if (value === "open") { setFilter(key); setStatus("all"); }
        else if (value === "all") {
          if (!editing) setEditing(true);
          setSelected((chosen) => [...new Set([...chosen, ...members.filter((id) => !removedSet.has(id))])]);
        } else if (value === "here") move(variant!, selectedActive);
        else if (value === "copy") startCopy(members, true);
        else if (value === "rename") setRename(variant !== null
          ? { kind: "variant", target: variant, name: title } : { kind: "folder", target: key.slice(7), name: title });
        else if (value === "export") void exportVariants([variant!]);
        else if (value === "reveal") revealVariant(variant!);
      },
    });
  }
  function startCopy(ids: number[], whole = false) {
    if (!ids.length || saving) return;
    setNumber(nextVariant(variants));
    setCopyTarget(whole ? "new" : "same");
    setCopying({ ids: [...ids], whole });
  }
  function createCopies(event: React.FormEvent) {
    event.preventDefault();
    if (!copying) return;
    const choice = copyTarget === "new" ? variantChoice(number, variantLabels) : { key: copyTarget };
    if ("error" in choice) {
      notify(choice.error, true);
      return;
    }
    if (copyTarget === "new" && variants.includes(choice.key)) {
      notify("Такой вариант уже есть. Укажите свободный номер, новое название или выберите готовый вариант.", true);
      return;
    }
    try {
      const target = choice.key;
      const next = editDraft(draft, { type: "copy", ids: copying.ids, target, name: choice.name });
      setDraft(next);
      setSelected(next.order.filter((id) => !draft.order.includes(id)));
      setCopying(null);
      if (target !== "same") { setFilter(`variant:${target}`); setStatus("all"); setQuery(""); }
    } catch (error) { notify(errorText(error), true); }
  }
  /** The mark on a variant's dot in the folded menu: its number, or the first letter of a name of its own. */
  function dotMark(key: string) {
    const label = variantLabel(labelled, key);
    return /^(под)?вариант\s/i.test(label) ? key : label.charAt(0).toLocaleUpperCase("ru");
  }
  function sidebar(
    key: string,
    title: string,
    count: number,
    icon: ReactNode,
    variant?: string,
  ) {
    return (
      <button
        key={key}
        aria-current={filter === key ? "page" : undefined}
        className={`project-link ${filter === key ? "active" : ""}${variant?.includes(".") ? " is-subvariant" : ""}${drag?.place?.kind === "variant" && drag.place.key === variant ? " is-drop-target" : ""}`}
        title={title}
        onClick={() => {
          setFilter(key);
          setStatus("all");
        }}
        onContextMenu={key.startsWith("variant:") || key.startsWith("folder:")
          ? (event) => openGroupMenu(event, key, title) : undefined}
        data-drop-variant={variant}
      >
        <span>
          {icon}
          <span className="nav-text">{title}</span>
        </span>
        <b>{count}</b>
      </button>
    );
  }
  const selectedVariant = selected.length === 1 ? draftVariant(draft, selected[0]) : null;
  const selectedMembers = selectedVariant === null ? [] : draft.variants[String(selectedVariant)];
  const selectedPosition = selectedMembers.indexOf(selected[0]);
  const selectedRemoved = selected.filter((id) => removedSet.has(id));
  const selectedActive = selected.filter((id) => !removedSet.has(id));
  const selectionVariants = new Set(selectedActive.map((id) => draftVariant(draft, id)));
  // «Такие же по размеру»: a repeated key plan or title strip shares its pixel size on every slide.
  const sizeKey = (id: number) => allRows[id]?.image.size?.join("×");
  const selectedSizes = new Set(selectedActive.map(sizeKey).filter(Boolean));
  const chosenIds = new Set(selected);
  const sameSizeIds = selectedSizes.size
    ? rows.filter((row) => !removedSet.has(row.id) && !chosenIds.has(row.id) && selectedSizes.has(sizeKey(row.id))).map((row) => row.id)
    : [];
  const canMoveEdge = selectedActive.length > 0 && selectionVariants.size === 1 && !selectionVariants.has(null);
  const variantCount = variants.filter((key) => draft.variants[key].length && !key.includes(".")).length;
  const subvariantCount = variants.filter((key) => draft.variants[key].length && key.includes(".")).length;
  const variantSummary = `Варианты: ${variantCount}` + (subvariantCount ? `, подвариантов: ${subvariantCount}` : "");
  const parents = [...new Set(variants.map(variantParent))];
  // A subvariant shares its variant's colour, so 3 and 3.1 read as one family.
  const dotOf = (key: string) => `color-${Math.max(0, parents.indexOf(variantParent(key))) % 4}`;
  const sectionPage = filter === "collages" || filter === "cutouts";
  return (
    <Shell
      footer={footer}
      navigation={
        <>
          <button className="project-link back-link" aria-label="Вернуться в историю обработки" onClick={onBack}>
            <span>
              <ArrowLeft size={16} />История обработки
            </span>
          </button>
          <div className="nav-label">Проект</div>
          {sidebar("all", "Все изображения", rows.length, <Images size={16} />)}
          {sidebar("removed", "Убранные", removed.length, <Trash2 size={16} />)}
          {!!report.images.some((image) => image.suppressed || image.suppression_code) &&
            sidebar("service", "Служебные", service.length, <Shapes size={16} />)}
          {/* Always listed: any picture can be cut by hand, even when the program found no collage. */}
          {sidebar("collages", "Склейки", collageViews.length,
            <span className={`nav-icon-attention${collageCheck ? " has-attention" : ""}`}
              title={collageCheck ? `Нужно проверить: ${collageCheck}` : undefined}><Scissors size={16} /></span>)}
          {sidebar("cutouts", "Вырезки", cutoutCount, <Crop size={16} />)}
          {/* Counted as in the heading of the result: whole variants, their subvariants are listed under them. */}
          <button className="nav-group-toggle" aria-label={variantSummary} title={variantSummary}
            aria-expanded={variantsOpen} aria-controls="variant-navigation"
            onClick={(event) => { event.stopPropagation(); setVariantsOpen((value) => !value); }}>
            <Layers3 size={16} /><span>Варианты</span><b>{variantCount}</b><ChevronDown size={16} />
          </button>
          <Collapsible id="variant-navigation" open={variantsOpen}>
          {variants.map((key) =>
            sidebar(
              `variant:${key}`,
              variantLabel(labelled, key),
              draft.variants[key].length,
              // A subvariant shares its variant's colour, so 3 and 3.1 read as one family.
              <span className={`variant-dot ${dotOf(key)}`}
                aria-hidden="true"><i>{dotMark(key)}</i></span>,
              key,
            ),
          )}
          {!variants.length && (
            <p className="nav-empty">Варианты пока не определены</p>
          )}
          </Collapsible>
          <div className="nav-label spaced">Статус</div>
          {sidebar(
            "review",
            "На проверку",
            counts.review,
            <ScanEye size={16} />,
          )}
          {sidebar(
            "unsigned",
            "Без подписи",
            counts.unsigned,
            <Tag size={16} />,
          )}
          {!!folders.length && (
            <>
              <button className="nav-group-toggle" aria-label={`Подписи: ${folders.length}`} title={`Подписи: ${folders.length}`}
                aria-expanded={foldersOpen} aria-controls="folder-navigation"
                onClick={(event) => { event.stopPropagation(); setFoldersOpen((value) => !value); }}>
                <Tags size={16} /><span>Подписи</span><b>{folders.length}</b><ChevronDown size={16} />
              </button>
              <Collapsible id="folder-navigation" className="category-list" open={foldersOpen}>
                {folders.map((folder, i) =>
                  sidebar(
                    `folder:${folder}`,
                    folderLabel(report, folder),
                    rows.filter(
                      (row) => row.folder === folder && row.variant === null,
                    ).length,
                    <span className="variant-dot folder-dot" aria-hidden="true"><i>{i + 1}</i></span>,
                  ),
                )}
              </Collapsible>
            </>
          )}
        </>
      }
      header={
        <>
          <label className="searchbox">
            <Search size={16} />
            <input
              ref={search}
              type="search"
              placeholder="Найти изображение или подпись"
              aria-label="Найти изображение или подпись"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <kbd>Ctrl K</kbd>
          </label>
          <div className="topbar-actions">
            {(saving || dirty || savedHere) && <StatusNote busy={saving} dirty={dirty} undone={!saving && !dirty && savedHere === "undone"}>
              {saving
                ? "Сохраняю…"
                : dirty
                  ? "Есть изменения"
                  : savedHere === "undone" ? "Сохранение отменено" : "Изменения сохранены"}
            </StatusNote>}
            {/* The main action of a result; while editing, «Сохранить» in the toolbar takes over. */}
            <button
              className={`btn ${editing ? "secondary" : "primary"}`}
              disabled={saving}
              onClick={() => {
                if (editing && dirty) {
                  finishEditing.current = true;
                  setReset(true);
                } else {
                  setEditing(!editing);
                  setSelected([]);
                }
              }}
            >
              <Pencil size={16} />
              {editing ? "Завершить правки" : "Редактировать"}
            </button>
          </div>
        </>
      }
    >
      <div className={`content viewer-content${editing ? " is-editing" : ""}`}>
        <div className="breadcrumbs">
          <button aria-label="Открыть историю обработки" onClick={onBack}>История обработки</button>
          <ChevronRight size={12} />
          <span title={result.title}>{result.title}</span>
        </div>
        <div className="page-heading viewer-heading">
          <div>
            <h1>{result.title}</h1>
            <p className="viewer-facts">
              <span>{rows.length} {plural(rows.length, ["изображение", "изображения", "изображений"])}</span>
              <span>{variantCount} {plural(variantCount, ["вариант", "варианта", "вариантов"])}</span>
              {!!subvariantCount && <span>{subvariantCount} {plural(subvariantCount, ["подвариант", "подварианта", "подвариантов"])}</span>}
              <span>{report.documents.length} {plural(report.documents.length, ["готовый", "готовых", "готовых"])} PDF</span>
              <span>{report.pages} {plural(report.pages, ["страница", "страницы", "страниц"])} в презентации</span>
            </p>
          </div>
          {savedVariants.size > 0 && <button className="btn secondary export-all" disabled={saving} aria-label="Варианты в папку…"
            title="Создать в выбранном месте папку с названием исходного PDF и положить в неё PDF всех вариантов — без служебных файлов"
            onClick={() => void exportVariants()}>
            <FolderOutput size={16} />
            <span>Варианты в папку…</span>
          </button>}
          <button className="btn secondary result-folder" onClick={() => open()} aria-label="Папка результата" title="Открыть папку результата в Проводнике">
            <FolderOpen size={16} />
            <span>Папка результата</span>
          </button>
        </div>
        {/* The review of the gallery and its card tools have nothing to work on in the collage and cutout sections. */}
        {!sectionPage && <ReviewSummary key={result.id} review={counts.review} unsigned={counts.unsigned}
          warnings={report.warnings || []} errors={report.errors || []}
          serviceCount={report.suppressed?.length || 0} canRestoreService={service.length > 0}
          collages={{ total: collageViews.length, check: collageCheck }}
          onCollages={() => { setFilter("collages"); setStatus("all"); setQuery(""); }}
          onReview={() => {
            setFilter("needs-review"); setStatus("all"); setQuery("");
            // The doubtful images open ready to move, as after «Редактировать».
            if (!editing) { setEditing(true); setSelected([]); }
          }}
          onService={() => { setFilter("service"); setStatus("all"); setQuery(""); }}
          onReveal={revealIssue} />}
        {sectionPage && editing && (dirty || conflict) && (
          <section className="section-save-bar" aria-label="Несохранённые правки">
            <Pencil size={16} aria-hidden="true" />
            <p role="status">{conflict ? "Результат изменён в другом окне. Загрузите актуальную версию и повторите правки."
              : saving ? "Сохраняю правки…" : "Есть несохранённые правки. В готовые PDF они запишутся по «Сохранить»."}</p>
            <button className="btn compact secondary" disabled={saving || !draft.past.length} title="Отменить шаг · Ctrl+Z"
              onClick={() => change({ type: "undo" })}><Undo2 size={16} />Отменить шаг</button>
            <button className="btn compact secondary" disabled={saving} onClick={() => setReset(true)}>
              <RotateCcw size={16} />{conflict ? "Обновить" : "Сбросить"}</button>
            <button className="btn compact primary editor-save" disabled={saving || !dirty} title="Сохранить · Ctrl+S" onClick={() => void save()}>
              {saving ? <LoaderCircle size={16} className="spin" /> : <Save size={16} />}{saving ? "Сохраняю…" : "Сохранить"}</button>
          </section>
        )}
        <Presence>{editing && !sectionPage && (
          <EditorToolbar
            selected={selected.length}
            visibleSelected={visibleSelected}
            visible={visible.length}
            variants={variants.map((key) => ({
              value: key, label: variantLabel(labelled, key),
            }))}
            dirty={dirty}
            saving={saving}
            canUndo={!!draft.past.length}
            canRedo={!!draft.future?.length}
            canMoveUp={selected.length === 1 && selectedPosition > 0}
            canMoveDown={selected.length === 1 && selectedPosition >= 0 && selectedPosition < selectedMembers.length - 1}
            canMoveEdge={canMoveEdge}
            canReturn={selectedActive.some((id) => draftVariant(draft, id) !== null)}
            canKeep={selectedActive.some(waitsForDecision)}
            selectedRemoved={selectedRemoved.length}
            selectedActive={selectedActive.length}
            removed={removed.length}
            canUndoSave={!!report.can_undo_save}
            conflict={conflict}
            onSelectVisible={() => setSelected((ids) => {
              const chosen = new Set(ids);
              const allVisible = visibleIds.length > 0 && visibleIds.every((id) => chosen.has(id));
              return allVisible
                ? ids.filter((id) => !visibleIdSet.has(id))
                : [...new Set([...ids, ...visibleIds])];
            })}
            onClearSelection={() => { setSelected([]); anchor.current = null; }}
            onMove={(target) => move(target)}
            onNewVariant={() => {
              setNewVariant(selected);
            }}
            onReorder={reorder}
            onMoveEdge={(edge) => change({ type: "move-edge", ids: selectedActive, edge })}
            onReturn={() => change({ type: "return-to-misc", ids: selectedActive })}
            onKeep={() => change({ type: "keep", ids: selectedActive.filter(waitsForDecision) })}
            onRestore={() => change({ type: "restore", ids: selectedRemoved })}
            onShowRemoved={() => { setFilter("removed"); setStatus("all"); setQuery(""); }}
            onUndoSave={() => setConfirmUndoSave(true)}
            onRemove={() => change({ type: "remove", ids: selectedActive })}
            onCopy={() => startCopy(selectedActive)}
            onHelp={() => onHelp?.("edit")}
            sameSize={sameSizeIds.length}
            onSelectSameSize={() => setSelected((ids) => [...new Set([...ids, ...sameSizeIds])])}
            canCut={selectedActive.length === 1}
            onCut={() => cutImage(selectedActive[0])}
            onCutout={() => startCutout(selectedActive.length === 1 ? selectedActive[0] : undefined)}
            onUndo={() => change({ type: "undo" })}
            onRedo={() => change({ type: "redo" })}
            onReset={() => setReset(true)}
            onSave={() => void save()}
          />
        )}</Presence>
        {filter === "collages" ? <>
          <CollagesPage views={collageViews} saving={saving} onCut={startCut}
            onBrowse={() => { setFilter("all"); setStatus("all"); }}
            onCheck={(view) => { if (!editing) setEditing(true); change({ type: "collage-check", id: view.collage! }); }}
            onDismiss={(view) => { if (!editing) setEditing(true); change({ type: "collage-dismiss", id: view.collage! }); }}
            onWhole={(view) => { if (!editing) setEditing(true); applyCut(view, [[0, 0, view.size[0], view.size[1]]], ["keep"], false); }} />
        </> : filter === "cutouts" ? <>
          <CutoutsPage views={shownCutouts} count={cutoutCount} saving={saving} onOpen={(page) => startCutout(undefined, page)}
            onReturn={(view) => { if (!editing) setEditing(true); applyCutouts([{ source: view, rects: [], targets: [], marks: [] }]); }} />
        </> : <>
        <div className="library-header">
          <div>
            <h2>{activeTitle}</h2>
            <span aria-live="polite">
              {visible.length} из {filter === "removed" ? removed.length : filter === "service" ? service.length : rows.length}
            </span>
            {editing &&
              (filter.startsWith("variant:") ||
                filter.startsWith("folder:")) && (
                <button
                  className="icon-btn"
                  aria-label={filter.startsWith("variant:") ? "Переименовать вариант" : "Переименовать подпись"}
                  title={
                    dirty
                      ? "Сначала сохраните или сбросьте правки"
                      : filter.startsWith("variant:") ? "Переименовать вариант" : "Переименовать подпись"
                  }
                  disabled={dirty || saving}
                  onClick={renameGroup}
                >
                  <Pencil size={16} />
                </button>
              )}
            {filter.startsWith("variant:") && <button className="btn compact secondary variant-export"
              aria-label="Сохранить PDF варианта в папку"
              title={result.delivery?.[filter.slice(8)]?.length
                ? `Сейчас копия лежит в «${result.delivery[filter.slice(8)][0].folder}». Можно выбрать другую папку`
                : "Положить PDF этого варианта в выбранную папку — сразу файлом, без вложенной папки"}
              disabled={saving || !savedVariants.has(filter.slice(8))}
              onClick={() => void exportVariants([filter.slice(8)])}>
              <FolderOutput size={16} /><span>В папку…</span>
            </button>}
          </div>
          {editing && filter.startsWith("variant:") && <button className="btn compact secondary"
            aria-label="Создать копию PDF варианта" title="Создать копию всего PDF варианта"
            disabled={saving || !draft.variants[filter.slice(8)]?.length}
            onClick={() => startCopy(draft.variants[filter.slice(8)] || [], true)}>
            <Copy size={16} /><span>Копия PDF</span>
          </button>}
          {showStatusTabs && <div
            className="filter-tabs"
            role="group"
            aria-label="Фильтр по статусу"
          >
            {[
              ["all", "Все"],
              ["named", "С подписью"],
              ["review", "На проверку"],
              ["unsigned", "Без подписи"],
            ].map(([key, title]) => (
              <button
                key={key}
                className={status === key ? "active" : ""}
                aria-pressed={status === key}
                onClick={() => setStatus(key)}
              >
                {title}
              </button>
            ))}
          </div>}
        </div>
        {filter === "service" && <div className="section-note">
          <Shapes size={16} aria-hidden="true" /><p>Программа пропустила логотипы, повторяющиеся значки, мелкие фрагменты и закрытые картинки. Возвращённые появятся в «Без подписи» после сохранения.</p>
        </div>}
        {filter === "removed" && <div className="section-note">
          <RotateCcw size={16} aria-hidden="true" /><p>Убранные изображения, в том числе уже сохранённые. Верните нужные карточки и сохраните результат.</p>
        </div>}
        {visible.length ? (
          <div className="asset-grid" ref={grid}>
            {withSlot((drag?.slot != null ? visible.filter((row) => !drag.ids.includes(row.id)) : visible).map((row) => (
              <article
                key={row.id}
                data-id={row.id}
                className={`asset-card ${selected.includes(row.id) ? "selected-card" : ""}${removedSet.has(row.id) ? " removed-card" : ""}${drag?.ids.includes(row.id) ? " is-dragging" : ""}${drag?.place?.kind === "card" && drag.place.id === row.id ? ` is-insertion-target${drag.place.after ? " is-after" : ""}` : ""}`}
                onPointerDown={(event) => startDrag(event, row.id)}
                onContextMenu={(event) => openCardMenu(event, row.id)}
              >
                <div className="asset-image">
                  <button
                    className="cover-button"
                    aria-label={`${editing ? "Выбрать" : "Просмотреть"}: ${row.title}, страница ${row.image.page}`}
                    aria-pressed={editing ? selected.includes(row.id) : undefined}
                    tabIndex={row.id === tabStop ? 0 : -1}
                    onFocus={() => setCursor(row.id)}
                    onKeyDown={(event) => cardKey(event, row)}
                    disabled={saving}
                    onClick={(e) =>
                      editing ? select(row.id, e) : showPreview(row.id)
                    }
                    onDoubleClick={() => {
                      if (editing) showPreview(row.id);
                    }}
                  >
                    {row.id in draft.frames && collageOf(row.id)?.preview ? (
                      <CroppedImage src={collageOf(row.id)!.preview!} size={collageOf(row.id)!.size}
                        box={draft.frames[row.id].box} alt={row.title} />
                    ) : row.id in draft.frames && cutoutOf(row.id)?.view.preview ? (
                      <CroppedImage src={cutoutOf(row.id)!.view.preview!} size={cutoutOf(row.id)!.view.size}
                        box={draft.frames[row.id].box} alt={row.title} />
                    ) : row.image.preview ? (
                      <img
                        draggable={false}
                        src={fileUrl(result.base_url, row.image.preview)}
                        alt={row.title}
                        style={{
                          maxWidth: row.image.size?.[0] > 0 ? row.image.size[0] : undefined,
                          maxHeight: row.image.size?.[1] > 0 ? row.image.size[1] : undefined,
                        }}
                        loading="lazy"
                        decoding="async"
                      />
                    ) : (
                      <span className="no-preview">
                        <ImageOff size={32} />
                        Без миниатюры
                      </span>
                    )}
                  </button>
                  {editing && (
                    <label className="pick">
                      <input
                        type="checkbox"
                        tabIndex={-1}
                        aria-label={`Выбрать изображение ${row.id + 1}`}
                        checked={selected.includes(row.id)}
                        disabled={saving}
                        onChange={(e) => select(row.id, e)}
                      />
                      <span />
                    </label>
                  )}
                  {/* A caption found is the usual case: only the cards that need attention carry a badge. */}
                  {(removedSet.has(row.id) || row.status !== "named") && <span className={`status-badge ${removedSet.has(row.id) ? "removed" : row.status}`}>
                    {removedSet.has(row.id) ? isService(row.id) ? <Shapes size={12} /> : <Trash2 size={12} /> : row.status === "review" ? (
                      <ScanEye size={12} />
                    ) : (
                      <Tag size={12} />
                    )}
                    {removedSet.has(row.id) ? isService(row.id) ? serviceLabel(row.image.suppression_code) : "Убрано" : STATUS[row.status as keyof typeof STATUS]}
                  </span>}
                  {(() => {
                    const collage = removedSet.has(row.id) ? undefined : collageOf(row.id);
                    if (!collage) return null;
                    if (collage.state === "suspected") return <>
                      <span className="collage-chip amber collage-badge"><TriangleAlert size={12} />Возможно, склейка</span>
                      {editing && <button className="btn compact secondary collage-cut-button" disabled={saving}
                        onClick={() => startCut(collage)}><Scissors size={16} />Разрезать…</button>}
                    </>;
                    const number = collage.members.indexOf(row.id) + 1;
                    return number > 0 && collage.boxes.length > 1 ? <span className="collage-chip dark collage-badge">
                      <Scissors size={12} />Кадр {number} из склейки</span> : null;
                  })()}
                  {(() => {
                    const piece = removedSet.has(row.id) ? undefined : cutoutOf(row.id);
                    return piece ? <span className="collage-chip dark collage-badge">
                      <Crop size={12} />Вырезка {piece.number} со стр. {piece.view.page}</span> : null;
                  })()}
                  {editing && !saving && !removedSet.has(row.id) && !(row.id in draft.frames) && collageOf(row.id)?.state !== "suspected" &&
                    <button className="btn compact secondary cutout-cut-button" tabIndex={-1} onClick={() => startCutout(row.id)}>
                      <SquareScissors size={16} />Вырезать со страницы…</button>}
                  {editing && !removedSet.has(row.id) && (
                    <span className="drag-handle" title="Перетащите карточку">
                      <GripVertical size={16} />
                    </span>
                  )}
                </div>
                <div className="asset-info">
                  {!repeatsLabel(row.label, row.title) && <span className="asset-group" title={row.label}>
                    {row.label}
                  </span>}
                  <div className="asset-title">
                    <button
                      tabIndex={-1}
                      onClick={() => showPreview(row.id)}
                      title={row.title}
                    >
                      {row.title}
                    </button>
                    {editing && row.variant === null && !removedSet.has(row.id) && (
                      <button
                        className="icon-btn"
                        tabIndex={-1}
                        aria-label={`Переименовать PDF ${row.id + 1}`}
                        title={dirty ? "Сначала сохраните или сбросьте правки" : saving ? "Дождитесь завершения сохранения" : "Переименовать PDF · F2"}
                        disabled={dirty || saving}
                        onClick={() => renameFile(row.id)}
                      >
                        <Pencil size={12} />
                      </button>
                    )}
                  </div>
                  <div className="asset-meta">
                    <span>{row.image.copy_of !== undefined && <span className="copy-label"><Copy size={12} />Копия · </span>}Страница {row.image.page}</span>
                    <span>{row.image.size?.join(" × ")}</span>
                  </div>
                  {removedSet.has(row.id) && <button className="btn compact secondary restore-image" disabled={saving}
                    onClick={() => { setEditing(true); change({ type: "restore", ids: [row.id] }); }}>
                    <RotateCcw size={16} />Вернуть изображение
                  </button>}
                </div>
              </article>
            )))}
          </div>
        ) : (
          <Empty
            icon={reviewComplete ? <Check size={32} /> : <Images size={32} />}
            title={reviewComplete ? dirty ? "Спорные изображения распределены в черновике" : "Спорные изображения распределены"
              : filter === "removed" && !query.trim() ? "Убранных изображений нет"
              : filter === "service" && !query.trim() ? "Служебных элементов нет" : "Изображения не найдены"}
            action={
              reviewComplete && dirty ? <button className="btn primary" disabled={saving} onClick={() => void save()}>
                {saving ? "Сохраняю…" : "Сохранить распределение"}
              </button> :
              <button
                className="btn secondary"
                onClick={() => {
                  setFilter("all");
                  setStatus("all");
                  setQuery("");
                }}
              >
                Показать все изображения
              </button>
            }
          >
            {reviewComplete ? dirty ? "Сохраните правки, чтобы записать распределение в готовые PDF." : "Правки сохранены. Можно перейти к просмотру всех изображений."
              : filter === "removed" ? "Убранные карточки появятся здесь. Их можно вернуть и после сохранения."
              : filter === "service" ? "Все автоматически пропущенные элементы уже возвращены в результат."
              : "Измените поиск или фильтр. Убранные карточки находятся в разделе «Убранные»."}
          </Empty>
        )}
        </>}
      </div>
      <Presence>{cutting && <CutEditor view={cutting} resultId={result.id} variants={variants} dotOf={dotOf}
        labelOf={(key) => variants.includes(key) ? variantLabel(labelled, key) : `Вариант ${key} (новый)`}
        onApply={(boxes, targets, trim, names) => applyCut(cutting, boxes, targets, trim, names)}
        onClose={() => setCutting(null)} onHelp={onHelp && (() => onHelp("collages"))} />}</Presence>
      <Presence>{cropping && <CropDialog title={cropping.view.title} src={cropping.src} size={cropping.view.size}
        box={cropping.box} busy={saving} onApply={(box) => void applyCrop(box)} onClose={() => setCropping(null)} />}</Presence>
      <Presence>{cutoutEditor && <CutoutEditor resultId={result.id} title={result.title} views={cutoutViews} start={cutoutEditor}
        pictureInfo={pictureInfo} isLive={liveCard} variants={variants} dotOf={dotOf}
        labelOf={(key) => variants.includes(key) ? variantLabel(labelled, key) : `Вариант ${key} (новый)`}
        onApply={applyCutouts} onClose={() => setCutoutEditor(null)} onHelp={onHelp && (() => onHelp("cutouts"))} />}</Presence>
      <Presence>{previewRow && (
        <Dialog
          wide
          className="viewer-preview-dialog"
          title={previewRow.title}
          onClose={() => setPreview(null)}
          subtitle={<div className="preview-source-meta">
            <span>Исходная страница {previewRow.image.page}</span><span aria-hidden="true">·</span>
            <span>{previewRow.image.size?.join(" × ")} px</span>
          </div>}
          headerActions={<div className="preview-header-actions">
            <button className="btn primary preview-open-pdf" aria-label="Открыть готовый PDF"
              disabled={removedSet.has(previewRow.id) || !!previewRow.image.removed}
              title={removedSet.has(previewRow.id) ? "Верните изображение и сохраните результат, чтобы открыть PDF" : previewRow.image.removed ? "Сохраните восстановление, чтобы открыть PDF" : "Открыть сохранённый PDF результата"}
              onClick={() => open(previewRow.image.file)}>
              <FileText size={16} /><span>Открыть готовый PDF</span><ArrowUpRight size={16} />
            </button>
            <span className="preview-action-separator" aria-hidden="true" />
          </div>}
        >
          <div className="preview-taskbar">
            <div className="preview-view-switch" role="group" aria-label="Что показать">
              <button className="btn compact secondary" aria-pressed={!sourcePage} data-autofocus
                onClick={() => setSourcePage(false)}><Images size={16} />Извлечённое изображение</button>
              <button className="btn compact secondary" aria-pressed={sourcePage}
                onClick={() => { if (!sourcePage) { setSourcePreview({ key: "" }); setSourcePage(true); } }}><Scan size={16} />Исходная страница {previewRow.image.page}</button>
            </div>
            <div className="preview-image-actions" aria-label="Изображение">
              <ActionMenu label="Повернуть" icon={<RotateCw size={16} />} items={TURN_ITEMS}
                disabled={saving || dirty || removedSet.has(previewRow.id) || !!previewRow.image.removed}
                onAction={(value) => void turnImages([previewRow.id], Number(value.slice(5)) as Turn)} />
              <button className="btn compact secondary" disabled={saving || dirty || removedSet.has(previewRow.id) || !!previewRow.image.removed}
                title={dirty ? "Обрезка сразу записывается в PDF: сначала сохраните правки" : "Обрезать изображение рамкой на месте"}
                onClick={() => { const id = previewRow.id; setPreview(null); cropImage(id); }}><Crop size={16} />Обрезать…</button>
            </div>
            {editing && <div className="preview-edit-actions" aria-label="Правки открытого изображения">
              <ActionMenu label="Перенести в вариант" disabled={saving}
                items={[...variants.map((key) => ({ value: key, label: variantLabel(labelled, key) })),
                  { value: "return", label: "Вернуть на прежнее место", separator: true,
                    disabled: previewRow.variant === null || removedSet.has(previewRow.id) }]}
                onAction={(target) => target === "return"
                  ? change({ type: "return-to-misc", ids: [previewRow.id] }) : move(target, [previewRow.id])} />
              <button className="btn compact secondary" disabled={saving || removedSet.has(previewRow.id)}
                onClick={() => cutImage(previewRow.id)}><Scissors size={16} />Разрезать…</button>
              <button className="btn compact secondary" disabled={saving || removedSet.has(previewRow.id)}
                onClick={() => { const id = previewRow.id; setPreview(null); startCutout(id); }}><SquareScissors size={16} />Вырезать со страницы…</button>
              <button className="btn compact secondary" disabled={saving}
                aria-label={removedSet.has(previewRow.id) ? "Вернуть изображение" : "Убрать изображение"}
                onClick={() => change({ type: removedSet.has(previewRow.id) ? "restore" : "remove", ids: [previewRow.id] })}>
                {removedSet.has(previewRow.id) ? <RotateCcw size={16} /> : <Trash2 size={16} />}
                {removedSet.has(previewRow.id) ? "Вернуть" : "Убрать"}
              </button>
            </div>}
          </div>
          <div className="preview-stage">
            <button
              className="icon-btn preview-page-button preview-previous"
              aria-label="Предыдущее изображение"
              title="Предыдущее изображение · ←"
              disabled={previewOrder.indexOf(previewRow.id) <= 0}
              onClick={() => stepPreview(-1)}
            >
              <ChevronLeft size={20} />
            </button>
            <div className={`preview-image-scroll${zoom > 1 ? " is-zoomed" : ""}`} ref={previewViewport}
              title="Колесо мыши — масштаб; двойной щелчок — увеличить или вместить"
              onDoubleClick={() => setZoom((value) => value === 1 ? 2 : 1)}
              onContextMenu={(event) => openPreviewMenu(event, previewRow.id)}
              onPointerDown={(event) => {
                if (zoom <= 1 || event.button !== 0) return;
                const element = event.currentTarget;
                previewPan.current = { x: event.clientX, y: event.clientY, left: element.scrollLeft, top: element.scrollTop };
                element.setPointerCapture(event.pointerId);
              }}
              onPointerMove={(event) => {
                const pan = previewPan.current;
                if (!pan) return;
                event.currentTarget.scrollLeft = pan.left - event.clientX + pan.x;
                event.currentTarget.scrollTop = pan.top - event.clientY + pan.y;
              }}
              onPointerUp={() => { previewPan.current = null; }}
              onPointerCancel={() => { previewPan.current = null; }}
              onLostPointerCapture={() => { previewPan.current = null; }}>
              <div className="preview-image-canvas" style={{
                width: Math.max(previewBounds.width, previewWidth), height: Math.max(previewBounds.height, previewHeight),
              }}>
                {sourcePage ? source?.url ? <img key={previewKey} draggable={false}
                  data-preview-kind="source" src={source.url} alt={`Исходная страница ${previewRow.image.page}`}
                  style={{ width: previewWidth, height: previewHeight }} onLoad={previewLoaded} />
                  : <div className="source-preview-state" role="status">
                    {source?.error ? <><ImageOff size={32} /><strong>Исходный PDF недоступен</strong><p>{source.error}</p></>
                      : <><Scan size={32} /><span>Открываю исходную страницу…</span></>}
                  </div>
                  : previewRow.id in draft.frames && (collageOf(previewRow.id)?.preview || cutoutOf(previewRow.id)?.view.preview) ? <span className="preview-frame"
                    style={{ width: previewWidth, height: previewHeight }}>
                    <CroppedImage src={(collageOf(previewRow.id)?.preview || cutoutOf(previewRow.id)?.view.preview)!}
                      size={collageOf(previewRow.id)?.size ?? cutoutOf(previewRow.id)!.view.size}
                      box={draft.frames[previewRow.id].box} alt={previewRow.title} /></span>
                  : !previewFailed ? <img key={previewKey} draggable={false}
                  src={new URL(`api/results/${encodeURIComponent(result.id)}/preview/${sourceId(draft, previewRow.id)}?revision=${report.revision}`, document.baseURI).href}
                  alt={previewRow.title} style={{ width: previewWidth, height: previewHeight }}
                  onLoad={previewLoaded} onError={() => setPreviewFailed(true)} />
                  : previewRow.image.preview ? <img draggable={false}
                    src={fileUrl(result.base_url, previewRow.image.preview)} alt={previewRow.title}
                    style={{ width: previewWidth, height: previewHeight }} onLoad={previewLoaded} />
                  : <span className="no-preview"><ImageOff size={48} />Не удалось загрузить изображение</span>}
              </div>
            </div>
            <button
              className="icon-btn preview-page-button preview-next"
              aria-label="Следующее изображение"
              title="Следующее изображение · →"
              disabled={
                previewOrder.indexOf(previewRow.id) >= previewOrder.length - 1
              }
              onClick={() => stepPreview(1)}
            >
              <ChevronRight size={20} />
            </button>
            <div className="preview-zoom-tools" aria-label="Масштаб изображения">
              <button className="icon-btn" aria-label="Уменьшить" title="Уменьшить масштаб"
                disabled={zoom <= 0.25} onClick={() => setZoom((value) => Math.max(0.25, value - 0.5))}><Minus size={18} /></button>
              <span className="preview-scale" title="Масштаб относительно размера по окну" aria-live="polite">{Math.round(zoom * 100)}%</span>
              <button className="icon-btn" aria-label="Увеличить" title="Увеличить масштаб"
                disabled={zoom >= 5} onClick={() => setZoom((value) => Math.min(5, value + 0.5))}><Plus size={18} /></button>
              <span className="preview-zoom-separator" aria-hidden="true" />
              <button className="icon-btn" aria-label="Вместить изображение" title="Вместить изображение целиком в окно"
                onClick={() => { setZoom(1); previewViewport.current?.scrollTo(0, 0); }}><Scan size={18} /></button>
            </div>
          </div>
          <div className="preview-footer">
            <Info size={16} className="preview-info-icon" aria-hidden="true" />
            <div className="preview-explanation">
              <strong>{previewDetailHeading}</strong>
              <p>{previewDetail}</p>
              {previewFailed && <p className="preview-fallback" role="status">{previewRow.image.preview
                ? "Полноразмерный просмотр недоступен. Показана миниатюра; детали можно проверить в PDF."
                : "Полноразмерный просмотр недоступен. Проверьте изображение в PDF."}</p>}
              <span className="preview-shortcuts">← → листать{editing && " · 1–9 перенести в существующий вариант · 0 оставить как есть"}</span>
            </div>
            <span className={`preview-location${removedSet.has(previewRow.id) ? " is-removed" : ""}`}
              title={removedSet.has(previewRow.id) ? isService(previewRow.id) ? "Пропущено автоматически" : "Убрано из результата" : previewRow.label}>
              {removedSet.has(previewRow.id) ? isService(previewRow.id) ? <Shapes size={16} /> : <Trash2 size={16} />
                : previewRow.variant !== null ? <Layers2 size={16} /> : <Tag size={16} />}
              <span>{removedSet.has(previewRow.id) ? isService(previewRow.id) ? "Служебный" : "Убрано" : previewRow.label}</span>
            </span>
          </div>
        </Dialog>
      )}</Presence>
      <Presence>{copying && <Dialog title={copying.whole ? "Создать копию PDF варианта" : "Создать копии выбранных"}
        onClose={() => setCopying(null)}>
        <form onSubmit={createCopies}>
          <p>{copying.whole ? "Страниц в PDF" : "Выбрано карточек"}: {copying.ids.length}. Оригиналы останутся на месте.</p>
          <span className="form-label">Куда поместить копии</span>
          <Select className="copy-destination" label="Куда поместить копии" value={copyTarget} onChange={setCopyTarget}
            options={[
              ...(!copying.whole ? [{ value: "same", label: "Рядом с оригиналами" }] : []),
              { value: "new", label: "В новый вариант" },
              ...variants.map((key) => ({ value: key, label: variantLabel(labelled, key), dot: dotOf(key) })),
            ]} />
          {copyTarget === "new" && <div className="copy-variant-number">
            <label htmlFor="copy-variant-number" className="form-label">Номер или название нового варианта</label>
            <input id="copy-variant-number" data-autofocus className="form-input" type="text"
              autoComplete="off" required value={number} onChange={(event) => setNumber(event.target.value)} />
            <VariantSuggestions variants={variants} current={number} onPick={setNumber}
              names={namedSuggestions(Object.values(variantLabels))} labelOf={(key) => variantLabels[key] ?? `Вариант ${key}`}
              preferred={copying.ids.map((id) => draftVariant(draft, id)).find((key) => key !== null) ?? null} />
          </div>}
          <p>Копии появятся в результате после сохранения. До этого создание можно отменить через Ctrl+Z.</p>
          <div className="dialog-choices">
            <button className="btn primary"><Copy size={16} />Создать копии</button>
            <div className="dialog-alternatives">
              <button type="button" className="btn secondary" onClick={() => setCopying(null)}>Отмена</button>
            </div>
          </div>
        </form>
      </Dialog>}</Presence>
      <Presence>{rename && (
        <Dialog
          title="Переименовать"
          className="rename-dialog"
          busy={saving}
          onClose={() => setRename(null)}
        >
          <form onSubmit={(e) => void renameItem(e)}>
            <label className="form-label" htmlFor="rename-input">
              Новое имя
            </label>
            <textarea
              ref={renameField}
              data-autofocus
              id="rename-input"
              className="form-input rename-input"
              rows={3}
              value={rename.name}
              maxLength={120}
              spellCheck={false}
              disabled={saving}
              onChange={(e) => setRename({ ...rename, name: e.target.value.replace(/[\r\n]+/g, " ") })}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  if (!saving && rename.name.trim()) e.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <p className="muted">
              Имя папки и связанного PDF обновится в результате.
            </p>
            <div className="dialog-choices">
              <button className="btn primary" disabled={saving || !rename.name.trim()}>
                <Pencil size={16} />
                Переименовать
              </button>
              <div className="dialog-alternatives">
                <button type="button" className="btn secondary" disabled={saving} onClick={() => setRename(null)}>
                  Отмена
                </button>
              </div>
            </div>
          </form>
        </Dialog>
      )}</Presence>
      {context.menu}
      <Presence>{newVariant && <NewVariantDialog title="Перенести в новый вариант" action="Создать и перенести"
        variants={variants} labelOf={(key) => variantLabel(labelled, key)}
        preferred={newVariant.filter((id) => !removedSet.has(id)).map((id) => draftVariant(draft, id)).find((key) => key !== null)
          ?? (filter.startsWith("variant:") ? filter.slice(8) : null)}
        subject={`Выбранных изображений: ${newVariant.length}.`} onClose={() => setNewVariant(null)}
        onCreate={(key, name) => {
          change({ type: "move", ids: newVariant, target: key, name });
          setFilter(`variant:${key}`);
          setStatus("all");
          setNewVariant(null);
        }} />}</Presence>
      <Presence>{reset && (
        <Dialog
          title={
            conflict
              ? "Загрузить актуальный результат?"
              : finishEditing.current ? "Завершить правки?" : "Сбросить несохранённые правки?"
          }
          busy={saving}
          onClose={() => {
            finishEditing.current = false;
            setReset(false);
          }}
        >
          <p>
            {finishEditing.current && !conflict
              ? "Сохраните изменения в PDF или завершите редактирование без сохранения."
              : "Несохранённые перемещения, удаления и порядок будут отменены. Сохранённые файлы останутся на месте."}
          </p>
          {(() => {
            const back = <button className="btn secondary" disabled={saving}
              onClick={() => { finishEditing.current = false; setReset(false); }}>Вернуться к правкам</button>;
            const discard = (className: string) => <button className={className} disabled={saving}
              onClick={() => {
                setReset(false);
                if (conflict) {
                  finishEditing.current = false;
                  setSaving(true);
                  void request<Result>(`results/${result.id}`)
                    .then(onResult)
                    .catch((error) => notify(errorText(error), true))
                    .finally(() => setSaving(false));
                } else {
                  setDraft(initial);
                  setSelected([]);
                  if (finishEditing.current) {
                    setEditing(false);
                    finishEditing.current = false;
                  }
                }
              }}>
              {conflict ? "Загрузить" : finishEditing.current ? "Выйти без сохранения" : "Сбросить правки"}
            </button>;
            // Finishing with unsaved edits offers saving first, as the dialog on leaving the result does.
            return finishEditing.current && !conflict ? <div className="dialog-choices">
              <button className="btn primary" disabled={saving}
                onClick={() => void save().then((saved) => {
                  if (!saved) return;
                  finishEditing.current = false;
                  setReset(false);
                  setEditing(false);
                  setSelected([]);
                })}>
                {saving && <LoaderCircle size={16} className="spin" />}{saving ? "Сохраняю…" : "Сохранить и выйти"}
              </button>
              <div className="dialog-alternatives">{back}{discard("btn danger-text")}</div>
            </div> : <div className="dialog-choices">
              {discard(conflict ? "btn primary" : "btn danger")}
              <div className="dialog-alternatives">{back}</div>
            </div>;
          })()}
        </Dialog>
      )}</Presence>
      <Presence>{exportAsk && (
        <Dialog title={exportAsk.dirty ? "Сохранить правки перед выгрузкой?" : "Остались неразобранные изображения"}
          busy={saving} onClose={() => setExportAsk(null)}>
          {exportAsk.dirty && <p>В папку попадают сохранённые PDF вариантов: несохранённые правки в них ещё не вошли.</p>}
          {exportAsk.open > 0 && <p>{exportAsk.open} {plural(exportAsk.open, ["изображение", "изображения", "изображений"])} ещё
            «На проверку» или «Без подписи» — в PDF вариантов {exportAsk.open === 1 ? "оно не попадёт" : "они не попадут"}.</p>}
          <div className="dialog-choices">
            <button className="btn primary" data-autofocus disabled={saving} onClick={() => {
              const ask = exportAsk;
              setExportAsk(null);
              void (async () => {
                if (ask.dirty && !(await save())) return;
                await exportVariants(ask.keys, true);
              })();
            }}>{exportAsk.dirty ? "Сохранить и выгрузить" : "Выгрузить как есть"}</button>
            <div className="dialog-alternatives">
              {exportAsk.open > 0 && <button className="btn secondary" disabled={saving} onClick={() => {
                setExportAsk(null);
                setFilter("needs-review"); setStatus("all"); setQuery("");
                if (!editing) { setEditing(true); setSelected([]); }
              }}>Сначала разобрать</button>}
              <button className="btn quiet-text" disabled={saving} onClick={() => setExportAsk(null)}>Отмена</button>
            </div>
          </div>
        </Dialog>
      )}</Presence>
      <Presence>{confirmUndoSave && (
        <Dialog title="Отменить последнее сохранение?" busy={saving} onClose={() => setConfirmUndoSave(false)}>
          <p>Готовые PDF и отчёт вернутся к состоянию до последнего «Сохранить». Правки этого сохранения пропадут, повторить их можно будет только вручную.</p>
          <div className="dialog-choices">
            <button className="btn danger" disabled={saving}
              onClick={() => { setConfirmUndoSave(false); void undoSaved(result); }}>Отменить сохранение</button>
            <div className="dialog-alternatives">
              <button className="btn secondary" disabled={saving} onClick={() => setConfirmUndoSave(false)}>Оставить как есть</button>
            </div>
          </div>
        </Dialog>
      )}</Presence>
    </Shell>
  );
}
