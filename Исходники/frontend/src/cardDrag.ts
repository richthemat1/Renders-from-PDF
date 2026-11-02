import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";
import gsap from "gsap";
import { Flip } from "gsap/Flip";
import { reducedMotion } from "./motion";

gsap.registerPlugin(Flip);

/** A card of the grid in pixels from the grid's top left corner, without the transforms of an animation. */
export interface Slot { id: number; left: number; top: number; width: number; height: number }

/** Where dragged cards land among the other cards of a grid, in reading order: before a card over its left
 * half, after it over the right half; a gap counts as the place between its cards, below the grid is the end. */
export function insertionIndex(slots: Slot[], x: number, y: number): number {
  const rows: Slot[][] = [];
  for (const slot of slots) {
    const row = rows.at(-1);
    if (row && Math.abs(row[0].top - slot.top) < 2) row.push(slot); else rows.push([slot]);
  }
  if (!rows.length || y < rows[0][0].top) return 0;
  let start = 0;
  for (const [number, row] of rows.entries()) {
    const bottom = Math.max(...row.map((slot) => slot.top + slot.height));
    const next = rows[number + 1];
    if (next ? y < (bottom + next[0].top) / 2 : y <= bottom) {
      const at = row.findIndex((slot) => x < slot.left + slot.width / 2);
      return start + (at < 0 ? row.length : at);
    }
    start += row.length;
  }
  return slots.length;
}

/** The card of a variant that cards dropped at `index` among the `shown` ones go before; undefined is its end.
 * Past the last card shown they go right after it, so cards a search hides keep their places. */
export function beforeAt(members: number[], shown: number[], dragged: Set<number>, index: number): number | undefined {
  if (index < shown.length) return shown[index];
  const last = shown.at(-1);
  return last === undefined ? undefined : members.slice(members.indexOf(last) + 1).find((id) => !dragged.has(id));
}

export type DropPlace =
  /** Among the cards of the variant shown, which make room there. */
  | { kind: "slot"; index: number }
  /** Beside a card of a variant, where the cards are not in the variant's order. */
  | { kind: "card"; id: number; after: boolean }
  /** A variant in the side menu: its end. */
  | { kind: "variant"; key: string };

export interface CardDrag {
  ids: number[];
  /** Where the cards would land now; null over nothing that takes them. */
  place: DropPlace | null;
  /** The gap the cards leave among the others while they are shown in order. */
  slot: number | null;
  /** Height of the card picked up, for that gap. */
  height: number;
}

interface Options {
  enabled: boolean;
  grid: RefObject<HTMLElement | null>;
  /** The grid shows one variant in its order: the other cards make room where the dragged ones would go. */
  reorder: boolean;
  /** The cards picked up with this one: the selection, when it holds it. */
  pick(id: number): number[];
  /** A card that takes cards beside it: one of a variant. */
  takes(id: number): boolean;
  onStart(ids: number[]): void;
  onDrop(ids: number[], place: DropPlace): void;
}

// A press becomes a drag once the pointer has moved this far, so a click still selects the card.
const THRESHOLD = 6;
// Near the top or the bottom of the page the page scrolls, up to SCROLL px a frame.
const EDGE = 72;
const SCROLL = 18;
const MENU_SCALE = 0.4;
// What a press may start on: not the controls of a card.
const CONTROLS = "input, label, textarea, select, a, .btn, .icon-btn, .asset-title button";

function scroller(element: Element): HTMLElement {
  for (let node = element.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if ((overflowY === "auto" || overflowY === "scroll") && node.scrollHeight > node.clientHeight) return node;
  }
  return document.scrollingElement as HTMLElement;
}

/** Cards picked up by the pointer, as on an iPhone home screen: the card lifts and follows the pointer, the
 * others make room for it, and it settles into its new place. */
