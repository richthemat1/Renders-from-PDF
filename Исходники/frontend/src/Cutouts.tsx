import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent } from "react";
import { ChevronLeft, ChevronRight, Crop, Image as ImageIcon, Images, LocateFixed, Magnet, Maximize, Minus, Pencil, Plus, Scan,
  SquareDashed, Trash, Trash2, TriangleAlert, Undo2 } from "lucide-react";
import type { LucideProps } from "lucide-react";
import { errorText, plural, request } from "./api";
import { Dialog, NewVariantDialog, Select } from "./components";
import type { SelectOption } from "./components";
import { Presence } from "./motion";
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
  /** Live cutouts now, in reading order: card id, frame on the page and where the card sits. */
  pieces: { id: number; rect: Rect; variant: string | null }[];
  /** Where the cutouts are, or the picture was: a variant key, else null. */
  variant: string | null;
  variantLabel: string | null;
}
/** The cutouts of one source as the editor leaves them. */
export interface CutoutResult {
  source: CutoutSource;
  rects: Rect[];
  targets: Target[];
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

function sameRect(a: Rect, b: Rect): boolean {
  return a.every((value, index) => Math.abs(value - b[index]) < 1e-6);
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

function SourcePicture({ view, maxHeight }: { view: CutoutSource; maxHeight: number }) {
  const [width, height] = view.size;
  return (
    <span className="collage-picture cutout-picture" style={{ aspectRatio: `${width} / ${height}`,
      width: `min(100%, ${Math.round(maxHeight * width / height)}px)` }}>
      {view.preview ? <img src={view.preview} alt={view.title} draggable={false} loading="lazy" /> : <span className="no-preview" />}
      {view.pieces.map((piece, index) => (
        <span key={piece.id} className="collage-frame cutout-card-frame" style={relative(piece.rect, view.area)}><b>{index + 1}</b></span>
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
  return (
    <section className="collages-page cutouts-page" aria-label="Вырезки">
      <div className="library-header">
        <div><h2>Вырезки</h2><span>{count}</span></div>
        {/* An empty section offers the same button in its middle and explains itself there. */}
        {!!views.length && <button className="btn compact primary" disabled={saving} onClick={() => onOpen()}><Crop size={16} />Вырезать из страниц…</button>}
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
              <article key={view.key} className="collage-card cutout-card" aria-label={`Вырезки: ${view.title}`}>
                <button className="collage-stage" disabled={saving} onClick={() => onOpen(view.page)}
                  aria-label={`Изменить вырезки: ${view.title}, страница ${view.page}`}>
                  <SourcePicture view={view} maxHeight={220} />
                </button>
                <div className="collage-body">
                  <div className="collage-chips">
                    {view.kind === "image"
                      ? <span className="collage-chip blue"><Crop size={14} />{count} {plural(count, ["вырезка", "вырезки", "вырезок"])}</span>
                      : <span className="collage-chip amber"><Scan size={14} />Снимок страницы{count > 1 ? ` · ${count}` : ""}</span>}
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
          <button className="btn primary" disabled={saving} onClick={() => onOpen()}><Crop size={16} />Вырезать из страниц…</button>
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
}
type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
type Drag =
  | { mode: "draw"; pointer: number; start: [number, number] }
  | { mode: "move"; pointer: number; uid: number; start: [number, number]; rect: Rect; before: Frame[] | null }
  | { mode: "resize"; pointer: number; uid: number; handle: Handle; rect: Rect; before: Frame[] | null };
const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

// The page editor draws its 16 px icons with a 2 px line, as in its Figma design.
const LINE: LucideProps = { size: 16, strokeWidth: 2, absoluteStrokeWidth: true };

export function CutoutEditor({ resultId, title, views, pictureInfo, isLive, start, variants, labelOf, dotOf, onApply, onClose }: {
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
  onApply(results: CutoutResult[]): void;
  onClose(): void;
}) {
  const [info, setInfo] = useState<PagesInfo | null>(null);
  const [failure, setFailure] = useState("");
  const [page, setPage] = useState(start.page);
  const [frames, setFrames] = useState<Frame[]>(() => {
    let uid = 0;
    return views.flatMap((view) => view.pieces.map((piece) => ({ uid: ++uid, page: view.page, rect: piece.rect, origin: piece.id })));
  });
  const [history, setHistory] = useState<Frame[][]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [showPictures, setShowPictures] = useState(true);
  const [snapping, setSnapping] = useState(true);
  const [drawing, setDrawing] = useState<Rect | null>(null);
  const [snapped, setSnapped] = useState(false);
  const [note, setNote] = useState("");
  const [bounds, setBounds] = useState({ width: 0, height: 0 });
  // Frames waiting for the number of a new variant.
  const [naming, setNaming] = useState<number[] | null>(null);
  const nextUid = useRef(frames.length);
  const scroller = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
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
    if (!info || start.picture === undefined) return;
    const picture = pictures.find((value) => value.source === start.picture);
    if (!picture || frames.some((frame) => sourceOf(frame).key === picture.key)) return;
    // Opened from a picture: the frame starts at its edges, one edge in is enough to leave a plan out.
    const frame = { uid: ++nextUid.current, page: picture.page, rect: picture.area };
    setFrames((current) => [...current, frame]);
    setSelected(frame.uid);
    setNote("Рамка поставлена по краям картинки — сдвиньте край, чтобы отрезать лишнее.");
    // Only once, when the pages arrive.
  }, [info]);
  useEffect(() => { stage.current?.focus({ preventScroll: true }); }, [info]);
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
    setSelected(null);
    setNote("");
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
    setSelected(null);
  }
  function startMove(event: ReactPointerEvent, frame: Frame, handle?: Handle) {
    event.stopPropagation();
    if (event.button !== 0) return;
    stage.current?.setPointerCapture(event.pointerId);
    setSelected(frame.uid);
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
    setSelected(frame.uid);
  }
  function wholePicture(event: ReactMouseEvent<HTMLDivElement>) {
    if ((event.target as Element).closest(".cutout-frame, .cut-callout")) return;
    const [x, y] = point(event);
    const picture = sourceFor([x, y, x, y], pagePictures);
    if (!picture) return;
    const existing = onPage.find((frame) => sameRect(frame.rect, picture.area));
    if (existing) { setSelected(existing.uid); return; }
    const frame = { uid: ++nextUid.current, page, rect: picture.area };
    commit([...frames, frame]);
    setSelected(frame.uid);
  }
  function remove(uids: number[]) {
    if (!uids.length) return;
    commit(frames.filter((frame) => !uids.includes(frame.uid)));
    if (selected !== null && uids.includes(selected)) setSelected(null);
  }
  function setTarget(uids: number[], target: Target) {
    setFrames(frames.map((frame) => uids.includes(frame.uid) ? { ...frame, target } : frame));
  }
  function show(frame: Frame) {
    setPage(frame.page);
    setSelected(frame.uid);
    stage.current?.focus({ preventScroll: true });
  }
  function keyDown(event: ReactKeyboardEvent) {
    const target = event.target as Element;
    if (target.closest("select, input") || !target.closest(".cutout-stage") || target.closest("button")) return;
    if (event.key === "Enter") { event.preventDefault(); apply(); return; }
    const frame = frames.find((value) => value.uid === selected);
    if (!frame) return;
    if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); remove([frame.uid]); return; }
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
      const same = list.length === source.pieces.length && list.every((frame, index) =>
        sameRect(frame.rect, source.pieces[index].rect) && targets[index] === source.pieces[index].variant);
      if (!same) changed.push({ source, rects: list.map((frame) => frame.rect), targets });
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
    if (changed.length) onApply(changed); else onClose();
  }

  const extra = frames.map((frame) => targetOf(frame))
    .filter((value): value is string => typeof value === "string" && value !== "keep" && !variants.includes(value));
  const options = [...new Set([...variants, ...extra])].sort(compareVariants);
  const base = pictures.find((picture) => frames.some((frame) => sourceOf(frame).key === picture.key))?.variant ?? variants[0] ?? "1";
  function eachToSubvariant() {
    const used = [...options];
    const next = new Map<number, string>();
    for (const frame of ordered) {
      const key = nextSubvariant(used, base);
      used.push(key);
      next.set(frame.uid, key);
    }
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
    ...options.map((key) => ({ value: key, label: labelOf(key), dot: dotOf(key) })), { value: "__misc", label: "Без варианта" },
    { value: "__new", label: "Новый вариант…", icon: <Plus size={14} />, separator: true }];
  function choose(uids: number[], value: string) {
    if (value === "__new") setNaming(uids); else setTarget(uids, parse(value));
  }
  const valueOf = (target: Target) => target === "keep" ? "__keep" : target === null ? "__misc" : target;
  const parse = (value: string): Target => value === "__keep" ? "keep" : value === "__misc" ? null : value;

  return (
    <Dialog title="Вырезать из страниц PDF" wide className="cut-editor cutout-editor" onClose={onClose}
      icon={<Crop size={22} strokeWidth={2.75} absoluteStrokeWidth />}
      subtitle={<p className="cut-editor-subtitle"><span>{title}</span>
        {!!pages.length && <><i aria-hidden="true" /><span>{pages.length} {plural(pages.length, ["страница", "страницы", "страниц"])}</span></>}</p>}>
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
            <SquareDashed {...LINE} />Убрать рамки</button>
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
                {showPictures && pagePictures.map((picture) => (
                  <span key={picture.key} className="cutout-found" style={relative(picture.area, [0, 0, 1, 1])}>
                    <span className="collage-chip dark"><ImageIcon size={12} />
                      {picture.source !== null && !isLive(picture.source) ? "Исходная картинка" : "Найденная картинка"}
                      {picture.variantLabel ? ` · ${picture.variantLabel}` : ""}</span>
                  </span>
                ))}
                {onPage.map((frame) => {
                  const active = frame.uid === selected;
                  return (
                    <div key={frame.uid} className={`cutout-frame${active ? " is-selected" : ""}`} style={relative(frame.rect, [0, 0, 1, 1])}
                      role="button" tabIndex={-1} aria-label={`Вырезка ${numberOf.get(frame.uid)}`} aria-pressed={active}
                      onPointerDown={(event) => startMove(event, frame)}>
                      <b>{numberOf.get(frame.uid)}</b>
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
                    <span>{sourceOf(chosen).kind === "page" ? "снимок страницы" : snapped ? "край прилип" : "из картинки"}</span>
                  </div>
                )}
              </div>
            </div>
            <div className="cut-hints" style={{ maxWidth: stageWidth }}>
              <p>Потяните мышью — новая рамка · Двойной щелчок по картинке — рамка по её краям · Delete — удалить выбранную</p>
              <p>← ↑ → ↓ — сдвиг на 1 px, Shift — на 10 px · Ctrl + колесо — масштаб · Ctrl+Z — отменить шаг</p>
              <p className="cut-note" role="status">{note}</p>
            </div>
          </div>
          <aside className="cut-panel cutout-panel" aria-label="Вырезки">
            <div className="cut-panel-head">
              <h3>Вырезки <b>{count}</b></h3>
              {!!count && <>
                <div className="cut-bulk"><span>Куда все:</span>
                  <Select label="Куда поместить все вырезки" value="" options={choices(true)}
                    onChange={(value) => choose(frames.map((frame) => frame.uid), value)} />
                </div>
                <button className="cut-subvariants" onClick={eachToSubvariant}>
                  Или: каждую — в свой подвариант {nextSubvariant(options, base)}…
                </button>
              </>}
            </div>
            <div className="cutout-list">
              {!count && <p className="cutout-list-empty">Рамок пока нет. Обведите часть страницы или дважды щёлкните по картинке.</p>}
              {groups.map(({ page: sheet, items }) => (
                <section key={sheet} className="cutout-group" aria-label={`Страница ${sheet}`}>
                  <h4><b>Страница {sheet}</b><span>{items.length} {plural(items.length, ["вырезка", "вырезки", "вырезок"])}</span></h4>
                  {items.map(({ source, frame }) => {
                    const box = rectBox(frame.rect, source.area, source.size);
                    const number = numberOf.get(frame.uid);
                    const small = box[2] - box[0] < MIN_PIXELS || box[3] - box[1] < MIN_PIXELS;
                    return (
                      <div key={frame.uid} className={`cutout-row${frame.uid === selected ? " is-selected" : ""}`}
                        title={source.kind === "image" ? `Из картинки «${source.title}»` : undefined}>
                        <span className={`cut-thumb${(box[2] - box[0]) / Math.max(1, box[3] - box[1]) < 0.56 ? " is-tall" : ""}`}>
                          {source.preview && <CroppedImage src={source.preview} size={source.size} box={box} alt="" />}
                        </span>
                        <div>
                          <strong>Вырезка {number} <span>стр. {frame.page}</span></strong>
                          <span>{box[2] - box[0]} × {box[3] - box[1]} px · {source.kind === "image" ? "из картинки" : "снимок страницы"}</span>
                          {small && <span className="cutout-warning"><TriangleAlert size={13} />Меньше {MIN_PIXELS} px — увеличьте рамку</span>}
                          <Select label={`Куда поместить вырезку ${number}`} value={valueOf(targetOf(frame, source))}
                            options={choices(source.kind === "image")} onChange={(value) => choose([frame.uid], value)} />
                          <div className="cutout-row-actions">
                            <button className="text-action" onClick={() => show(frame)}><LocateFixed {...LINE} />Показать</button>
                            <button className="text-action" onClick={() => remove([frame.uid])}><Trash {...LINE} />Удалить</button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </section>
              ))}
              {!!snapshots && <div className="cutout-kind-note"><ImageIcon {...LINE} />
                <p><b>Снимки страницы PDF</b>Здесь нет отдельных картинок — будут добавлены выбранные области страницы.</p></div>}
              {!!fromPictures && <div className="cutout-kind-note"><Crop {...LINE} />
                <p><b>Вырезки из картинок</b>Встанут на место своих картинок в исходных пикселях; сами картинки сохранятся в разделе «Вырезки».</p></div>}
            </div>
          </aside>
        </div>
        <div className="cut-footer">
          <ImageIcon {...LINE} />
          <p><b>{summary.title}</b><span>{summary.detail}</span></p>
          <button className="btn secondary" onClick={onClose}>Отмена</button>
          {/* Without frames and earlier pieces there is nothing to apply. */}
          <button className="btn primary" disabled={!count && !hadPieces} onClick={apply}>
            <Crop {...LINE} />{hadPieces || !count ? "Применить" : `Добавить ${count} ${plural(count, ["вырезку", "вырезки", "вырезок"])}`}
          </button>
        </div>
      </div>
      )}
      <Presence>{naming && <NewVariantDialog variants={options} preferred={base} onClose={() => setNaming(null)}
        subject={naming.length === 1 ? `Вырезка ${numberOf.get(naming[0])} встанет в новый вариант.` : "Все вырезки встанут в новый вариант."}
        onCreate={(key) => { setTarget(naming, key); setNaming(null); }} />}</Presence>
    </Dialog>
  );
}
