import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, MouseEvent, ReactNode } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Copy,
  FileText,
  FolderOpen,
  GripVertical,
  ImageOff,
  Images,
  Info,
  Layers2,
  Layers3,
  Minus,
  Pencil,
  Plus,
  RotateCcw,
  Scan,
  Shapes,
  Search,
  Tag,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { ApiError, errorText, fileUrl, native, plural, request } from "./api";
import { ActionMenu, Dialog, Empty, Shell, StatusNote } from "./components";
import {
  createDraft,
  draftVariant,
  draftLocation,
  draftImage,
  folderLabel,
  editDraft,
  isDirty,
  orderedIds,
  payload,
  removedIds,
  selectRange,
  sourceId,
  variantLabel,
} from "./editor";
import type { Draft } from "./editor";
import { EditorToolbar } from "./EditorToolbar";
import type { NoticeAction, Result } from "./types";
import "./viewer-ux.css";
import "./preview-visual.css";

type Rename = {
  kind: "variant" | "folder" | "file";
  target: string | number;
  name: string;
};
type DropTarget = { kind: "variant"; key: string } | { kind: "card"; key: number };
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
function reportIssue(value: unknown, error: boolean) {
  const data = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const page = Number(data.page) || null;
  const image = Number(data.image) || null;
  const detail = typeof value === "string" ? value :
    [data.message, data.error, data.reason].find((text) => typeof text === "string") as string | undefined;
  const scan = /ocr|распознав|скан/i.test(detail || "");
  const message = error
    ? (detail || "Не удалось извлечь изображение. Проверьте исходную страницу PDF.")
    : scan
      ? "Не удалось распознать подпись на скане. Проверьте изображение вручную."
      : data.fallback
        ? `Изображение сохранено снимком области страницы. ${String(data.fallback)}`
        : (detail || "Проверьте изображение и его подпись вручную.");
  return { page, image, message, detail: scan && detail ? detail : undefined, error };
}
export function Viewer({
  result,
  footer,
  notify,
  onResult,
  onDirty,
  onBack,
  onSaveReady,
}: {
  result: Result;
  footer: ReactNode;
  notify(text: string, error?: boolean, action?: NoticeAction): void;
  onResult(result: Result): void;
  onDirty(dirty: boolean): void;
  onBack(): void;
  onSaveReady?(save: (() => Promise<boolean>) | null): void;
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
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [rename, setRename] = useState<Rename | null>(null);
  const [newVariant, setNewVariant] = useState(false);
  const [copying, setCopying] = useState<{ ids: number[]; whole: boolean } | null>(null);
  const [copyTarget, setCopyTarget] = useState("same");
  const [number, setNumber] = useState("1");
  const [preview, setPreview] = useState<number | null>(null);
  const [previewOrder, setPreviewOrder] = useState<number[]>([]);
  const [zoom, setZoom] = useState(1);
  const [previewBounds, setPreviewBounds] = useState({ width: 0, height: 0 });
  const [previewNatural, setPreviewNatural] = useState<{ key: string; width: number; height: number } | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [reset, setReset] = useState(false);
  const [dragged, setDragged] = useState<number[]>([]);
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);
  const finishEditing = useRef(false);
  const anchor = useRef<number | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const renameField = useRef<HTMLTextAreaElement>(null);
  const dragging = useRef<number[]>([]);
  const dragImage = useRef<HTMLDivElement | null>(null);
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
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; dragImage.current?.remove(); };
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
          : variantLabel(report, location.variant);
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
  // Service elements were set aside by the automatic filter, not by the user.
  const isService = (id: number) => !!report.images[id]?.suppressed;
  const removed = removedAll.filter((id) => !isService(id));
  const service = removedAll.filter(isService);
  const rows = orderedIds(draft).map((id) => allRows[id]);
  const removedRows = removed.map((id) => allRows[id]);
  const serviceRows = service.map((id) => allRows[id]);
  const showStatusTabs = filter === "all" || filter.startsWith("variant:");
  const issues = [...(report.errors || []).map((value) => reportIssue(value, true)),
    ...(report.warnings || []).map((value) => reportIssue(value, false))];
  const variants = useMemo(
    () => Object.keys(draft.variants).sort((a, b) => Number(a) - Number(b)),
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
      text.includes(query.trim().toLocaleLowerCase("ru"))
    );
  });
  if (!filter.startsWith("variant:")) visible.sort((a, b) =>
    a.image.page - b.image.page || a.image.sequence - b.image.sequence || a.id - b.id);
  const visibleIds = visible.map((row) => row.id);
  const visibleIdSet = new Set(visibleIds);
  const selectedIdSet = new Set(selected);
  const visibleSelected = visibleIds.filter((id) => selectedIdSet.has(id)).length;
  const counts = {
    named: rows.filter((row) => row.status === "named").length,
    review: rows.filter((row) => row.status === "review").length,
    unsigned: rows.filter((row) => row.status === "unsigned").length,
  };
  const activeTitle = filter.startsWith("variant:")
    ? variantLabel(report, filter.slice(8))
    : filter.startsWith("folder:")
      ? folderLabel(report, filter.slice(7))
      : filter === "needs-review"
        ? "Нужно проверить"
        : filter === "removed"
          ? "Убранные"
        : filter === "service"
          ? "Служебные элементы"
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
  function change(edit: Parameters<typeof editDraft>[1]) {
    try {
      setDraft((previous) => editDraft(previous, edit));
      setSelected([]);
    } catch (error) {
      notify(errorText(error), true);
    }
  }
  function move(target: string, ids = selected, before?: number) {
    if (ids.length) {
      change({ type: "move", ids, target, before });
    }
  }
  function reorder(direction: -1 | 1) {
    if (selected.length !== 1) return;
    const id = selected[0],
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
      notify("Последнее сохранение отменено. Предыдущие файлы восстановлены.");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      notify(errorText(error), true);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [notify, onResult]);
  const save = useCallback(async (): Promise<boolean> => {
    if (savingRef.current || savingState.current) return false;
    if (!dirty) return true;
    savingRef.current = true;
    setSaving(true);
    try {
      const value = await request<{ result: Result; message: string }>(
        `results/${result.id}/apply`,
        payload(draft, report.revision),
      );
      onResult(value.result);
      setSelected([]);
      onDirty(false);
      notify("Правки сохранены. Резервная копия создана.", false, {
        label: "Отменить сохранение", run: () => undoSaved(value.result),
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
  }, [dirty, draft, report.revision, result.id, onResult, onDirty, notify, undoSaved]);
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
      notify(value.message);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      notify(errorText(error), true);
    } finally {
      setSaving(false);
    }
  }
  function open(relative = "") {
    void native("open_result", result.id, relative).catch((error) =>
      notify(errorText(error), true),
    );
  }
  function startDrag(event: DragEvent, id: number) {
    const ids = selected.includes(id) ? selected : [id];
    dragging.current = ids;
    setDragged(ids);
    setDropTarget(null);
    setSelected(ids);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-pdf-photos", ids.join(","));
    dragImage.current?.remove();
    const badge = document.createElement("div");
    badge.className = "editor-drag-preview";
    badge.textContent = `${ids.length} ${plural(ids.length, ["изображение", "изображения", "изображений"])}`;
    badge.setAttribute("aria-hidden", "true");
    document.body.appendChild(badge);
    dragImage.current = badge;
    event.dataTransfer.setDragImage(badge, 18, 18);
  }
  function endDrag() {
    dragging.current = [];
    setDragged([]);
    setDropTarget(null);
    dragImage.current?.remove();
    dragImage.current = null;
  }
  function allowDrop(event: DragEvent, target?: DropTarget) {
    if (
      editing &&
      !saving &&
      event.dataTransfer.types.includes("application/x-pdf-photos")
    ) {
      if (target?.kind === "card" && dragging.current.includes(target.key)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      if (target) setDropTarget((previous) => previous?.kind === target.kind && previous.key === target.key ? previous : target);
    }
  }
  function leaveDrop(event: DragEvent, target: DropTarget) {
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.clientX >= rect.left && event.clientX < rect.right && event.clientY >= rect.top && event.clientY < rect.bottom) return;
    setDropTarget((previous) => previous?.kind === target.kind && previous.key === target.key ? null : previous);
  }
  function drop(event: DragEvent, target: string, before?: number) {
    if (
      !editing ||
      saving ||
      !event.dataTransfer.types.includes("application/x-pdf-photos")
    )
      return;
    event.preventDefault();
    event.stopPropagation();
    if (before !== undefined && dragging.current.includes(before)) { endDrag(); return; }
    move(target, dragging.current, before);
    endDrag();
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const input = textEntry(event.target);
      if (event.defaultPrevented || document.querySelector("dialog[open], [role=menu]")) return;
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyK") {
        event.preventDefault();
        search.current?.focus();
        return;
      }
      if (!editing || input || saving) return;
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyA") {
        event.preventDefault();
        setSelected((ids) => [...new Set([...ids, ...visibleIds])]);
      }
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyZ") {
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
  const pendingLocation = previewRow && previewRow.variant !== previewRow.image.variant;
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
    : previewRow?.image.reason || "Изображение извлечено из PDF. Подпись и расположение можно проверить в редакторе.";
  const previewKey = `${preview}:${report.revision}`;
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
  function showPreview(id: number) {
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
  }, [preview, report.revision]);
  useEffect(() => {
    if (preview === null) return;
    const viewport = previewViewport.current;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setZoom((value) => Math.max(0.25, Math.min(5, Math.round((value - event.deltaY * 0.003) * 100) / 100)));
    };
    const key = (event: KeyboardEvent) => {
      if (textEntry(event.target) || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const index = previewOrder.indexOf(preview);
        const id = previewOrder[index + (event.key === "ArrowLeft" ? -1 : 1)];
        if (id !== undefined) setPreview(id);
      } else if (editing && !saving && /^[1-9]$/.test(event.key) && variants.includes(event.key)) {
        event.preventDefault();
        change({ type: "move", ids: [preview], target: event.key });
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
  function startCopy(ids: number[], whole = false) {
    if (!ids.length || saving) return;
    setNumber(String(Array.from({ length: 999 }, (_, i) => i + 1).find((n) => !variants.includes(String(n))) ?? ""));
    setCopyTarget(whole ? "new" : "same");
    setCopying({ ids: [...ids], whole });
  }
  function createCopies(event: React.FormEvent) {
    event.preventDefault();
    if (!copying) return;
    if (copyTarget === "new" && variants.includes(String(Number(number)))) {
      notify("Такой вариант уже существует. Укажите свободный номер или выберите готовый вариант.", true);
      return;
    }
    try {
      const target = copyTarget === "new" ? number : copyTarget;
      const next = editDraft(draft, { type: "copy", ids: copying.ids, target });
      setDraft(next);
      setSelected(next.order.filter((id) => !draft.order.includes(id)));
      setCopying(null);
      if (target !== "same") { setFilter(`variant:${Number(target)}`); setStatus("all"); setQuery(""); }
    } catch (error) { notify(errorText(error), true); }
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
        className={`project-link ${filter === key ? "active" : ""}${dropTarget?.kind === "variant" && dropTarget.key === variant ? " is-drop-target" : ""}`}
        title={title}
        onClick={() => {
          setFilter(key);
          setStatus("all");
        }}
        onDragOver={variant ? (event) => allowDrop(event, { kind: "variant", key: variant }) : undefined}
        onDragLeave={variant ? (event) => leaveDrop(event, { kind: "variant", key: variant }) : undefined}
        onDrop={variant ? (event) => drop(event, variant) : undefined}
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
  const canMoveEdge = selectedActive.length > 0 && selectionVariants.size === 1 && !selectionVariants.has(null);
  const variantCount = variants.filter((key) => draft.variants[key].length).length;
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
          {sidebar("all", "Все изображения", rows.length, <Images size={17} />)}
          {sidebar("removed", "Убранные", removed.length, <Trash2 size={17} />)}
          {!!report.images.some((image) => image.suppressed || image.suppression_code) &&
            sidebar("service", "Служебные", service.length, <Shapes size={17} />)}
          <div className="nav-label spaced">Варианты</div>
          {variants.map((key, i) =>
            sidebar(
              `variant:${key}`,
              variantLabel(report, key),
              draft.variants[key].length,
              <span className={`variant-dot color-${i % 4}`} />,
              key,
            ),
          )}
          {!variants.length && (
            <p className="nav-empty">Варианты пока не определены</p>
          )}
          <div className="nav-label spaced">Статус</div>
          {sidebar(
            "review",
            "На проверку",
            counts.review,
            <Clock3 size={17} />,
          )}
          {sidebar(
            "unsigned",
            "Без подписи",
            counts.unsigned,
            <Tag size={17} />,
          )}
          {!!folders.length && (
            <>
              <div className="nav-label spaced">Подписи</div>
              <div className="category-list">
                {folders.map((folder) =>
                  sidebar(
                    `folder:${folder}`,
                    folderLabel(report, folder),
                    rows.filter(
                      (row) => row.folder === folder && row.variant === null,
                    ).length,
                    <Tag size={16} />,
                  ),
                )}
              </div>
            </>
          )}
        </>
      }
      header={
        <>
          <label className="searchbox">
            <Search size={17} />
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
            <StatusNote busy={saving} dirty={dirty}>
              {saving
                ? "Сохраняю…"
                : dirty
                  ? "Есть изменения"
                  : "Изменения сохранены"}
            </StatusNote>
            <button
              className={`btn ${editing ? "primary" : "secondary"}`}
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
            <p>
              {report.pages} {plural(report.pages, ["страница", "страницы", "страниц"])} PDF · {report.documents.length} {plural(report.documents.length, ["готовый", "готовых", "готовых"])} PDF
            </p>
          </div>
          <button className="btn secondary" onClick={() => open()}>
            <FolderOpen size={16} />
            <span>Папка результата</span>
          </button>
        </div>
        <div className="stats" aria-label="Итоги обработки">
          <button
            className={filter === "all" ? "selected" : ""}
            onClick={() => {
              setFilter("all");
              setStatus("all");
            }}
          >
            <span className="stat-icon blue">
              <Images size={20} />
            </span>
            <span>
              <strong>{rows.length}</strong>
              <small>{plural(rows.length, ["Изображение", "Изображения", "Изображений"])}</small>
            </span>
          </button>
          <div className="passive-stat">
            <span className="stat-icon violet">
              <FileText size={20} />
            </span>
            <span>
              <strong>{report.pages}</strong>
              <small>{plural(report.pages, ["Страница", "Страницы", "Страниц"])} PDF</small>
            </span>
          </div>
          <div className="passive-stat">
            <span className="stat-icon teal">
              <Layers3 size={20} />
            </span>
            <span>
              <strong>
                {variantCount}
              </strong>
              <small>{plural(variantCount, ["Вариант", "Варианта", "Вариантов"])}</small>
            </span>
          </div>
          <button
            className={filter === "review" ? "selected" : ""}
            onClick={() => {
              setFilter("review");
              setStatus("all");
            }}
          >
            <span className="stat-icon amber">
              <Clock3 size={20} />
            </span>
            <span>
              <strong>{counts.review}</strong>
              <small>На проверку</small>
            </span>
          </button>
          <button
            className={filter === "unsigned" ? "selected" : ""}
            onClick={() => {
              setFilter("unsigned");
              setStatus("all");
            }}
          >
            <span className="stat-icon rose">
              <Tag size={20} />
            </span>
            <span>
              <strong>{counts.unsigned}</strong>
              <small>Без подписи</small>
            </span>
          </button>
        </div>
        {!!report.suppressed?.length && <p className="suppressed-summary">
          Пропущено служебных элементов: {report.suppressed.length}
          {!!service.length && <> · <button className="text-btn" onClick={() => {
            setFilter("service"); setStatus("all"); setQuery("");
          }}>Посмотреть и вернуть</button></>}
        </p>}
        {!!(counts.review + counts.unsigned) && (
          <div className="review-banner">
            <span className="review-icon">
              <TriangleAlert size={20} />
            </span>
            <div>
              <strong>
                Нужно проверить: {counts.review + counts.unsigned}
              </strong>
              <span>
                Проверьте подписи и перенесите изображения в нужный вариант.
              </span>
            </div>
            <button
              onClick={() => {
                setFilter("needs-review");
                setStatus("all");
              }}
            >
              Начать проверку
              <ChevronRight size={16} />
            </button>
          </div>
        )}
        {!!(report.errors?.length || report.warnings?.length) && (
          <details className="report-notes" open={!!report.errors?.length}>
            <summary>
              <TriangleAlert size={15} />
              Предупреждения: {report.warnings?.length || 0} · Ошибки:{" "}
              {report.errors?.length || 0}
            </summary>
            <ul className="report-issue-list">
              {issues.map((issue, index) => <li key={index} className={issue.error ? "is-error" : ""}>
                <TriangleAlert size={17} aria-hidden="true" />
                <div><strong>{issue.page ? `Страница ${issue.page}` : issue.error ? "Ошибка обработки" : "Предупреждение"}</strong>
                  <p>{issue.message}</p>
                  {issue.detail && <details><summary>Подробности</summary><p>{issue.detail}</p></details>}
                </div>
                {issue.page && <button className="text-btn" onClick={() => revealIssue(issue.page!, issue.image)}>Показать страницу</button>}
              </li>)}
            </ul>
          </details>
        )}
        {editing && (
          <EditorToolbar
            selected={selected.length}
            visibleSelected={visibleSelected}
            visible={visible.length}
            variants={variants.map((key) => ({
              value: key, label: variantLabel(report, key),
            }))}
            dirty={dirty}
            saving={saving}
            canUndo={!!draft.past.length}
            canMoveUp={selected.length === 1 && selectedPosition > 0}
            canMoveDown={selected.length === 1 && selectedPosition >= 0 && selectedPosition < selectedMembers.length - 1}
            canMoveEdge={canMoveEdge}
            canReturn={selectedActive.some((id) => draftVariant(draft, id) !== null)}
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
            onMove={(target) => move(target)}
            onNewVariant={() => {
              setNumber(String(Math.max(0, ...variants.map(Number)) + 1));
              setNewVariant(true);
            }}
            onReorder={reorder}
            onMoveEdge={(edge) => change({ type: "move-edge", ids: selectedActive, edge })}
            onReturn={() => change({ type: "return-to-misc", ids: selectedActive })}
            onRestore={() => change({ type: "restore", ids: selectedRemoved })}
            onShowRemoved={() => { setFilter("removed"); setStatus("all"); setQuery(""); }}
            onUndoSave={() => void undoSaved(result)}
            onRemove={() => change({ type: "remove", ids: selectedActive })}
            onCopy={() => startCopy(selectedActive)}
            onUndo={() => change({ type: "undo" })}
            onReset={() => setReset(true)}
            onSave={() => void save()}
          />
        )}
        <div className="library-header">
          <div>
            <h2>{activeTitle}</h2>
            <span>
              {visible.length} из {filter === "removed" ? removed.length : filter === "service" ? service.length : rows.length}
            </span>
            {editing &&
              (filter.startsWith("variant:") ||
                filter.startsWith("folder:")) && (
                <button
                  className="icon-btn"
                  aria-label="Переименовать группу"
                  title={
                    dirty
                      ? "Сначала сохраните или сбросьте правки"
                      : "Переименовать группу"
                  }
                  disabled={dirty || saving}
                  onClick={renameGroup}
                >
                  <Pencil size={15} />
                </button>
              )}
          </div>
          {editing && filter.startsWith("variant:") && <button className="btn compact secondary"
            aria-label="Создать копию PDF варианта" title="Создать копию всего PDF варианта"
            disabled={saving || !draft.variants[filter.slice(8)]?.length}
            onClick={() => startCopy(draft.variants[filter.slice(8)] || [], true)}>
            <Copy size={15} /><span>Копия PDF</span>
          </button>}
          {showStatusTabs && <div
            className="filter-tabs"
            role="group"
            aria-label="Фильтр по статусу"
          >
            {[
              ["all", "Все изображения"],
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
        {filter === "service" && <div className="removed-explanation">
          <Shapes size={18} /><p>Эти элементы программа пропустила автоматически: логотипы, повторяющиеся значки, мелкие фрагменты и полностью закрытые картинки. Если среди них есть нужное изображение, верните его — оно появится в «Без подписи», после чего его можно перенести в вариант. Изменение применяется при сохранении.</p>
        </div>}
        {filter === "removed" && <div className="removed-explanation">
          <RotateCcw size={18} /><p>Здесь видны убранные изображения, включая сохранённые. Верните нужные карточки и сохраните результат.</p>
        </div>}
        {visible.length ? (
          <div className="asset-grid">
            {visible.map((row) => (
              <article
                key={row.id}
                data-id={row.id}
                className={`asset-card ${selected.includes(row.id) ? "selected-card" : ""}${removedSet.has(row.id) ? " removed-card" : ""}${dragged.includes(row.id) ? " is-dragging" : ""}${dropTarget?.kind === "card" && dropTarget.key === row.id ? " is-insertion-target" : ""}`}
                draggable={editing && !saving && !removedSet.has(row.id)}
                onDragStart={(e) => startDrag(e, row.id)}
                onDragEnd={endDrag}
                onDragOver={row.variant !== null ? (event) => allowDrop(event, { kind: "card", key: row.id }) : undefined}
                onDragLeave={row.variant !== null ? (event) => leaveDrop(event, { kind: "card", key: row.id }) : undefined}
                onDrop={
                  row.variant !== null
                    ? (e) => drop(e, String(row.variant), row.id)
                    : undefined
                }
              >
                <div className="asset-image">
                  <button
                    className="cover-button"
                    aria-label={`${editing ? "Выбрать" : "Просмотреть"}: ${row.title}, страница ${row.image.page}`}
                    disabled={saving}
                    onClick={(e) =>
                      editing ? select(row.id, e) : showPreview(row.id)
                    }
                    onDoubleClick={() => {
                      if (editing) showPreview(row.id);
                    }}
                  >
                    {row.image.preview ? (
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
                        <ImageOff size={30} />
                        Без миниатюры
                      </span>
                    )}
                  </button>
                  {editing && (
                    <label className="pick">
                      <input
                        type="checkbox"
                        aria-label={`Выбрать изображение ${row.id + 1}`}
                        checked={selected.includes(row.id)}
                        disabled={saving}
                        onChange={(e) => select(row.id, e)}
                      />
                      <span />
                    </label>
                  )}
                  <span className={`status-badge ${removedSet.has(row.id) ? "removed" : row.status}`}>
                    {removedSet.has(row.id) ? isService(row.id) ? <Shapes size={12} /> : <Trash2 size={12} /> : row.status === "named" ? (
                      <Check size={12} />
                    ) : row.status === "review" ? (
                      <Clock3 size={12} />
                    ) : (
                      <Tag size={12} />
                    )}
                    {removedSet.has(row.id) ? isService(row.id) ? "Служебный" : "Убрано" : STATUS[row.status as keyof typeof STATUS]}
                  </span>
                  {editing && !removedSet.has(row.id) && (
                    <span className="drag-handle" title="Перетащите карточку">
                      <GripVertical size={16} />
                    </span>
                  )}
                </div>
                <div className="asset-info">
                  {row.label.trim().toLocaleLowerCase("ru") !== row.title.trim().toLocaleLowerCase("ru") && <span className="asset-group" title={row.label}>
                    {row.label}
                  </span>}
                  <div className="asset-title">
                    <button
                      onClick={() => showPreview(row.id)}
                      title={row.title}
                    >
                      {row.title}
                    </button>
                    {editing && row.variant === null && !removedSet.has(row.id) && (
                      <button
                        className="icon-btn"
                        aria-label={`Переименовать PDF ${row.id + 1}`}
                        title={dirty ? "Сначала сохраните или сбросьте правки" : saving ? "Дождитесь завершения сохранения" : "Переименовать PDF"}
                        disabled={dirty || saving}
                        onClick={() =>
                          setRename({
                            kind: "file",
                            target: row.id,
                            name:
                              row.image.file
                                .replaceAll("\\", "/")
                                .split("/")
                                .at(-1)
                                ?.replace(/\.pdf$/i, "") || "",
                          })
                        }
                      >
                        <Pencil size={13} />
                      </button>
                    )}
                  </div>
                  <div className="asset-meta">
                    <span>{row.image.copy_of !== undefined && <span className="copy-label"><Copy size={11} />Копия · </span>}Страница {row.image.page}</span>
                    <span>{row.image.size?.join(" × ")}</span>
                  </div>
                  {removedSet.has(row.id) && <button className="btn compact secondary restore-image" disabled={saving}
                    onClick={() => { setEditing(true); change({ type: "restore", ids: [row.id] }); }}>
                    <RotateCcw size={15} />Вернуть изображение
                  </button>}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            icon={<Images size={32} />}
            title={filter === "removed" && !query.trim() ? "Убранных изображений нет"
              : filter === "service" && !query.trim() ? "Служебных элементов нет" : "Изображения не найдены"}
            action={
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
            {filter === "removed" ? "Убранные карточки появятся здесь. Их можно вернуть и после сохранения."
              : filter === "service" ? "Все автоматически пропущенные элементы уже возвращены в результат."
              : "Измените поиск или фильтр. Убранные карточки находятся в разделе «Убранные»."}
          </Empty>
        )}
      </div>
      {previewRow && (
        <Dialog
          wide
          className="viewer-preview-dialog"
          title={previewRow.title}
          onClose={() => setPreview(null)}
          subtitle={<div className="preview-source-meta">
            <span>Страница {previewRow.image.page}</span><span aria-hidden="true">·</span>
            <span>{previewRow.image.size?.join(" × ")} px</span>
          </div>}
          headerActions={<div className="preview-header-actions">
            <button className="btn primary preview-open-pdf" aria-label="Открыть PDF"
              disabled={removedSet.has(previewRow.id) || !!previewRow.image.removed}
              title={removedSet.has(previewRow.id) ? "Верните изображение и сохраните результат, чтобы открыть PDF" : previewRow.image.removed ? "Сохраните восстановление, чтобы открыть PDF" : "Открыть PDF"}
              onClick={() => open(previewRow.image.file)}>
              <FileText size={16} /><span>Открыть PDF</span><ArrowUpRight size={14} />
            </button>
            <span className="preview-action-separator" aria-hidden="true" />
          </div>}
        >
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
                {!previewFailed ? <img key={`${previewRow.id}:${report.revision}`} draggable={false}
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
              <span className="preview-shortcuts">← → листать{editing && " · 1–9 перенести в существующий вариант"}</span>
            </div>
            <span className={`preview-location${removedSet.has(previewRow.id) ? " is-removed" : ""}`}
              title={removedSet.has(previewRow.id) ? isService(previewRow.id) ? "Пропущено автоматически" : "Убрано из результата" : previewRow.label}>
              {removedSet.has(previewRow.id) ? isService(previewRow.id) ? <Shapes size={14} /> : <Trash2 size={14} />
                : previewRow.variant !== null ? <Layers2 size={14} /> : <Tag size={14} />}
              <span>{removedSet.has(previewRow.id) ? isService(previewRow.id) ? "Служебный" : "Убрано" : previewRow.label}</span>
            </span>
          </div>
        </Dialog>
      )}
      {copying && <Dialog title={copying.whole ? "Создать копию PDF варианта" : "Создать копии выбранных"}
        onClose={() => setCopying(null)}>
        <form onSubmit={createCopies}>
          <p>{copying.whole ? "Страниц в PDF" : "Выбрано карточек"}: {copying.ids.length}. Оригиналы останутся на месте.</p>
          <label className="form-label">Куда поместить копии</label>
          <ActionMenu className="copy-destination" label={copyTarget === "same" ? "Рядом с оригиналами" : copyTarget === "new" ? "В новый вариант" : variantLabel(report, copyTarget)}
            items={[
              ...(!copying.whole ? [{ value: "same", label: "Рядом с оригиналами" }] : []),
              { value: "new", label: "В новый вариант" },
              ...variants.map((key) => ({ value: key, label: variantLabel(report, key) })),
            ]} onAction={setCopyTarget} />
          {copyTarget === "new" && <div className="copy-variant-number">
            <label htmlFor="copy-variant-number" className="form-label">Номер нового варианта</label>
            <input id="copy-variant-number" autoFocus className="form-input" type="number" min="1" max="999" required
              value={number} onChange={(event) => setNumber(event.target.value)} />
          </div>}
          <p>Копии появятся в результате после сохранения. До этого создание можно отменить через Ctrl+Z.</p>
          <div className="dialog-actions">
            <button type="button" className="btn secondary" onClick={() => setCopying(null)}>Отмена</button>
            <button className="btn primary"><Copy size={16} />Создать копии</button>
          </div>
        </form>
      </Dialog>}
      {rename && (
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
              autoFocus
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
            <div className="dialog-actions">
              <button
                type="button"
                className="btn secondary"
                disabled={saving}
                onClick={() => setRename(null)}
              >
                Отмена
              </button>
              <button
                className="btn primary"
                disabled={saving || !rename.name.trim()}
              >
                <Pencil size={16} />
                Переименовать
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {newVariant && (
        <Dialog
          title="Перенести в новый вариант"
          onClose={() => setNewVariant(false)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!/^\d+$/.test(number) || +number < 1 || +number > 999) {
                notify("Номер варианта должен быть от 1 до 999.", true);
                return;
              }
              if (variants.includes(String(Number(number)))) {
                notify(
                  "Такой вариант уже существует. Выберите его в списке переноса.",
                  true,
                );
                return;
              }
              move(number);
              setFilter(`variant:${Number(number)}`);
              setStatus("all");
              setNewVariant(false);
            }}
          >
            <label htmlFor="variant-number" className="form-label">
              Номер варианта
            </label>
            <input
              autoFocus
              id="variant-number"
              className="form-input"
              type="number"
              min="1"
              max="999"
              required
              value={number}
              onChange={(e) => setNumber(e.target.value)}
            />
            <p className="muted">
              Выбранных изображений: {selected.length}. PDF будет создан после
              сохранения.
            </p>
            <div className="dialog-actions">
              <button
                type="button"
                className="btn secondary"
                onClick={() => setNewVariant(false)}
              >
                Отмена
              </button>
              <button className="btn primary">
                <Plus size={16} />
                Создать и перенести
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {reset && (
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
          <div className="dialog-actions">
            <button
              className="btn secondary"
              disabled={saving}
              onClick={() => {
                finishEditing.current = false;
                setReset(false);
              }}
            >
              Вернуться
            </button>
            <button
              className="btn danger"
              disabled={saving}
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
              }}
            >
              {conflict ? "Загрузить" : finishEditing.current ? "Выйти без сохранения" : "Сбросить правки"}
            </button>
            {finishEditing.current && !conflict && <button className="btn primary" disabled={saving}
              onClick={() => void save().then((saved) => {
                if (!saved) return;
                finishEditing.current = false;
                setReset(false);
                setEditing(false);
                setSelected([]);
              })}>
              {saving ? "Сохраняю…" : "Сохранить и выйти"}
            </button>}
          </div>
        </Dialog>
      )}
    </Shell>
  );
}
