import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { Check, Columns3, Eye, EyeOff, Layers, Minus, Pencil, Plus, Rows3, Scissors, SeparatorHorizontal,
  SeparatorVertical, Sparkles, Trash2, TriangleAlert, Undo2 } from "lucide-react";
import { errorText, plural, request } from "./api";
import { Dialog } from "./components";
import type { Box } from "./editor";
import { compareVariants, nextSubvariant } from "./editor";
import type { Collage } from "./types";
import "./collages.css";

/** A collage as the section and the editor show it: saved state merged with the draft. */
export interface CollageView {
  key: string;
  collage: string | null;
  source: number | null;
  page: number;
  title: string;
  size: [number, number];
  preview: string | null;
  state: Collage["state"];
  checked: boolean;
  pending: boolean;
  boxes: Box[];
  suggestion: Box[];
  /** Saved cards that belong to the collage now: its frames, or the whole picture. */
  members: number[];
  /** Where each live frame sits now, so a re-cut keeps it there. */
  frames: { box: Box; variant: string | null }[];
  /** Trimming of a cut not saved yet: its frames are trimmed only on saving. */
  trim?: boolean;
  variant: string | null;
  variantLabel: string | null;
  reason?: string;
}

export function needsCheck(view: CollageView): boolean {
  return !view.checked && (view.state === "suspected" || view.state === "auto");
}

/** Part of a picture by its box in source pixels; the percentages do not depend on the preview size. */
export function CroppedImage({ src, size, box, alt, className = "" }: {
  src: string; size: [number, number]; box: Box; alt: string; className?: string;
}) {
  const [left, top, right, bottom] = box;
  const width = right - left, height = bottom - top;
  return (
    <span className={`cropped-image ${className}`} style={{ aspectRatio: `${width} / ${height}` }}>
      <img src={src} alt={alt} draggable={false} style={{
        width: `${(size[0] / width) * 100}%`, height: `${(size[1] / height) * 100}%`,
        left: `${(-left / width) * 100}%`, top: `${(-top / height) * 100}%`,
      }} />
    </span>
  );
}

/** The whole picture, never cropped, with its frames drawn over it. */
function CollagePicture({ view, maxHeight }: { view: CollageView; maxHeight: number }) {
  const [width, height] = view.size;
  const frames = view.boxes.length ? view.boxes : view.suggestion;
  const suggested = !view.boxes.length;
  const style: CSSProperties = { aspectRatio: `${width} / ${height}`,
    width: `min(100%, ${Math.round(maxHeight * width / height)}px)` };
  return (
    <span className="collage-picture" style={style}>
      {view.preview ? <img src={view.preview} alt={view.title} draggable={false} loading="lazy" /> : <span className="no-preview" />}
      {frames.map((box, index) => (
        <span key={index} className={`collage-frame${suggested ? " is-suggested" : ""}`} style={{
          left: `${(box[0] / width) * 100}%`, top: `${(box[1] / height) * 100}%`,
          width: `${((box[2] - box[0]) / width) * 100}%`, height: `${((box[3] - box[1]) / height) * 100}%`,
        }}>
          <b>{index + 1}</b>
        </span>
      ))}
    </span>
  );
}

function status(view: CollageView): { tone: string; icon: ReactNode; label: string } {
  const count = view.boxes.length;
  const frames = `${count} ${plural(count, ["кадр", "кадра", "кадров"])}`;
  if (view.state === "suspected") return { tone: "amber", icon: <TriangleAlert size={14} />, label: "Возможно, склейка" };
  if (view.state === "whole") return { tone: "neutral", icon: <Layers size={14} />, label: "Целиком" };
  if (view.pending) return { tone: "blue", icon: <Scissors size={14} />, label: `Разрезано · ${frames} · не сохранено` };
  if (view.state === "auto" && !view.checked) return { tone: "blue", icon: <Scissors size={14} />, label: `Разрезано автоматически · ${frames}` };
  if (view.state === "auto") return { tone: "green", icon: <Check size={14} />, label: `Проверено · ${frames}` };
  return { tone: "green", icon: <Scissors size={14} />, label: `Разрезано вручную · ${frames}` };
}

