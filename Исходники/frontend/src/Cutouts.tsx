import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent } from "react";
import { ChevronLeft, ChevronRight, CircleHelp, Crop, Image as ImageIcon, Images, LocateFixed, Magnet, Maximize, Minus, Pencil, Plus, Scan, SquareScissors,
  Shapes, SquareDashed, Trash, Trash2, TriangleAlert, Undo2 } from "lucide-react";
import type { LucideProps } from "lucide-react";
import { errorText, plural, request } from "./api";
import { Dialog, DiscardDialog, NewVariantDialog, Select } from "./components";
import type { ActionMenuItem, SelectOption } from "./components";
import { useContextMenu } from "./contextMenu";
import { Presence, reducedMotion } from "./motion";
import { CroppedImage } from "./Collages";
import { compareVariants, nextSubvariant, rectBox } from "./editor";
import type { PagesInfo, Rect } from "./types";
import "./cutouts.css";

type Target = string | null | "keep";

/** Where cutouts come from as the section and the editor show them: saved state merged with the draft. */
export interface CutoutSource {
  key: string;
  record: string | null;
  kind: "image" | "page";
  page: number;
  /** The picture the cutouts replace; null for page snapshots. */
  source: number | null;
  area: Rect;
  size: [number, number];
  title: string;
  /** The picture (or the page) the cutouts are drawn over. */
  preview: string | null;
  pending: boolean;
  /** Live cutouts now, in reading order: card id, frame on the page, where the card sits and whether it
   * is drawn with the marks over its picture. */
  pieces: { id: number; rect: Rect; variant: string | null; marks: boolean }[];
  /** Where the cutouts are, or the picture was: a variant key, else null. */
  variant: string | null;
  variantLabel: string | null;
}
/** The cutouts of one source as the editor leaves them. */
export interface CutoutResult {
  source: CutoutSource;
  rects: Rect[];
  targets: Target[];
  marks: boolean[];
}
/** A picture of the page the editor may offer as a new source. */
export interface PictureInfo {
  title: string;
  preview: string | null;
  variant: string | null;
  variantLabel: string | null;
}

/** A frame may stray this far past a picture and still be cut from its pixels; the server allows the same. */
const TOLERANCE = 0.006;
const MIN_PIXELS = 16;
const SNAP = 8;
const ZOOMS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];

export function pageUrl(resultId: string, page: number, thumb = false): string {
  return new URL(`api/results/${encodeURIComponent(resultId)}/page/${page}${thumb ? "?size=thumb" : ""}`, document.baseURI).href;
}

export function contains(area: Rect, rect: Rect, tolerance = TOLERANCE): boolean {
  return rect[0] >= area[0] - tolerance && rect[1] >= area[1] - tolerance &&
    rect[2] <= area[2] + tolerance && rect[3] <= area[3] + tolerance;
}

/** The picture a frame is cut from: the smallest one holding it; anything else is a snapshot of the page. */
export function sourceFor(rect: Rect, pictures: CutoutSource[]): CutoutSource | undefined {
  let best: CutoutSource | undefined;
  let bestArea = Infinity;
  for (const picture of pictures) {
    const [left, top, right, bottom] = picture.area;
    const size = (right - left) * (bottom - top);
    if (contains(picture.area, rect) && size < bestArea) { best = picture; bestArea = size; }
  }
  return best;
}

/** Whether a frame covers any of the marks drawn over its picture, not just touches its edge. */
export function touches(rect: Rect, marks: Rect[]): boolean {
  return marks.some((mark) => Math.min(rect[2], mark[2]) - Math.max(rect[0], mark[0]) > 1e-6 &&
    Math.min(rect[3], mark[3]) - Math.max(rect[1], mark[1]) > 1e-6);
}

/** The nearest candidate within the limit, if any. */
export function snapTo(value: number, candidates: number[], limit: number): number | null {
  let best: number | null = null;
  let distance = limit;
  for (const candidate of candidates) {
    const gap = Math.abs(candidate - value);
    if (gap <= distance) { best = candidate; distance = gap; }
  }
  return best;
}

/** Reading order on the page: top to bottom, then left to right. */
export function byPlace(a: Rect, b: Rect): number {
  return Math.abs(a[1] - b[1]) > 0.01 ? a[1] - b[1] : a[0] - b[0];
}

/** Numbers of the cutouts across the result, page by page in reading order, as the editor numbers its frames. */
export function cutoutNumbers(views: CutoutSource[]): Map<number, number> {
  const pieces = views.flatMap((view) => view.pieces.map((piece) => ({ page: view.page, piece })));
  pieces.sort((a, b) => a.page - b.page || byPlace(a.piece.rect, b.piece.rect));
  return new Map(pieces.map(({ piece }, index) => [piece.id, index + 1]));
}

/** The frames picked after a click on one: it alone, added or taken away with Ctrl, a run in list order with Shift
 * (from the anchor, the last frame clicked without Shift). The last frame picked is the one in hand. */
export function pickFrames(picked: number[], anchor: number | null, uid: number, order: number[],
  mode: "only" | "toggle" | "range"): { picked: number[]; anchor: number | null } {
  if (mode === "range" && anchor !== null) {
    const from = order.indexOf(anchor), to = order.indexOf(uid);
    if (from >= 0 && to >= 0) {
      const run = order.slice(Math.min(from, to), Math.max(from, to) + 1).filter((value) => value !== uid);
      return { picked: [...run, uid], anchor };
    }
  }
  if (mode === "toggle") {
    if (!picked.includes(uid)) return { picked: [...picked, uid], anchor: uid };
    const rest = picked.filter((value) => value !== uid);
    return { picked: rest, anchor: rest.at(-1) ?? null };
  }
  return { picked: [uid], anchor: uid };
}

function sameRect(a: Rect, b: Rect): boolean {
  return a.every((value, index) => Math.abs(value - b[index]) < 1e-6);
}

/** Scrolls the list just enough to show an item, never past its top: a group taller than the list starts at its head. */
function reveal(container: HTMLElement, item: HTMLElement) {
  const room = 12;
  const box = container.getBoundingClientRect(), own = item.getBoundingClientRect();
  const above = own.top - box.top - room, below = own.bottom - box.bottom + room;
  const shift = above < 0 ? above : below > 0 ? Math.min(above, below) : 0;
  if (Math.abs(shift) >= 1) container.scrollBy({ top: shift, behavior: reducedMotion() ? "auto" : "smooth" });
}

function clampRect([left, top, right, bottom]: Rect): Rect {
  return [Math.max(0, left), Math.max(0, top), Math.min(1, right), Math.min(1, bottom)];
}