export function useCardDrag(options: Options) {
  const [drag, setDrag] = useState<CardDrag | null>(null);
  const latest = useRef(options);
  latest.current = options;
  const live = useRef<CardDrag | null>(null);
  const flip = useRef<Flip.FlipState | null>(null);
  const ghost = useRef<HTMLElement | null>(null);
  const finish = useRef<(() => void) | null>(null);
  const stop = useRef<() => void>(() => {});

  function update(next: CardDrag | null) {
    const grid = latest.current.grid.current;
    // The cards move to make room: their places before the change, to animate from.
    if (grid && (next?.slot ?? null) !== (live.current?.slot ?? null))
      flip.current = Flip.getState(grid.querySelectorAll(".asset-card"));
    live.current = next;
    setDrag(next);
  }

  useLayoutEffect(() => {
    const state = flip.current;
    flip.current = null;
    if (state) Flip.from(state, { duration: reducedMotion() ? 0 : 0.32, ease: "power3.out", simple: true });
    // The lifted copy goes once its card stands where the copy was put down.
    if (!drag && finish.current) { finish.current(); finish.current = null; }
  }, [drag]);
  useEffect(() => () => stop.current(), []);

  function startDrag(event: ReactPointerEvent, id: number) {
    const { enabled } = latest.current;
    if (!enabled || event.button !== 0 || !event.isPrimary || live.current || (event.target as Element).closest(CONTROLS)) return;
    const card = event.currentTarget as HTMLElement;
    const start = { x: event.clientX, y: event.clientY };
    let pointer = start;
    let origin: DOMRect | null = null;
    let follow: { x: gsap.QuickToFunc; y: gsap.QuickToFunc } | null = null;
    let frame = 0;
    let nudge = 0;
    const quick = reducedMotion() ? 0 : 1;

    function placeAt(x: number, y: number, current: CardDrag): Pick<CardDrag, "place" | "slot"> {
      const { reorder, grid, takes } = latest.current;
      const under = document.elementFromPoint(x, y);
      const variant = under?.closest<HTMLElement>("[data-drop-variant]")?.dataset.dropVariant;
      if (variant !== undefined) return { place: { kind: "variant", key: variant }, slot: current.slot };
      const box = grid.current?.getBoundingClientRect();
      if (reorder && grid.current && box) {
        // Off the grid (over the header or the menu) the gap stays where it was.
        if (x < box.left - EDGE || x > box.right + EDGE || current.slot === null && !under?.closest(".asset-grid"))
          return { place: current.slot === null ? null : { kind: "slot", index: current.slot }, slot: current.slot };
        const slots = [...grid.current.querySelectorAll<HTMLElement>(".asset-card[data-id]")].map((element) => ({
          id: Number(element.dataset.id), left: element.offsetLeft, top: element.offsetTop,
          width: element.offsetWidth, height: element.offsetHeight }));
        const index = insertionIndex(slots, x - box.left, y - box.top);
        return { place: { kind: "slot", index }, slot: index };
      }
      const target = under?.closest<HTMLElement>(".asset-card[data-id]");
      const over = target ? Number(target.dataset.id) : NaN;
      if (!target || current.ids.includes(over) || !takes(over)) return { place: null, slot: null };
      const rect = target.getBoundingClientRect();
      return { place: { kind: "card", id: over, after: x > rect.left + rect.width / 2 }, slot: null };
    }
    function retarget() {
      const current = live.current;
      if (!current) return;
      const next = placeAt(pointer.x, pointer.y, current);
      const same = JSON.stringify(next.place) === JSON.stringify(current.place) && next.slot === current.slot;
      if (same) return;
      // Over a variant of the menu the card shrinks to the right of the pointer, so the variant it goes to shows.
      const menu = next.place?.kind === "variant";
      if (ghost.current && origin && menu !== (current.place?.kind === "variant")) {
        nudge = menu ? (start.x - origin.left) * MENU_SCALE + 16 : 0;
        gsap.to(ghost.current, { scale: menu ? MENU_SCALE : 1.04, duration: 0.18 * quick, ease: "power2.out", overwrite: "auto" });
        follow?.x(pointer.x - start.x + nudge);
      }
      update({ ...current, ...next });
    }
    function begin() {
      const { pick, reorder, grid, onStart } = latest.current;
      const ids = pick(id);
      if (!ids.length) return false;
      origin = card.getBoundingClientRect();
      const copy = card.cloneNode(true) as HTMLElement;
      copy.removeAttribute("data-id");
      copy.classList.add("card-drag-ghost");
      if (ids.length > 1) {
        const count = document.createElement("span");
        count.className = "card-drag-count";
        count.textContent = String(ids.length);
        copy.appendChild(count);
      }
      Object.assign(copy.style, { left: `${origin.left}px`, top: `${origin.top}px`, width: `${origin.width}px`,
        height: `${origin.height}px`, transformOrigin: `${start.x - origin.left}px ${start.y - origin.top}px` });
      // Inside the page the copy keeps the card styles; an animated ancestor may shift fixed positions.
      (card.closest(".viewer-content") ?? document.body).appendChild(copy);
      const placed = copy.getBoundingClientRect();
      copy.style.left = `${2 * origin.left - placed.left}px`;
      copy.style.top = `${2 * origin.top - placed.top}px`;
      ghost.current = copy;
      gsap.to(copy, { scale: 1.04, boxShadow: "0 22px 44px #17202b3d, 0 4px 12px #17202b24", duration: 0.18 * quick,
        ease: "power2.out" });
      follow = { x: gsap.quickTo(copy, "x", { duration: 0.14 * quick, ease: "power3" }),
        y: gsap.quickTo(copy, "y", { duration: 0.14 * quick, ease: "power3" }) };
      getSelection()?.removeAllRanges();
      document.body.classList.add("is-card-dragging");
      // In the variant's own order the cards leave a gap where they were.
      const shown = grid.current ? [...grid.current.querySelectorAll<HTMLElement>(".asset-card[data-id]")]
        .map((element) => Number(element.dataset.id)) : [];
      const slot = reorder ? shown.slice(0, shown.indexOf(id)).filter((value) => !ids.includes(value)).length : null;
      update({ ids, place: slot === null ? null : { kind: "slot", index: slot }, slot, height: origin.height });
      onStart(ids);
      frame = requestAnimationFrame(scroll);
      return true;
    }
    function scroll() {
      const box = scroller(card);
      const view = box === document.scrollingElement ? { top: 0, bottom: innerHeight } : box.getBoundingClientRect();
      const speed = pointer.y < view.top + EDGE ? -(view.top + EDGE - pointer.y) : pointer.y > view.bottom - EDGE
        ? pointer.y - view.bottom + EDGE : 0;
      if (speed) {
        const before = box.scrollTop;
        box.scrollTop += Math.round(Math.max(-SCROLL, Math.min(SCROLL, speed * SCROLL / EDGE)));
        if (box.scrollTop !== before) retarget();
      }
      frame = requestAnimationFrame(scroll);
    }
    function move(event: PointerEvent) {
      if (event.pointerId !== undefined && event.pointerId !== pointerId) return;
      pointer = { x: event.clientX, y: event.clientY };
      if (!live.current) {
        if (Math.hypot(pointer.x - start.x, pointer.y - start.y) < THRESHOLD || !begin()) return;
      }
      follow?.x(pointer.x - start.x + nudge);
      follow?.y(pointer.y - start.y);
      retarget();
    }
    function land(cancel: boolean) {
      const current = live.current;
      const copy = ghost.current;
      cleanup();
      if (!current || !copy || !origin) return;
      // The click that ends a drag must not also select or open the card.
      const swallow = (click: MouseEvent) => { click.stopPropagation(); click.preventDefault(); };
      window.addEventListener("click", swallow, { capture: true, once: true });
      setTimeout(() => window.removeEventListener("click", swallow, { capture: true }), 0);
      const place = cancel ? null : current.place;
      const grid = latest.current.grid.current;
      const to = place?.kind === "slot" ? grid?.querySelector(".drag-slot")?.getBoundingClientRect()
        : place?.kind === "variant" ? document.querySelector(`[data-drop-variant="${CSS.escape(place.key)}"]`)?.getBoundingClientRect()
        : !place ? origin : undefined;
      const done = () => {
        finish.current = () => { copy.remove(); document.body.classList.remove("is-card-dragging"); };
        if (place) latest.current.onDrop(current.ids, place);
        update(null);
      };
      gsap.killTweensOf(copy);
      if (!to) { gsap.to(copy, { opacity: 0, duration: 0.14 * quick, onComplete: done }); return; }
      // Into a menu item the copy shrinks away; into a place among the cards it settles there.
      const into = place?.kind === "variant";
      gsap.to(copy, {
        x: to.left + to.width / 2 - origin.left - origin.width / 2,
        y: to.top + to.height / 2 - origin.top - origin.height / 2,
        scale: into ? 0.2 : 1, opacity: into ? 0 : 1, boxShadow: "0 0 0 #17202b00",
        duration: (into ? 0.24 : 0.22) * quick, ease: "power3.out", onComplete: done });
    }
    function up(event: PointerEvent) { if (event.pointerId === pointerId) land(false); }
    function key(event: KeyboardEvent) {
      if (event.key !== "Escape" || !live.current) return;
      event.preventDefault();
      event.stopPropagation();
      land(true);
    }
    function cleanup() {
      cancelAnimationFrame(frame);
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", cancel);
      removeEventListener("keydown", key, true);
      stop.current = () => {};
    }
    function cancel() { land(true); }
    const pointerId = event.pointerId;
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", cancel);
    addEventListener("keydown", key, true);
    stop.current = () => { cleanup(); ghost.current?.remove(); document.body.classList.remove("is-card-dragging"); };
  }

  return { drag, startDrag };
}