export function CollagesPage({ views, saving, onCut, onCheck, onDismiss, onWhole }: {
  views: CollageView[];
  saving: boolean;
  onCut(view: CollageView): void;
  onCheck(view: CollageView): void;
  onDismiss(view: CollageView): void;
  onWhole(view: CollageView): void;
}) {
  // «Все» by default: a collage handled here must not vanish from under the pointer.
  const [tab, setTab] = useState<"all" | "check" | "done">("all");
  const shown = views.filter((view) => tab === "all" || (tab === "check") === needsCheck(view));
  const tabs: [typeof tab, string, number][] = [["all", "Все", views.length],
    ["check", "Нужно проверить", views.filter(needsCheck).length], ["done", "Готовы", views.filter((view) => !needsCheck(view)).length]];
  return (
    <section className="collages-page" aria-label="Склейки">
      <div className="collages-heading">
        <p>Картинки, в которых склеено несколько рендеров. Программа режет их по светлым полосам между кадрами;
          проверьте результат или разрежьте вручную то, что она оставила целым.</p>
        <div className="collages-tabs" role="group" aria-label="Фильтр склеек">
          {tabs.map(([key, label, count]) => (
            <button key={key} className={tab === key ? "active" : ""} aria-pressed={tab === key} onClick={() => setTab(key)}>
              {label}<b>{count}</b>
            </button>
          ))}
        </div>
      </div>
      {shown.length ? (
        <div className="collages-grid">
          {shown.map((view) => {
            const chip = status(view);
            const check = needsCheck(view);
            const variantless = view.state === "auto" && view.members.length > 0 && view.variant === null;
            return (
              <article key={view.key} className={`collage-card${check ? " needs-check" : ""}`} aria-label={`Склейка: ${view.title}`}>
                <button className="collage-stage" disabled={saving} onClick={() => onCut(view)}
                  aria-label={`Открыть разрезку: ${view.title}, страница ${view.page}`}>
                  <CollagePicture view={view} maxHeight={220} />
                </button>
                <div className="collage-body">
                  <div className="collage-chips">
                    <span className={`collage-chip ${chip.tone}`}>{chip.icon}{chip.label}</span>
                    {variantless && <span className="collage-chip amber"><TriangleAlert size={14} />Вариант не определён</span>}
                    <span className="collage-chip neutral">Стр. {view.page}{view.variantLabel ? ` · ${view.variantLabel}` : ""}</span>
                  </div>
                  <h3 title={view.title}>{view.title}</h3>
                  {view.reason && <p>{view.reason}</p>}
                </div>
                <div className="collage-actions">
                  {view.state === "suspected" || view.state === "whole" ? <>
                    <button className="btn primary" disabled={saving} onClick={() => onCut(view)}><Scissors size={16} />Разрезать…</button>
                    {view.state === "suspected" && <button className="btn text-action" disabled={saving}
                      onClick={() => onDismiss(view)}><Check size={16} />Это одно фото</button>}
                  </> : <>
                    <button className="btn secondary" disabled={saving} onClick={() => onCut(view)}>
                      <Pencil size={16} />{check ? "Проверить разрезку" : "Изменить разрезку"}</button>
                    {check ? <button className="btn text-action" disabled={saving} onClick={() => onCheck(view)}><Check size={16} />Всё верно</button>
                      : <button className="btn text-action" disabled={saving} onClick={() => onWhole(view)}><Undo2 size={16} />Вернуть целиком</button>}
                  </>}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <p className="collages-empty">{tab === "check" ? "Проверять больше нечего." : "Склеек нет."}</p>
      )}
    </section>
  );
}

/** Vertical and horizontal cut lines through the whole picture; frames are the cells between them. */
export interface Lines { xs: number[]; ys: number[] }

function axisLines(boxes: Box[], start: 0 | 1, size: number): number[] {
  const ranges = boxes.map((box) => [box[start], box[start + 2]]).sort((a, b) => a[0] - b[0]);
  const lines = new Set<number>();
  let reach = -1;
  for (const [from, to] of ranges) {
    // A gap between neighbours puts the line in its middle; touching boxes put it on the joint.
    if (reach >= 0 && from >= reach - 2) lines.add(Math.round(from > reach ? (reach + from) / 2 : from));
    reach = Math.max(reach, to);
  }
  return [...lines].filter((value) => value > 0 && value < size).sort((a, b) => a - b);
}

export function linesFromBoxes(boxes: Box[], size: [number, number]): Lines {
  return { xs: axisLines(boxes, 0, size[0]), ys: axisLines(boxes, 1, size[1]) };
}

export function cellsFromLines(lines: Lines, size: [number, number]): Box[] {
  const xs = [0, ...lines.xs, size[0]], ys = [0, ...lines.ys, size[1]];
  const cells: Box[] = [];
  for (let row = 0; row < ys.length - 1; row++)
    for (let column = 0; column < xs.length - 1; column++)
      cells.push([xs[column], ys[row], xs[column + 1], ys[row + 1]]);
  return cells;
}

const MIN_FRAME = 16;
const SNAP = 8;
type Axis = "x" | "y";
type Target = string | null | "keep";

function evenLines(size: number, count: number): number[] {
  return Array.from({ length: Math.max(0, count - 1) }, (_, index) => Math.round(size * (index + 1) / count));
}

/** Earlier frames whose centre lies in the cell. */
function framesIn(cell: Box, frames: CollageView["frames"]) {
  return frames.filter(({ box }) => {
    const x = (box[0] + box[2]) / 2, y = (box[1] + box[3]) / 2;
    return x >= cell[0] && x < cell[2] && y >= cell[1] && y < cell[3];
  });
}

/** What the cell held before this cut: one earlier frame, as a cell over several of them held none in particular. */
export function heldIn(cell: Box, frames: CollageView["frames"]) {
  const inside = framesIn(cell, frames);
  return inside.length === 1 ? inside[0] : undefined;
}

/** Frames cut with their light margins trimmed do not touch, so they differ from the cells of their lines. */
export function trimmedBoxes(boxes: Box[], size: [number, number]): boolean {
  const cells = cellsFromLines(linesFromBoxes(boxes, size), size);
  return boxes.some((box) => !cells.some((cell) => cell.every((value, at) => Math.abs(value - box[at]) <= 2)));
}

/** Lines turned to the other direction: the same number of frames, on found seams or evenly. */
export function turnedLines(lines: Lines, axis: Axis, size: [number, number], seams: Lines): Lines | null {
  const key = axis === "x" ? "xs" : "ys", other = axis === "x" ? "ys" : "xs";
  if (lines[key].length || !lines[other].length) return null;
  const length = axis === "x" ? size[0] : size[1];
  const frames = Math.min(lines[other].length + 1, Math.floor(length / MIN_FRAME));
  const values = seams[key].length === frames - 1 ? seams[key] : evenLines(length, frames);
  return axis === "x" ? { xs: values, ys: [] } : { xs: [], ys: values };
}

export function CutEditor({ view, resultId, variants, labelOf, onApply, onClose }: {
  view: CollageView;
  resultId: string;
  variants: string[];
  labelOf(key: string): string;
  onApply(boxes: Box[], targets: Target[], trim: boolean): void;
  onClose(): void;
}) {
  const [width, height] = view.size;
  const initial = view.boxes.length ? view.boxes : view.suggestion;
  const [opening] = useState(() => {
    const found = linesFromBoxes(initial, view.size);
    return { lines: found, cells: cellsFromLines(found, view.size) };
  });
  const [lines, setLines] = useState<Lines>(opening.lines);
  const [history, setHistory] = useState<Lines[]>([]);
  const [mode, setMode] = useState<Axis>(opening.lines.ys.length > opening.lines.xs.length ? "y" : "x");
  const [selected, setSelected] = useState<{ axis: Axis; index: number } | null>(null);
  // Trimming is on only where the earlier cut trimmed: on renders edge to edge it would eat a light sky.
  const [trim, setTrim] = useState(() => view.trim ?? trimmedBoxes(initial, view.size));
  const [snaps, setSnaps] = useState<Lines>(opening.lines);
  const [searching, setSearching] = useState(false);
  // Lines run through the whole picture, so a cut of nested frames turns into a grid with more of them.
  const [note, setNote] = useState(() => view.boxes.length && opening.cells.length !== view.boxes.length
    ? `Программа разрезала картинку не сеткой, а линии здесь проходят через всю картинку: кадров станет ${opening.cells.length} вместо ${view.boxes.length}. Если прежняя разрезка верна, нажмите «Отмена».`
    : "");
  // A frame removed earlier stays unsaved in the re-cut.
  const [skipped, setSkipped] = useState<Set<number>>(() => new Set(view.frames.length
    ? opening.cells.flatMap((cell, index) => framesIn(cell, view.frames).length ? [] : [index]) : []));
  const [targets, setTargets] = useState<Record<number, Target>>({});
  const [guide, setGuide] = useState<number | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  const drag = useRef<{ axis: Axis; index: number; pointer: number; before: Lines | null } | null>(null);
  const cellCount = useRef(opening.cells.length);
  const cells = useMemo(() => cellsFromLines(lines, view.size), [lines, view.size]);
  const held = useMemo(() => cells.map((cell) => heldIn(cell, view.frames)), [cells, view.frames]);
  // Unless the person chooses, a frame goes where the frame in its place is now.
  function targetOf(index: number): Target {
    if (index in targets) return targets[index];
    const frame = held[index];
    return frame ? frame.variant : "keep";
  }
  const kept = cells.map((_, index) => index).filter((index) => !skipped.has(index));
  const whole = cells.length === 1;
  const extra = cells.map((_, index) => targetOf(index))
    .filter((value): value is string => typeof value === "string" && value !== "keep" && !variants.includes(value));
  const options = [...new Set([...variants, ...extra])].sort(compareVariants);
  const base = view.variant ?? variants[0] ?? "1";

  useEffect(() => {
    if (cellCount.current === cells.length) return;
    cellCount.current = cells.length;
    setSkipped(new Set());
    setTargets({});
  }, [cells.length]);
  // Runs after the dialog opened, so the picture takes the focus from its close button.
  useEffect(() => { stage.current?.focus({ preventScroll: true }); }, []);
  // Ctrl+Z steps back wherever the focus is in the editor, its heading included.
  const latestUndo = useRef(() => {});
  useEffect(() => { latestUndo.current = undo; });
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey) || event.code !== "KeyZ") return;
      if (event.target instanceof Element && event.target.closest("select, input")) return;
      event.preventDefault();
      latestUndo.current();
    }
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);

  function commit(next: Lines) {
    setHistory((past) => [...past.slice(-49), lines]);
    setNote("");
    setLines({ xs: [...next.xs].sort((a, b) => a - b), ys: [...next.ys].sort((a, b) => a - b) });
  }
  function scale() {
    const rect = stage.current?.getBoundingClientRect();
    return rect && rect.width ? rect.width / width : 1;
  }
  function snapped(axis: Axis, value: number) {
    const limit = SNAP / scale();
    const near = snaps[axis === "x" ? "xs" : "ys"].find((position) => Math.abs(position - value) <= limit);
    return near ?? value;
  }
  function allowed(axis: Axis, value: number, except?: number) {
    const size = axis === "x" ? width : height;
    const others = lines[axis === "x" ? "xs" : "ys"].filter((_, index) => index !== except);
    return value >= MIN_FRAME && value <= size - MIN_FRAME && others.every((other) => Math.abs(other - value) >= MIN_FRAME);
  }
  function along(axis: Axis, event: { clientX: number; clientY: number }) {
    const rect = stage.current!.getBoundingClientRect();
    return axis === "x" ? (event.clientX - rect.left) / rect.width * width : (event.clientY - rect.top) / rect.height * height;
  }
  function onPicture(event: ReactPointerEvent) {
    return !!stage.current && !(event.target as Element).closest(".cut-line, .cut-callout");
  }
  function place(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !onPicture(event)) return;
    const value = Math.round(snapped(mode, along(mode, event)));
    if (!allowed(mode, value)) return;
    const key = mode === "x" ? "xs" : "ys";
    const next = { ...lines, [key]: [...lines[key], value] };
    commit(next);
    setSelected({ axis: mode, index: [...next[key]].sort((a, b) => a - b).indexOf(value) });
    setGuide(null);
  }
  function hover(event: ReactPointerEvent<HTMLDivElement>) {
    if (drag.current || !onPicture(event)) { setGuide(null); return; }
    const value = Math.round(snapped(mode, along(mode, event)));
    setGuide(allowed(mode, value) ? value : null);
  }
  function move(axis: Axis, index: number, value: number, record = true) {
    const key = axis === "x" ? "xs" : "ys";
    const size = axis === "x" ? width : height;
    const clamped = Math.round(Math.max(MIN_FRAME, Math.min(size - MIN_FRAME, value)));
    if (!allowed(axis, clamped, index)) return;
    const next = { ...lines, [key]: lines[key].map((old, at) => at === index ? clamped : old) };
    if (record) commit(next); else setLines(next);
  }
  function remove(axis: Axis, index: number) {
    const key = axis === "x" ? "xs" : "ys";
    commit({ ...lines, [key]: lines[key].filter((_, at) => at !== index) });
    setSelected(null);
  }
  function even(axis: Axis, count: number) {
    const key = axis === "x" ? "xs" : "ys";
    commit({ ...lines, [key]: evenLines(axis === "x" ? width : height, count) });
    setMode(axis);
    setSelected(null);
  }
  /** The direction is that of the cut, not only of a new line: lines across the other way turn round. */
  function orient(axis: Axis) {
    setMode(axis);
    const next = turnedLines(lines, axis, view.size, snaps);
    if (!next) return;
    commit(next);
    setSelected(null);
    const frames = (axis === "x" ? next.xs : next.ys).length + 1;
    const unit = axis === "x" ? plural(frames, ["колонка", "колонки", "колонок"]) : plural(frames, ["ряд", "ряда", "рядов"]);
    const seams = (axis === "x" ? snaps.xs : snaps.ys).length === frames - 1;
    setNote(`Линии развёрнуты: ${frames} ${unit} ${seams ? "по найденным швам" : "поровну"}. Передвиньте их, если кадры разного размера.`);
  }
  async function findSeams() {
    setSearching(true);
    setNote("");
    try {
      const found = await request<{ kind: string; boxes: Box[] }>(`results/${resultId}/seams`,
        view.collage !== null ? { collage: view.collage } : { source: view.source });
      if (!found.boxes.length) { setNote("Швы не найдены. Поставьте линии вручную или разделите поровну."); return; }
      const next = linesFromBoxes(found.boxes, view.size);
      commit(next);
      setSnaps(next);
      setSelected(null);
      setMode(next.ys.length > next.xs.length ? "y" : "x");
      setTrim(found.kind === "gutters");
      setNote(found.kind === "gutters" ? "Линии поставлены по светлым полосам между кадрами." : "Линии поставлены на стыки кадров одинакового размера.");
    } catch (error) {
      setNote(errorText(error));
    } finally {
      setSearching(false);
    }
  }
  function undo() {
    const last = history.at(-1);
    if (!last) return;
    setHistory(history.slice(0, -1));
    setLines(last);
    setSelected(null);
    setNote("");
    // Lines one way only bring their direction back too.
    if (!last.xs.length !== !last.ys.length) setMode(last.xs.length ? "x" : "y");
  }
  function apply() {
    if (!kept.length || (whole && !view.boxes.length)) return;
    onApply(kept.map((index) => cells[index]), kept.map(targetOf), trim && !whole);
  }
  function eachToSubvariant() {
    const used = [...options];
    const next: Record<number, string> = {};
    for (const index of kept) {
      const key = nextSubvariant(used, base);
      used.push(key);
      next[index] = key;
    }
    setTargets(next);
  }
  function keyDown(event: ReactKeyboardEvent) {
    const target = event.target as Element;
    if (target.closest("select, input")) return;
    // Enter, Delete and arrows work on the picture; on a button Enter presses that button.
    if (!target.closest(".cut-stage") || target.closest("button")) return;
    if (event.key === "Enter") { event.preventDefault(); apply(); return; }
    if (!selected || selectedValue === null) return;
    if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); remove(selected.axis, selected.index); return; }
    const step = event.shiftKey ? 10 : 1;
    const delta = { ArrowLeft: -step, ArrowUp: -step, ArrowRight: step, ArrowDown: step }[event.key];
    const matches = selected.axis === "x" ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
    if (delta !== undefined && matches.includes(event.key)) {
      event.preventDefault();
      move(selected.axis, selected.index, selectedValue + delta);
    }
  }
  const summary = whole
    ? view.boxes.length ? "Склейка вернётся на своё место одной картинкой."
      : "Поставьте хотя бы одну линию: щёлкните по картинке, разделите её поровну или найдите швы."
    : `${kept.length} ${plural(kept.length, ["кадр встанет", "кадра встанут", "кадров встанут"])} на место исходной картинки` +
      (skipped.size ? `, ${skipped.size} ${plural(skipped.size, ["кадр не сохранится", "кадра не сохранятся", "кадров не сохранятся"])}` : "") +
      ". Склейка останется в разделе «Склейки» — разрезку можно изменить или отменить. На диск всё запишется по «Сохранить».";
  const columns = lines.xs.length + 1, rows = lines.ys.length + 1;
  const most = (size: number) => Math.min(12, Math.floor(size / MIN_FRAME));
  const selectedValue = selected ? lines[selected.axis === "x" ? "xs" : "ys"][selected.index] ?? null : null;
  const snappedNow = selected && selectedValue !== null && snaps[selected.axis === "x" ? "xs" : "ys"].includes(selectedValue);
  const arrowsAxis = selected && selectedValue !== null ? selected.axis : mode;
  return (
    <Dialog title="Разрезать склейку" wide className="cut-editor" onClose={onClose}
      subtitle={<p className="cut-editor-subtitle">Страница {view.page} · {view.title} · {width} × {height} px</p>}>
      <div className="cut-editor-body" onKeyDown={keyDown}>
        <div className="cut-tools" role="toolbar" aria-label="Инструменты разрезки">
          <span className="cut-tools-label">Направление</span>
          <div className="cut-segment" role="group" aria-label="Направление линий разреза">
            <button aria-pressed={mode === "x"} className={mode === "x" ? "active" : ""} onClick={() => orient("x")}>
              <SeparatorVertical size={16} />Вертикальная</button>
            <button aria-pressed={mode === "y"} className={mode === "y" ? "active" : ""} onClick={() => orient("y")}>
              <SeparatorHorizontal size={16} />Горизонтальная</button>
          </div>
          <span className="cut-divider" />
          <span className="cut-tools-label">Поровну</span>
          <div className="cut-stepper" role="group" aria-label="Колонки">
            <Columns3 size={16} />
            <button aria-label="Меньше колонок" disabled={columns <= 1} onClick={() => even("x", columns - 1)}><Minus size={16} /></button>
            <b aria-label="Колонки">{columns}</b>
            <button aria-label="Больше колонок" disabled={columns >= most(width)} onClick={() => even("x", columns + 1)}><Plus size={16} /></button>
          </div>
          <div className="cut-stepper" role="group" aria-label="Ряды">
            <Rows3 size={16} />
            <button aria-label="Меньше рядов" disabled={rows <= 1} onClick={() => even("y", rows - 1)}><Minus size={16} /></button>
            <b aria-label="Ряды">{rows}</b>
            <button aria-label="Больше рядов" disabled={rows >= most(height)} onClick={() => even("y", rows + 1)}><Plus size={16} /></button>
          </div>
          <span className="cut-divider" />
          <button className="btn text-action" disabled={searching} onClick={() => void findSeams()}><Sparkles size={16} />Найти швы</button>
          <span className="cut-spacer" />
          <label className="cut-toggle">
            <input type="checkbox" checked={trim} onChange={(event) => setTrim(event.target.checked)} />
            <span aria-hidden="true" />Обрезать светлые поля
          </label>
          <button className="btn text-action" disabled={!lines.xs.length && !lines.ys.length}
            onClick={() => { commit({ xs: [], ys: [] }); setSelected(null); }}><Trash2 size={16} />Убрать линии</button>
        </div>
        <div className="cut-workspace">
          <div className="cut-canvas">
            <div className="cut-stage" ref={stage} onPointerDown={place} onPointerMove={hover} onPointerLeave={() => setGuide(null)}
              tabIndex={0} aria-label={`Картинка склейки: щелчок добавляет ${mode === "x" ? "вертикальную" : "горизонтальную"} линию`}
              style={{ aspectRatio: `${width} / ${height}`, "--ratio": width / height } as CSSProperties}>
              {view.preview && <img src={view.preview} alt="" draggable={false} />}
              {guide !== null && <span className={`cut-guide ${mode === "x" ? "vertical" : "horizontal"}`} aria-hidden="true"
                style={mode === "x" ? { left: `${(guide / width) * 100}%` } : { top: `${(guide / height) * 100}%` }} />}
              {(["x", "y"] as const).flatMap((axis) => lines[axis === "x" ? "xs" : "ys"].map((value, index) => {
                const active = selected?.axis === axis && selected.index === index;
                const position = axis === "x" ? { left: `${(value / width) * 100}%` } : { top: `${(value / height) * 100}%` };
                return (
                  <div key={`${axis}${index}`} role="slider" tabIndex={-1}
                    aria-label={axis === "x" ? `Вертикальная линия ${index + 1}` : `Горизонтальная линия ${index + 1}`}
                    aria-valuenow={value} aria-valuemin={MIN_FRAME} aria-valuemax={(axis === "x" ? width : height) - MIN_FRAME}
                    className={`cut-line ${axis === "x" ? "vertical" : "horizontal"}${active ? " is-selected" : ""}`} style={position}
                    onPointerDown={(event) => {
                      event.stopPropagation();
                      if (event.button !== 0) return;
                      event.currentTarget.setPointerCapture(event.pointerId);
                      // History gets the lines as they were only once the line really moves.
                      drag.current = { axis, index, pointer: event.pointerId, before: lines };
                      setSelected({ axis, index });
                      setGuide(null);
                      stage.current?.focus({ preventScroll: true });
                    }}
                    onPointerMove={(event) => {
                      const current = drag.current;
                      if (!current || current.pointer !== event.pointerId || !stage.current) return;
                      if (current.before) {
                        const before = current.before;
                        setHistory((past) => [...past.slice(-49), before]);
                        current.before = null;
                      }
                      move(axis, index, snapped(axis, along(axis, event)), false);
                    }}
                    onPointerUp={() => { drag.current = null; }}
                    onPointerCancel={() => { drag.current = null; }}>
                    <span className="cut-handle" />
                  </div>
                );
              }))}
              {cells.map((cell, index) => (
                <span key={index} className={`cut-number${skipped.has(index) ? " is-skipped" : ""}`}
                  style={{ left: `${(cell[0] / width) * 100}%`, top: `${(cell[3] / height) * 100}%` }}>{index + 1}</span>
              ))}
              {selected && selectedValue !== null && (
                <div className="cut-callout" style={selected.axis === "x"
                  ? { left: `${(selectedValue / width) * 100}%`, top: "12px" }
                  : { top: `${(selectedValue / height) * 100}%`, left: "12px" }}>
                  <b>{selectedValue} px</b>{snappedNow && <span>прилипла к стыку кадров</span>}
                  <button aria-label="Удалить линию" onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => remove(selected.axis, selected.index)}>×</button>
                </div>
              )}
            </div>
            <div className="cut-hints">
              <span><kbd>Щелчок</kbd>добавить {mode === "x" ? "вертикальную" : "горизонтальную"} линию</span>
              <span><kbd>Перетаскивание</kbd>сдвинуть линию</span>
              <span><kbd>Delete</kbd>удалить выбранную</span>
              <span><kbd>{arrowsAxis === "x" ? "← →" : "↑ ↓"}</kbd>сдвиг на 1 px, с Shift — на 10 px</span>
              <span><kbd>Ctrl+Z</kbd>отменить шаг</span>
            </div>
            {note && <p className="cut-note" role="status">{note}</p>}
          </div>
          <aside className="cut-panel" aria-label="Кадры">
            <div className="cut-panel-head">
              <h3>Кадры <b>{cells.length}</b></h3>
              {!whole && <>
                <label className="cut-bulk">Куда все:
                  <select value="" onChange={(event) => {
                    const value = event.target.value === "__keep" ? "keep" : event.target.value === "__misc" ? null : event.target.value;
                    setTargets(Object.fromEntries(kept.map((index) => [index, value])));
                  }}>
                    <option value="" disabled>Выберите…</option>
                    <option value="__keep">На место склейки</option>
                    {options.map((key) => <option key={key} value={key}>{labelOf(key)}</option>)}
                    <option value="__misc">Без варианта</option>
                  </select>
                </label>
                <button className="cut-subvariants" onClick={eachToSubvariant}>
                  Или: каждый кадр — в свой подвариант {nextSubvariant(options, base)}…
                </button>
              </>}
            </div>
            <ol className="cut-list">
              {cells.map((cell, index) => {
                const off = skipped.has(index);
                const target = targetOf(index);
                return (
                  <li key={index} className={off ? "is-skipped" : ""}>
                    {view.preview && <CroppedImage src={view.preview} size={view.size} box={cell} alt="" className="cut-thumb" />}
                    <div>
                      <strong>Кадр {index + 1}</strong><span>{cell[2] - cell[0]} × {cell[3] - cell[1]} px</span>
                      {!whole && <select aria-label={`Куда поместить кадр ${index + 1}`} disabled={off}
                        value={target === "keep" ? "__keep" : target === null ? "__misc" : target}
                        onChange={(event) => {
                          const value = event.target.value;
                          setTargets({ ...targets, [index]: value === "__keep" ? "keep" : value === "__misc" ? null : value });
                        }}>
                        <option value="__keep">На место склейки</option>
                        {options.map((key) => <option key={key} value={key}>{labelOf(key)}</option>)}
                        <option value="__misc">Без варианта</option>
                      </select>}
                      {!whole && <button className="text-action" onClick={() => {
                        const next = new Set(skipped);
                        if (off) next.delete(index); else next.add(index);
                        setSkipped(next);
                      }}>{off ? <><Eye size={15} />Сохранить кадр</> : <><EyeOff size={15} />Не сохранять</>}</button>}
                    </div>
                  </li>
                );
              })}
            </ol>
          </aside>
        </div>
        <div className="cut-footer">
          <p><Layers size={18} />{summary}</p>
          <button className="btn secondary" onClick={onClose}>Отмена</button>
          <button className="btn primary" disabled={!kept.length || (whole && !view.boxes.length)} onClick={apply}>
            <Scissors size={16} />{whole ? "Вернуть целиком" : `Разрезать на ${kept.length} ${plural(kept.length, ["кадр", "кадра", "кадров"])}`}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