/** The part of a source a frame covers, in shares of the source: for frames drawn over its picture. */
function relative(rect: Rect, area: Rect): CSSProperties {
  const width = area[2] - area[0], height = area[3] - area[1];
  return { left: `${(rect[0] - area[0]) / width * 100}%`, top: `${(rect[1] - area[1]) / height * 100}%`,
    width: `${(rect[2] - rect[0]) / width * 100}%`, height: `${(rect[3] - rect[1]) / height * 100}%` };
}

function SourcePicture({ view, maxHeight, numbers }: { view: CutoutSource; maxHeight: number; numbers: Map<number, number> }) {
  const [width, height] = view.size;
  return (
    <span className="collage-picture cutout-picture" style={{ aspectRatio: `${width} / ${height}`,
      width: `min(100%, ${Math.round(maxHeight * width / height)}px)` }}>
      {view.preview ? <img src={view.preview} alt={view.title} draggable={false} loading="lazy" /> : <span className="no-preview" />}
      {view.pieces.map((piece, index) => (
        <span key={piece.id} className="collage-frame cutout-card-frame" style={relative(piece.rect, view.area)}><b>{numbers.get(piece.id) ?? index + 1}</b></span>
      ))}
    </span>
  );
}

export function CutoutsPage({ views, count, saving, onOpen, onReturn }: {
  views: CutoutSource[];
  /** All cutouts, as in the menu. */
  count: number;
  saving: boolean;
  onOpen(page?: number): void;
  onReturn(view: CutoutSource): void;
}) {
  const numbers = cutoutNumbers(views);
  const context = useContextMenu();
  return (
    <section className="collages-page cutouts-page" aria-label="Вырезки">
      {context.menu}
      <div className="library-header">
        <div><h2>Вырезки</h2><span>{count}</span></div>
        {/* An empty section offers the same button in its middle and explains itself there. */}
        {!!views.length && <button className="btn compact primary" disabled={saving} onClick={() => onOpen()}><SquareScissors size={16} />Вырезать со страницы…</button>}
      </div>
      {!!views.length && <div className="section-note"><Crop size={16} aria-hidden="true" />
        <p>Части страниц PDF, вырезанные вручную. Вырезка встаёт в вариант вместо картинки, а саму картинку можно вернуть целиком.</p>
      </div>}
      {views.length ? (
        <div className="collages-grid">
          {views.map((view) => {
            const count = view.pieces.length;
            const where = view.variantLabel ? `в «${view.variantLabel}» ` : "";
            return (
              <article key={view.key} className="collage-card cutout-card" aria-label={`Вырезки: ${view.title}`}
                onContextMenu={(event) => context.open(event, {
                  heading: view.title,
                  // The card's own buttons.
                  items: [
                    { value: "open", label: "Изменить вырезки", icon: <Pencil size={16} />, disabled: saving },
                    view.kind === "image"
                      ? { value: "return", label: "Вернуть картинку", icon: <Undo2 size={16} />, disabled: saving }
                      : { value: "return", label: "Удалить вырезки", icon: <Trash2 size={16} />, danger: true, disabled: saving },
                  ],
                  onAction: (value) => value === "open" ? onOpen(view.page) : onReturn(view),
                })}>
                <button className="collage-stage" disabled={saving} onClick={() => onOpen(view.page)}
                  aria-label={`Изменить вырезки: ${view.title}, страница ${view.page}`}>
                  <SourcePicture view={view} maxHeight={220} numbers={numbers} />
                </button>
                <div className="collage-body">
                  <div className="collage-chips">
                    {view.kind === "image"
                      ? <span className="collage-chip blue"><Crop size={14} />{count} {plural(count, ["вырезка", "вырезки", "вырезок"])}</span>
                      : <span className="collage-chip amber"><Scan size={14} />Снимок страницы{count > 1 ? ` · ${count}` : ""}</span>}
                    {view.pieces.some((piece) => piece.marks) && <span className="collage-chip blue"><Shapes size={14} />с пометками</span>}
                    {view.pending && <span className="collage-chip blue">не сохранено</span>}
                    <span className="collage-chip neutral">Стр. {view.page}{view.variantLabel ? ` · ${view.variantLabel}` : ""}</span>
                  </div>
                  <h3 title={view.title}>{view.title}</h3>
                  <p>{view.kind === "image"
                    ? `${count > 1 ? "Вырезки стоят" : "Вырезка стоит"} ${where}вместо этой картинки. Сама картинка сохранена — её можно вернуть.`
                    : `На этом месте нет отдельной картинки: ${count > 1 ? "вырезки сделаны" : "вырезка сделана"} снимком страницы в высоком разрешении.`}</p>
                </div>
                <div className="collage-actions">
                  <button className="btn secondary" disabled={saving} onClick={() => onOpen(view.page)}><Pencil size={16} />Изменить вырезки</button>
                  <button className="btn text-action" disabled={saving} onClick={() => onReturn(view)}>
                    {view.kind === "image" ? <><Undo2 size={16} />Вернуть картинку</> : <><Trash2 size={16} />Удалить вырезки</>}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="cutouts-empty">
          <span className="cutouts-empty-icon"><Crop size={20} /></span>
          <strong>Вырезок пока нет</strong>
          <p>Откройте страницы исходного PDF и обведите рамкой нужную часть — например, рендер без схемы, которая лежит поверх него.</p>
          <button className="btn primary" disabled={saving} onClick={() => onOpen()}><SquareScissors size={16} />Вырезать со страницы…</button>
        </div>
      )}
    </section>
  );
}

interface Frame {
  uid: number;
  page: number;
  rect: Rect;
  /** The card this frame shows, while it is not moved to another source. */
  origin?: number;
  target?: Target;
  /** Drawn with the marks over its picture; unset — with them, as the page shows the picture. */
  marks?: boolean;
}
type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
type Drag =
  | { mode: "draw"; pointer: number; start: [number, number] }
  | { mode: "move"; pointer: number; uid: number; start: [number, number]; rect: Rect; before: Frame[] | null }
  | { mode: "resize"; pointer: number; uid: number; handle: Handle; rect: Rect; before: Frame[] | null };
const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

// The page editor draws its 16 px icons with a 2 px line, as in its Figma design.
const LINE: LucideProps = { size: 16, strokeWidth: 2, absoluteStrokeWidth: true };

export function CutoutEditor({ resultId, title, views, pictureInfo, isLive, start, variants, labelOf, dotOf, onApply, onClose, onHelp }: {
  resultId: string;
  title: string;
  views: CutoutSource[];
  pictureInfo(index: number): PictureInfo;
  isLive(index: number): boolean;
  start: { page: number; picture?: number };
  variants: string[];
  labelOf(key: string): string;
  /** The colour class of a variant's dot, as in the sidebar. */
  dotOf(key: string): string;
  /** The cutouts changed, and names given to variants made for them here. */
  onApply(results: CutoutResult[], names: Record<string, string>): void;
  onClose(): void;
  /** The help window on «Вырезки», over the editor. */
  onHelp?(): void;
}) {
  const [info, setInfo] = useState<PagesInfo | null>(null);
  const [failure, setFailure] = useState("");
  const [page, setPage] = useState(start.page);
  const [frames, setFrames] = useState<Frame[]>(() => {
    let uid = 0;
    return views.flatMap((view) => view.pieces.map((piece) => ({ uid: ++uid, page: view.page, rect: piece.rect, origin: piece.id,
      marks: piece.marks })));
  });
  const [history, setHistory] = useState<Frame[][]>([]);
  // Frames drawn or moved (history), sent to variants or with marks switched: closing would lose them, so it asks.
  const [touched, setTouched] = useState(false);
  const [askClose, setAskClose] = useState(false);
  const requestClose = () => { if (touched || history.length) setAskClose(true); else onClose(); };
  // Frames picked in the list or on the page, for «Куда выбранные»; the last one is in hand: its handles,
  // its open row, its size over it.
  const [picked, setPicked] = useState<number[]>([]);
  const selected = picked.at(-1) ?? null;
  const anchor = useRef<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [showPictures, setShowPictures] = useState(true);
  const [snapping, setSnapping] = useState(true);
  const [drawing, setDrawing] = useState<Rect | null>(null);
  const [snapped, setSnapped] = useState(false);
  const [note, setNote] = useState("");
  const [bounds, setBounds] = useState({ width: 0, height: 0 });
  // Frames waiting for the number of a new variant.
  const [naming, setNaming] = useState<number[] | null>(null);
  // Variants made here with a name of their own.
  const [names, setNames] = useState<Record<string, string>>({});
  const label = (key: string) => names[key] ?? labelOf(key);
  const nextUid = useRef(frames.length);
  const scroller = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const pieceOf = useMemo(() => new Map(views.flatMap((view) => view.pieces.map((piece) => [piece.id, { view, piece }] as const))), [views]);

  useEffect(() => {
    const controller = new AbortController();
    request<PagesInfo>(`results/${encodeURIComponent(resultId)}/pages`, undefined, controller.signal)
      .then(setInfo)
      .catch((error: unknown) => { if (!controller.signal.aborted) setFailure(errorText(error)); });
    return () => controller.abort();
  }, [resultId]);

  // Every picture a frame may come from: sources with cutouts already and live pictures of the result.
  const pictures = useMemo((): CutoutSource[] => {
    if (!info) return [];
    const known = views.filter((view) => view.kind === "image");
    const fresh = info.pictures.filter((picture) => isLive(picture.image) && !known.some((view) => view.source === picture.image))
      .map((picture): CutoutSource => {
        const described = pictureInfo(picture.image);
        return { key: `cutout:img:${picture.image}`, record: null, kind: "image", page: picture.page, source: picture.image,
          area: picture.area, size: picture.size, title: described.title, preview: described.preview, pending: false, pieces: [],
          variant: described.variant, variantLabel: described.variantLabel };
      });
    return [...known, ...fresh];
  }, [info, views, isLive, pictureInfo]);
  function pageSource(number: number): CutoutSource {
    const saved = views.find((view) => view.kind === "page" && view.page === number);
    if (saved) return saved;
    const snapshot = info?.pages[number - 1]?.snapshot ?? [1, 1];
    return { key: `cutout:page:${number}`, record: null, kind: "page", page: number, source: null, area: [0, 0, 1, 1],
      size: snapshot, title: `Страница ${number}`, preview: pageUrl(resultId, number), pending: false, pieces: [],
      variant: null, variantLabel: null };
  }
  function sourceOf(frame: Frame): CutoutSource {
    return sourceFor(frame.rect, pictures.filter((picture) => picture.page === frame.page)) ?? pageSource(frame.page);
  }
  function pixels(frame: Frame): [number, number] {
    const source = sourceOf(frame);
    const box = rectBox(frame.rect, source.area, source.size);
    return [box[2] - box[0], box[3] - box[1]];
  }
  function targetOf(frame: Frame, source = sourceOf(frame)): Target {
    if (frame.target !== undefined) return source.kind === "page" && frame.target === "keep" ? null : frame.target;
    const held = frame.origin === undefined ? undefined : pieceOf.get(frame.origin);
    if (held && held.view.key === source.key) return held.piece.variant;
    return source.kind === "image" ? "keep" : null;
  }
  /** Marks drawn over a frame's picture that the frame covers; a page snapshot shows everything anyway. */
  function hasMarks(frame: Frame, source = sourceOf(frame)): boolean {
    return source.kind === "image" && source.source !== null && touches(frame.rect, info?.marks?.[String(source.source)] ?? []);
  }
  function marked(frame: Frame, source = sourceOf(frame)): boolean {
    return hasMarks(frame, source) && (frame.marks ?? true);
  }

  // The frames in reading order across pages, numbered as the list shows them.
  const ordered = useMemo(() => [...frames].sort((a, b) => a.page - b.page || byPlace(a.rect, b.rect)), [frames]);
  const numberOf = new Map(ordered.map((frame, index) => [frame.uid, index + 1]));
  const pageInfo = info?.pages[page - 1];
  const ratio = pageInfo ? pageInfo.size[0] / pageInfo.size[1] : 16 / 9;
  const fitWidth = Math.max(1, Math.min(bounds.width, bounds.height * ratio));
  const stageWidth = fitWidth * zoom, stageHeight = stageWidth / ratio;
  const onPage = frames.filter((frame) => frame.page === page);
  const pagePictures = pictures.filter((picture) => picture.page === page);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const measure = () => {
      const style = getComputedStyle(element);
      // Whole pixels, one short: a page that fits must not bring scrollbars that then shrink the room.
      setBounds({ width: Math.floor(element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)) - 1,
        height: Math.floor(element.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)) - 1 });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [info]);
  useEffect(() => {
    if (!info) return;
    const picture = start.picture === undefined ? undefined : pictures.find((value) => value.source === start.picture);
    const held = picture ? ordered.find((frame) => sourceOf(frame).key === picture.key) : ordered.find((frame) => frame.page === start.page);
    if (held || !picture) {
      // Opened on cutouts made before: the first of them is in hand, its row open in the list.
      if (held) select(held.uid);
      return;
    }
    // Opened from a picture: the frame starts at its edges, one edge in is enough to leave a plan out.
    const frame = { uid: ++nextUid.current, page: picture.page, rect: picture.area };
    setFrames((current) => [...current, frame]);
    select(frame.uid);
    setNote("Рамка поставлена по краям картинки — сдвиньте край, чтобы отрезать лишнее.");
    // Only once, when the pages arrive.
  }, [info]);
  useEffect(() => { stage.current?.focus({ preventScroll: true }); }, [info]);
  // The list follows the page: the row of the frame in hand comes into sight, however it was picked, and a page
  // turned without it shows its own group.
  const handPage = frames.find((frame) => frame.uid === selected)?.page;
  useEffect(() => {
    const element = list.current;
    const item = element?.querySelector<HTMLElement>(handPage === page ? `[data-uid="${selected}"]` : `[data-page="${page}"]`);
    if (element && item) reveal(element, item);
  }, [selected, handPage, page, info]);
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
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    function wheel(event: WheelEvent) {
      if (!event.ctrlKey) return;
      event.preventDefault();
      setZoom((value) => Math.max(0.25, Math.min(4, Math.round((value - event.deltaY * 0.002) * 100) / 100)));
    }
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [info]);

  function commit(next: Frame[], before = frames) {
    setHistory((past) => [...past.slice(-49), before]);
    setFrames(next);
    setNote("");
  }
  function undo() {
    const last = history.at(-1);
    if (!last) return;
    setHistory(history.slice(0, -1));
    setFrames(last);
    select(null);
    setNote("");
  }
  function select(uid: number | null) {
    setPicked(uid === null ? [] : [uid]);
    anchor.current = uid;
  }
  /** A click on a row or a frame: Ctrl adds the frame or takes it away, Shift picks a run, a plain click picks it
   * alone. The page turns to the frame picked. */
  function pick(frame: Frame, event: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) {
    const mode = event.shiftKey ? "range" : event.ctrlKey || event.metaKey ? "toggle" : "only";
    const next = pickFrames(picked, anchor.current, frame.uid, ordered.map((value) => value.uid), mode);
    setPicked(next.picked);
    anchor.current = next.anchor;
    if (next.picked.includes(frame.uid)) setPage(frame.page);
  }
  function point(event: { clientX: number; clientY: number }): [number, number] {
    const rect = stage.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height))];
  }
  /** Edges of pictures and of other frames on the page, as shares of the page. */
  function edges(except?: number) {
    const xs = [0, 1], ys = [0, 1];
    for (const picture of pagePictures) { xs.push(picture.area[0], picture.area[2]); ys.push(picture.area[1], picture.area[3]); }
    for (const frame of onPage) if (frame.uid !== except) { xs.push(frame.rect[0], frame.rect[2]); ys.push(frame.rect[1], frame.rect[3]); }
    return { xs, ys };
  }
  function snapEdge(value: number, axis: "x" | "y", candidates: { xs: number[]; ys: number[] }) {
    if (!snapping) return { value, hit: false };
    const found = snapTo(value, axis === "x" ? candidates.xs : candidates.ys, SNAP / (axis === "x" ? stageWidth : stageHeight));
    return found === null ? { value, hit: false } : { value: found, hit: true };
  }
  function normalized(a: [number, number], b: [number, number]): Rect {
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  }
  function startDraw(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || (event.target as Element).closest(".cutout-frame, .cut-callout")) return;
    const candidates = edges();
    const [x, y] = point(event);
    const start: [number, number] = [snapEdge(x, "x", candidates).value, snapEdge(y, "y", candidates).value];
    drag.current = { mode: "draw", pointer: event.pointerId, start };
    event.currentTarget.setPointerCapture(event.pointerId);
    select(null);
  }
  function startMove(event: ReactPointerEvent, frame: Frame, handle?: Handle) {
    event.stopPropagation();
    if (event.button !== 0) return;
    // With Ctrl or Shift a frame is picked, as in the list; only a plain press takes it in hand to move.
    if (!handle && (event.ctrlKey || event.metaKey || event.shiftKey)) {
      pick(frame, event);
      stage.current?.focus({ preventScroll: true });
      return;
    }
    stage.current?.setPointerCapture(event.pointerId);
    select(frame.uid);
    drag.current = handle
      ? { mode: "resize", pointer: event.pointerId, uid: frame.uid, handle, rect: frame.rect, before: frames }
      : { mode: "move", pointer: event.pointerId, uid: frame.uid, start: point(event), rect: frame.rect, before: frames };
    stage.current?.focus({ preventScroll: true });
  }
  function update(uid: number, rect: Rect, current: Extract<Drag, { before: Frame[] | null }>) {
    // History gets the frames as they were only once a frame really changes.
    if (current.before) { const before = current.before; setHistory((past) => [...past.slice(-49), before]); current.before = null; setNote(""); }
    setFrames((all) => all.map((frame) => frame.uid === uid ? { ...frame, rect } : frame));
  }
  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    const [x, y] = point(event);
    if (current.mode === "draw") {
      const candidates = edges();
      const sx = snapEdge(x, "x", candidates), sy = snapEdge(y, "y", candidates);
      setDrawing(normalized(current.start, [sx.value, sy.value]));
      setSnapped(sx.hit || sy.hit);
      return;
    }
    const candidates = edges(current.uid);
    const [left, top, right, bottom] = current.rect;
    if (current.mode === "move") {
      const width = right - left, height = bottom - top;
      let nx = Math.min(1 - width, Math.max(0, left + x - current.start[0]));
      let ny = Math.min(1 - height, Math.max(0, top + y - current.start[1]));
      // Either edge may stick; the nearer one wins.
      const a = snapEdge(nx, "x", candidates), b = snapEdge(nx + width, "x", candidates);
      const c = snapEdge(ny, "y", candidates), d = snapEdge(ny + height, "y", candidates);
      if (a.hit && (!b.hit || Math.abs(a.value - nx) <= Math.abs(b.value - nx - width))) nx = a.value; else if (b.hit) nx = b.value - width;
      if (c.hit && (!d.hit || Math.abs(c.value - ny) <= Math.abs(d.value - ny - height))) ny = c.value; else if (d.hit) ny = d.value - height;
      setSnapped(a.hit || b.hit || c.hit || d.hit);
      update(current.uid, clampRect([nx, ny, nx + width, ny + height]), current);
      return;
    }
    const least = 6 / stageWidth, leastY = 6 / stageHeight;
    let [nl, nt, nr, nb] = current.rect;
    let hit = false;
    const edge = (value: number, axis: "x" | "y") => { const found = snapEdge(value, axis, candidates); hit ||= found.hit; return found.value; };
    if (current.handle.includes("w")) nl = Math.min(edge(x, "x"), nr - least);
    if (current.handle.includes("e")) nr = Math.max(edge(x, "x"), nl + least);
    if (current.handle.includes("n")) nt = Math.min(edge(y, "y"), nb - leastY);
    if (current.handle.includes("s")) nb = Math.max(edge(y, "y"), nt + leastY);
    setSnapped(hit);
    update(current.uid, clampRect([nl, nt, nr, nb]), current);
  }
  function endDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    drag.current = null;
    if (current.mode !== "draw") return;
    const rect = drawing;
    setDrawing(null);
    if (!rect || (rect[2] - rect[0]) * stageWidth < 6 || (rect[3] - rect[1]) * stageHeight < 6) return;
    const frame = { uid: ++nextUid.current, page, rect };
    const [width, height] = pixels(frame);
    if (width < MIN_PIXELS || height < MIN_PIXELS) {
      setNote(`Рамка меньше ${MIN_PIXELS} px картинки. Обведите часть побольше.`);
      return;
    }
    commit([...frames, frame]);
    select(frame.uid);
  }
  function wholePicture(event: ReactMouseEvent<HTMLDivElement>) {
    if ((event.target as Element).closest(".cutout-frame, .cut-callout")) return;
    const [x, y] = point(event);
    const picture = sourceFor([x, y, x, y], pagePictures);
    if (!picture) return;
    const existing = onPage.find((frame) => sameRect(frame.rect, picture.area));
    if (existing) { select(existing.uid); return; }
    const frame = { uid: ++nextUid.current, page, rect: picture.area };
    commit([...frames, frame]);
    select(frame.uid);
  }
  function remove(uids: number[]) {
    if (!uids.length) return;
    commit(frames.filter((frame) => !uids.includes(frame.uid)));
    const rest = picked.filter((uid) => !uids.includes(uid));
    setPicked(rest);
    if (anchor.current !== null && uids.includes(anchor.current)) anchor.current = rest.at(-1) ?? null;
  }
  function setTarget(uids: number[], target: Target) {
    setTouched(true);
    setFrames(frames.map((frame) => uids.includes(frame.uid) ? { ...frame, target } : frame));
  }
  function setMarks(uid: number, marks: boolean) {
    setTouched(true);
    setFrames(frames.map((frame) => frame.uid === uid ? { ...frame, marks } : frame));
  }
  function show(frame: Frame) {
    setPage(frame.page);
    select(frame.uid);
    stage.current?.focus({ preventScroll: true });
  }
  function keyDown(event: ReactKeyboardEvent) {
    const target = event.target as Element;
    const erase = event.key === "Delete" || event.key === "Backspace";
    // Delete on a row in focus: the frames picked with it, or just its own.
    const row = target.classList.contains("cutout-row-hit") ? target.closest<HTMLElement>(".cutout-row") : null;
    if (row && erase) {
      const uid = Number(row.dataset.uid);
      event.preventDefault();
      remove(picked.includes(uid) ? picked : [uid]);
      return;
    }
    if (target.closest("select, input") || !target.closest(".cutout-stage") || target.closest("button")) return;
    if (event.key === "Enter") { event.preventDefault(); apply(); return; }
    const frame = frames.find((value) => value.uid === selected);
    if (!frame) return;
    if (erase) { event.preventDefault(); remove(picked); return; }
    const step = event.shiftKey ? 10 : 1;
    const shift = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
    if (!shift) return;
    event.preventDefault();
    const [left, top, right, bottom] = frame.rect;
    const dx = Math.min(1 - right, Math.max(-left, shift[0] / stageWidth));
    const dy = Math.min(1 - bottom, Math.max(-top, shift[1] / stageHeight));
    commit(frames.map((value) => value.uid === frame.uid ? { ...value, rect: [left + dx, top + dy, right + dx, bottom + dy] } : value));
  }

  // The result for every source whose cutouts changed, sources left without cutouts included.
  function results(): CutoutResult[] {
    const groups = new Map<string, { source: CutoutSource; frames: Frame[] }>();
    for (const view of views) groups.set(view.key, { source: view, frames: [] });
    for (const frame of frames) {
      const source = sourceOf(frame);
      if (!groups.has(source.key)) groups.set(source.key, { source, frames: [] });
      groups.get(source.key)!.frames.push(frame);
    }
    const changed: CutoutResult[] = [];
    for (const { source, frames: own } of groups.values()) {
      const list = [...own].sort((a, b) => byPlace(a.rect, b.rect));
      const targets = list.map((frame) => targetOf(frame, source));
      const marks = list.map((frame) => marked(frame, source));
      const same = list.length === source.pieces.length && list.every((frame, index) =>
        sameRect(frame.rect, source.pieces[index].rect) && targets[index] === source.pieces[index].variant &&
        marks[index] === source.pieces[index].marks);
      if (!same) changed.push({ source, rects: list.map((frame) => frame.rect), targets, marks });
    }
    return changed;
  }
  function apply() {
    const small = frames.find((frame) => { const [width, height] = pixels(frame); return width < MIN_PIXELS || height < MIN_PIXELS; });
    if (small) {
      show(small);
      setNote(`Вырезка ${numberOf.get(small.uid)} меньше ${MIN_PIXELS} px картинки. Увеличьте рамку или удалите её.`);
      return;
    }
    const changed = results();
    if (changed.length) onApply(changed, names); else onClose();
  }

  const extra = frames.map((frame) => targetOf(frame))
    .filter((value): value is string => typeof value === "string" && value !== "keep" && !variants.includes(value));
  const options = [...new Set([...variants, ...extra])].sort(compareVariants);
  const base = pictures.find((picture) => frames.some((frame) => sourceOf(frame).key === picture.key))?.variant ?? variants[0] ?? "1";
  // Several frames picked: the bulk choices of the panel act on them instead of on all.
  const several = picked.length > 1;
  const bulk = several ? picked : frames.map((frame) => frame.uid);
  function eachToSubvariant() {
    const used = [...options];
    const next = new Map<number, string>();
    for (const frame of ordered.filter((value) => bulk.includes(value.uid))) {
      const key = nextSubvariant(used, base);
      used.push(key);
      next.set(frame.uid, key);
    }
    setTouched(true);
    setFrames(frames.map((frame) => next.has(frame.uid) ? { ...frame, target: next.get(frame.uid)! } : frame));
  }
  // The panel lists the cutouts page by page, each with the picture or the page it is cut from.
  const groups = useMemo(() => {
    const list: { page: number; items: { source: CutoutSource; frame: Frame }[] }[] = [];
    for (const frame of ordered) {
      const item = { source: sourceOf(frame), frame };
      const group = list.find((value) => value.page === frame.page);
      if (group) group.items.push(item); else list.push({ page: frame.page, items: [item] });
    }
    return list;
    // sourceOf depends on the pictures and the pages only.
  }, [ordered, pictures, info]);
  const hadPieces = views.some((view) => view.pieces.length);
  const snapshots = frames.filter((frame) => sourceOf(frame).kind === "page").length;
  const count = frames.length;
  const fromPictures = count - snapshots;
  const withMarks = frames.filter((frame) => marked(frame)).length;
  const snapshotText = `${snapshots} ${plural(snapshots, ["снимок", "снимка", "снимков"])} страницы`;
  const summary = !count
    ? hadPieces ? { title: "Рамок не осталось",
        detail: "Картинки вернутся на место своих вырезок, снимки страниц будут удалены. На диск всё запишется по «Сохранить»." }
      : { title: "Рамок пока нет", detail: "Обведите нужную часть страницы — перетащите по ней мышью или дважды щёлкните по картинке." }
    : fromPictures ? {
        title: `${fromPictures} ${plural(fromPictures, ["вырезка встанет", "вырезки встанут", "вырезок встанут"])} на место своих картинок` +
          (snapshots ? `, ещё ${snapshotText}` : ""),
        detail: "Исходные картинки сохранятся в разделе «Вырезки» — их можно вернуть. Файлы появятся на диске после «Сохранить»." }
    : { title: `${count} ${plural(count, ["вырезка", "вырезки", "вырезок"])} — ${count === 1 ? "снимок" : "снимки"} страницы`,
        detail: count === 1 ? "В этой области нет отдельной картинки. Файл появится на диске после «Сохранить»."
          : "В этих областях нет отдельных картинок. Файлы появятся на диске после «Сохранить»." };
  const chosen = frames.find((frame) => frame.uid === selected && frame.page === page);
  const chosenSize = chosen ? pixels(chosen) : null;
  const pages = info?.pages ?? [];
  // A page snapshot replaces no picture, so it has no place of its own to keep.
  const choices = (keep: boolean): SelectOption[] => [...(keep ? [{ value: "__keep", label: "На место картинки" }] : []),
    ...options.map((key) => ({ value: key, label: label(key), dot: dotOf(key) })), { value: "__misc", label: "Без варианта" },
    { value: "__new", label: "Новый вариант…", icon: <Plus size={14} />, separator: true }];
  function choose(uids: number[], value: string) {
    if (value === "__new") setNaming(uids); else setTarget(uids, parse(value));
  }
  const valueOf = (target: Target) => target === "keep" ? "__keep" : target === null ? "__misc" : target;
  const targetName = (target: Target) => target === "keep" ? "На место картинки" : target === null ? "Без варианта" : label(target);
  /** Where a cutout goes, as its row and its frame show it: a variant with its dot, or a plain word for the rest. */
  const targetTag = (target: Target, className: string) => (
    <span className={`${className}${target === "keep" ? " is-keep" : ""}`}>
      <span className={`variant-dot ${target === "keep" ? "is-keep" : target === null ? "is-misc" : dotOf(target)}`} aria-hidden="true" />
      <span>{targetName(target)}</span>
    </span>
  );
  const context = useContextMenu();
  /** A frame on the page or its row in the list: the row's own controls. */
  function openFrameMenu(event: ReactMouseEvent, frame: Frame) {
    if (several && picked.includes(frame.uid)) {
      // One of the frames picked: the menu acts on all of them, as «Куда выбранные» does.
      const uids = picked;
      context.open(event, {
        heading: `Выбрано: ${uids.length}`,
        items: [
          { value: "to", label: "Куда поместить", icon: <Images size={16} />,
            children: choices(true).map((choice) => ({ value: `to:${choice.value}`, label: choice.label, separator: choice.separator })) },
          { value: "remove", label: "Удалить", icon: <Trash size={16} />, danger: true, separator: true },
        ],
        onAction(value) {
          if (value.startsWith("to:")) choose(uids, value.slice(3));
          else if (value === "remove") remove(uids);
        },
      });
      return;
    }
    const source = sourceOf(frame);
    const current = valueOf(targetOf(frame, source));
    select(frame.uid);
    const items: ActionMenuItem[] = [
      { value: "show", label: "Показать", icon: <LocateFixed size={16} /> },
      { value: "to", label: "Куда поместить", icon: <Images size={16} />,
        children: choices(source.kind === "image").map((choice) => ({
          value: `to:${choice.value}`, label: choice.value === current ? `${choice.label} · сейчас` : choice.label,
          separator: choice.separator, disabled: choice.value === current })) },
    ];
    if (hasMarks(frame, source))
      items.push({ value: "marks", label: marked(frame, source) ? "Без пометок" : "С пометками", icon: <Shapes size={16} /> });
    items.push({ value: "remove", label: "Удалить", icon: <Trash size={16} />, danger: true, separator: true });
    context.open(event, {
      heading: `Вырезка ${numberOf.get(frame.uid)} · стр. ${frame.page}`,
      items,
      onAction(value) {
        if (value === "show") show(frame);
        else if (value.startsWith("to:")) choose([frame.uid], value.slice(3));
        else if (value === "marks") setMarks(frame.uid, !marked(frame, source));
        else if (value === "remove") remove([frame.uid]);
      },
    });
  }
  const parse = (value: string): Target => value === "__keep" ? "keep" : value === "__misc" ? null : value;

  return (
    <Dialog title="Вырезки со страниц PDF" wide className="cut-editor cutout-editor" onClose={requestClose}
      icon={<Crop size={22} strokeWidth={2.75} absoluteStrokeWidth />}
      headerActions={onHelp && <button className="icon-btn cut-editor-help" aria-label="Справка: Вырезки" title="Как пользоваться: Вырезки"
        onClick={onHelp}><CircleHelp size={18} /></button>}
      subtitle={<p className="cut-editor-subtitle"><span>{title}</span>
        {!!pages.length && <><i aria-hidden="true" /><span>{pages.length} {plural(pages.length, ["страница", "страницы", "страниц"])}</span></>}</p>}>
      {context.menu}
      {!info ? (
        <div className="cutout-loading" role="status">
          {failure ? <><TriangleAlert size={32} /><strong>Страницы PDF недоступны</strong><p>{failure}</p>
            <button className="btn secondary" onClick={onClose}>Закрыть</button></>
            : <><Scan size={32} /><span>Открываю страницы PDF…</span></>}
        </div>
      ) : (
      <div className="cut-editor-body" onKeyDown={keyDown}>
        <div className="cut-tools" role="toolbar" aria-label="Инструменты вырезки">
          <div className="cutout-pager" role="group" aria-label="Страница">
            <button aria-label="Предыдущая страница" disabled={page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft {...LINE} /></button>
            <b>{page}</b><span>из {pages.length}</span>
            <button aria-label="Следующая страница" disabled={page >= pages.length} onClick={() => setPage(page + 1)}><ChevronRight {...LINE} /></button>
          </div>
          <span className="cut-divider" />
          <div className="cutout-zoom" role="group" aria-label="Масштаб">
            <button aria-label="Уменьшить" disabled={zoom <= ZOOMS[0]} onClick={() => setZoom([...ZOOMS].reverse().find((value) => value < zoom - 0.01) ?? ZOOMS[0])}><Minus {...LINE} /></button>
            <b>{Math.round(zoom * 100)}%</b>
            <button aria-label="Увеличить" disabled={zoom >= ZOOMS.at(-1)!} onClick={() => setZoom(ZOOMS.find((value) => value > zoom + 0.01) ?? ZOOMS.at(-1)!)}><Plus {...LINE} /></button>
          </div>
          <button className="btn text-action" onClick={() => { setZoom(1); scroller.current?.scrollTo(0, 0); }}><Maximize {...LINE} />Вместить</button>
          <span className="cut-divider" />
          <label className="cut-toggle">
            <Images {...LINE} />Найденные картинки
            <input type="checkbox" checked={showPictures} onChange={(event) => setShowPictures(event.target.checked)} />
            <span aria-hidden="true" />
          </label>
          <span className="cut-divider" />
          <label className="cut-toggle">
            <Magnet {...LINE} />Прилипать к краям
            <input type="checkbox" checked={snapping} onChange={(event) => setSnapping(event.target.checked)} />
            <span aria-hidden="true" />
          </label>
          <span className="cut-spacer" />
          <button className="btn text-action" disabled={!onPage.length} onClick={() => remove(onPage.map((frame) => frame.uid))}>
            <SquareDashed {...LINE} />Удалить все рамки</button>
        </div>
        <div className="cut-workspace">
          <nav className="cutout-pages" aria-label="Страницы">
            <h3>Страницы <span>{pages.length}</span></h3>
            <ol>
              {pages.map((value) => {
                const here = frames.filter((frame) => frame.page === value.number).length;
                return (
                  <li key={value.number}>
                    <button className={value.number === page ? "is-current" : ""} aria-current={value.number === page ? "page" : undefined}
                      aria-label={`Страница ${value.number}${here ? `, вырезок: ${here}` : ""}`} onClick={() => setPage(value.number)}>
                      <span className="cutout-sheet" style={{ aspectRatio: `${value.size[0]} / ${value.size[1]}` }}>
                        <img src={pageUrl(resultId, value.number, true)} alt="" loading="lazy" draggable={false} />
                      </span>
                      <span className="cutout-sheet-caption">{value.number}{!!here && <b>{here}</b>}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>
          <div className="cutout-center">
            <div className={`cutout-scroller${zoom > 1 ? " is-zoomed" : ""}`} ref={scroller}>
              <div className="cutout-stage" ref={stage} tabIndex={0}
                aria-label={`Страница ${page}: перетащите мышью, чтобы обвести часть страницы`}
                style={{ width: stageWidth, height: stageHeight }}
                onPointerDown={startDraw} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}
                onDoubleClick={wholePicture}>
                <img key={page} src={pageUrl(resultId, page)} alt="" draggable={false} />
                {showPictures && pagePictures.map((picture) => {
                  const chip = (picture.source !== null && !isLive(picture.source) ? "Исходная картинка" : "Найденная картинка") +
                    (picture.variantLabel ? ` · ${picture.variantLabel}` : "");
                  // A long caption is cut to the width of its picture; the whole of it shows on hover.
                  return (
                    <span key={picture.key} className="cutout-found" style={relative(picture.area, [0, 0, 1, 1])}>
                      <span className="collage-chip dark" title={chip}><ImageIcon size={12} /><span>{chip}</span></span>
                    </span>
                  );
                })}
                {onPage.map((frame) => {
                  const active = frame.uid === selected;
                  const source = sourceOf(frame);
                  const target = targetOf(frame, source);
                  // A frame sent somewhere other than where it goes by itself says where, if it has room for a word.
                  const chosenTarget = target !== (source.kind === "image" ? "keep" : null);
                  const roomy = (frame.rect[2] - frame.rect[0]) * stageWidth >= 56 && (frame.rect[3] - frame.rect[1]) * stageHeight >= 32;
                  return (
                    <div key={frame.uid} className={`cutout-frame${picked.includes(frame.uid) ? " is-selected" : ""}`}
                      style={relative(frame.rect, [0, 0, 1, 1])} role="button" tabIndex={-1}
                      aria-label={`Вырезка ${numberOf.get(frame.uid)} — ${targetName(target)}`} aria-pressed={picked.includes(frame.uid)}
                      onPointerDown={(event) => startMove(event, frame)} onContextMenu={(event) => openFrameMenu(event, frame)}>
                      <b>{numberOf.get(frame.uid)}</b>
                      {chosenTarget && roomy && targetTag(target, "cutout-frame-target")}
                      {active && HANDLES.map((handle) => (
                        <span key={handle} className={`cutout-handle ${handle}`} aria-hidden="true"
                          onPointerDown={(event) => startMove(event, frame, handle)} />
                      ))}
                    </div>
                  );
                })}
                {drawing && <span className="cutout-frame is-drawing" style={relative(drawing, [0, 0, 1, 1])} aria-hidden="true" />}
                {chosen && chosenSize && (
                  // Over the frame, or under it at the top of the page, so it covers no other frame beside it.
                  <div className={`cut-callout cutout-callout ${chosen.rect[1] > 0.1 ? "above" : "below"}`}
                    style={{ ...(chosen.rect[1] > 0.1 ? { bottom: `${(1 - chosen.rect[1]) * 100}%` } : { top: `${chosen.rect[3] * 100}%` }),
                      ...(chosen.rect[0] > 0.55 ? { right: `${(1 - chosen.rect[2]) * 100}%` } : { left: `${chosen.rect[0] * 100}%` }) }}>
                    <b>{chosenSize[0]} × {chosenSize[1]} px</b>
                    <span>{sourceOf(chosen).kind === "page" ? "снимок страницы" : snapped ? "край прилип"
                      : marked(chosen) ? "с пометками" : "из картинки"}</span>
                  </div>
                )}
              </div>
            </div>
            {/* As wide as the page, but never narrower than a line of hints: hints squeezed to a small page
                grew tall and took the height the page needed, down to a page of one pixel. */}
            <div className="cut-hints" style={{ maxWidth: Math.max(stageWidth, Math.min(bounds.width, 560)) }}>
              <p>Потяните мышью — новая рамка · Двойной щелчок по картинке — рамка по её краям · Delete — удалить выбранные</p>
              <p>← ↑ → ↓ — сдвиг на 1 px, Shift — на 10 px · Ctrl + колесо — масштаб · Ctrl+Z — отменить шаг</p>
              <p className="cut-note" role="status">{note}</p>
            </div>
          </div>
          <aside className="cut-panel cutout-panel" aria-label="Вырезки">
            <div className="cut-panel-head">
              <h3>Вырезки <b>{count}</b></h3>
              {!!count && <>
                <div className="cut-bulk"><span>{several ? `Куда выбранные (${picked.length}):` : "Куда все:"}</span>
                  <Select label={several ? "Куда поместить выбранные вырезки" : "Куда поместить все вырезки"} value=""
                    options={choices(true)} onChange={(value) => choose(bulk, value)} />
                </div>
                <button className="cut-subvariants" onClick={eachToSubvariant}>
                  Или: каждую{several ? " выбранную" : ""} — в свой подвариант {nextSubvariant(options, base)}…
                </button>
              </>}
              {/* Keeps its line either way, so the list under it does not jump. */}
              {count > 1 && <p className={`cutout-pick-hint${several ? "" : " is-tip"}`}>{several
                ? <>Выбрано {picked.length} {plural(picked.length, ["вырезка", "вырезки", "вырезок"])}
                    <button className="cut-subvariants" onClick={() => select(null)}>Снять выбор</button></>
                : "Ctrl или Shift + щелчок — выбрать несколько"}</p>}
            </div>
            <div className="cutout-list" ref={list}>
              {!count && <p className="cutout-list-empty">Рамок пока нет. Обведите часть страницы или дважды щёлкните по картинке.</p>}
              {groups.map(({ page: sheet, items }) => (
                <section key={sheet} className="cutout-group" aria-label={`Страница ${sheet}`} data-page={sheet}>
                  <h4><b>Страница {sheet}</b><span>{items.length} {plural(items.length, ["вырезка", "вырезки", "вырезок"])}</span></h4>
                  {items.map(({ source, frame }) => {
                    const box = rectBox(frame.rect, source.area, source.size);
                    const number = numberOf.get(frame.uid);
                    const small = box[2] - box[0] < MIN_PIXELS || box[3] - box[1] < MIN_PIXELS;
                    const target = targetOf(frame, source);
                    const isPicked = picked.includes(frame.uid);
                    // Only the frame in hand picked alone opens its row; picked with others, rows stay short.
                    const open = frame.uid === selected && !several;
                    const tall = (box[2] - box[0]) / Math.max(1, box[3] - box[1]) < (open ? 0.56 : 1);
                    return (
                      <div key={frame.uid} className={`cutout-row${isPicked ? " is-selected" : ""}${open ? " is-open" : ""}`}
                        data-uid={frame.uid} onContextMenu={(event) => openFrameMenu(event, frame)}>
                        {/* The whole row shows its cutout on the page; the controls of an open row lie over it. */}
                        <button type="button" className="cutout-row-hit" aria-pressed={isPicked}
                          aria-label={`Вырезка ${number}, стр. ${frame.page} — ${targetName(target)}`}
                          title={source.kind === "image" ? `Из картинки «${source.title}»` : undefined}
                          onClick={(event) => pick(frame, event)} />
                        <span className={`cut-thumb${tall ? " is-tall" : ""}`}>
                          {source.preview && <CroppedImage src={source.preview} size={source.size} box={box} alt="" />}
                        </span>
                        <div>
                          <strong>Вырезка {number} <span>стр. {frame.page}</span>
                            {small && !open && <TriangleAlert className="cutout-warning" size={13} aria-label={`Меньше ${MIN_PIXELS} px`} />}</strong>
                          {open ? <>
                            <span>{box[2] - box[0]} × {box[3] - box[1]} px · {source.kind === "page" ? "снимок страницы"
                              : marked(frame, source) ? "с пометками" : "из картинки"}</span>
                            {small && <span className="cutout-warning"><TriangleAlert size={13} />Меньше {MIN_PIXELS} px — увеличьте рамку</span>}
                            {hasMarks(frame, source) && <label className="cut-toggle cutout-marks">
                              <Shapes {...LINE} />С пометками
                              <input type="checkbox" checked={marked(frame, source)} aria-label={`Вырезка ${number} с пометками поверх картинки`}
                                onChange={(event) => setMarks(frame.uid, event.target.checked)} />
                              <span aria-hidden="true" />
                            </label>}
                            <Select label={`Куда поместить вырезку ${number}`} value={valueOf(target)}
                              options={choices(source.kind === "image")} onChange={(value) => choose([frame.uid], value)} />
                            <div className="cutout-row-actions">
                              <button className="text-action" onClick={() => remove([frame.uid])}><Trash {...LINE} />Удалить</button>
                            </div>
                          </> : targetTag(target, "cutout-target")}
                        </div>
                      </div>
                    );
                  })}
                </section>
              ))}
              {!!snapshots && <div className="cutout-kind-note"><ImageIcon {...LINE} />
                <p><b>Снимки страницы PDF</b>Здесь нет отдельных картинок — будут добавлены выбранные области страницы.</p></div>}
              {fromPictures > withMarks && <div className="cutout-kind-note"><Crop {...LINE} />
                <p><b>Вырезки из картинок</b>Встанут на место своих картинок в исходных пикселях; сами картинки сохранятся в разделе «Вырезки».</p></div>}
              {!!withMarks && <div className="cutout-kind-note"><Shapes {...LINE} />
                <p><b>Вырезки с пометками</b>Как на странице: фото вместе с тем, что нарисовано поверх, в разрешении самой картинки. Чтобы взять чистое фото, выключите «С пометками».</p></div>}
            </div>
          </aside>
        </div>
        <div className="cut-footer">
          <ImageIcon {...LINE} />
          <p><b>{summary.title}</b><span>{summary.detail}</span></p>
          <button className="btn secondary" onClick={requestClose}>Отмена</button>
          {/* Without frames and earlier pieces there is nothing to apply. */}
          <button className="btn primary" disabled={!count && !hadPieces} onClick={apply}>
            <Crop {...LINE} />{hadPieces || !count ? "Применить" : `Добавить ${count} ${plural(count, ["вырезку", "вырезки", "вырезок"])}`}
          </button>
        </div>
      </div>
      )}
      <Presence>{naming && <NewVariantDialog variants={options} labelOf={label} preferred={base} onClose={() => setNaming(null)}
        subject={naming.length === 1 ? `Вырезка ${numberOf.get(naming[0])} встанет в новый вариант.`
          : naming.length === frames.length ? "Все вырезки встанут в новый вариант."
          : `${naming.length} ${plural(naming.length, ["вырезка встанет", "вырезки встанут", "вырезок встанут"])} в новый вариант.`}
        onCreate={(key, name) => { if (name) setNames({ ...names, [key]: name }); setTarget(naming, key); setNaming(null); }} />}</Presence>
      <Presence>{askClose && <DiscardDialog what="Нарисованные рамки и выбранные варианты вырезок"
        onStay={() => setAskClose(false)} onDiscard={() => { setAskClose(false); onClose(); }} />}</Presence>
    </Dialog>
  );
}
