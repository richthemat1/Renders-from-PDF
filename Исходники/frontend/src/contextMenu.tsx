import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, ChevronRight, ClipboardPaste, Copy, Scissors, TextSelect } from "lucide-react";
import { native } from "./api";
import { stepMenuFocus, useContainedWheel } from "./components";
import type { ActionMenuItem } from "./components";
import { MENU_MOTION, useAnimatedOpen } from "./motion";

/** What a right click offers: the same actions as the buttons and «Ещё» next to the thing, never actions of its own. */
export interface ContextMenuSpec {
  items: ActionMenuItem[];
  /** What the menu acts on: «Выбрано: 3», a card's title. */
  heading?: string;
  onAction(value: string): void;
}

const GAP = 2, EDGE = 16;

/** The menu opens just below and right of the pointer; it turns left or up where the window ends and scrolls when
 * it is taller than the room on either side. */
export function contextMenuPlace(x: number, y: number, size: { width: number; height: number },
  viewport: { width: number; height: number }) {
  let left = x + GAP;
  if (left + size.width > viewport.width - EDGE) left = x - size.width - GAP;
  left = Math.max(EDGE, Math.min(left, viewport.width - size.width - EDGE));
  const below = viewport.height - y - GAP - EDGE, above = y - GAP - EDGE;
  if (size.height <= below) return { left, top: y + GAP, maxHeight: below, side: "bottom" as const };
  if (size.height <= above) return { left, top: y - GAP - size.height, maxHeight: above, side: "top" as const };
  return { left, top: EDGE, maxHeight: viewport.height - 2 * EDGE, side: "bottom" as const };
}

interface OpenMenu extends ContextMenuSpec {
  x: number;
  y: number;
  origin: HTMLElement | null;
  /** Where the focus goes back: the control in focus inside the origin (Shift+F10 on a card's button), else the origin. */
  returnFocus: HTMLElement | null;
  container: Element;
  /** Each opening is a menu of its own: one opened again at the same point starts at its first level. */
  serial: number;
}

/**
 * A context menu for the right button, Shift+F10 and the menu key: `open(event, spec)` from an onContextMenu handler,
 * render `menu` anywhere in the component. The thing it acts on is marked with `data-context-target` while it is open.
 * A click or the wheel outside, a resize or leaving the window closes it.
 */
export function useContextMenu() {
  const [current, setCurrent] = useState<OpenMenu | null>(null);
  const [shown, setShown] = useState(false);
  const openings = useRef(0);
  const open = useCallback((event: ReactMouseEvent | MouseEvent, spec: ContextMenuSpec, target?: HTMLElement) => {
    event.preventDefault();
    event.stopPropagation();
    const origin = target ?? (event.currentTarget instanceof HTMLElement ? event.currentTarget : null);
    let { clientX: x, clientY: y } = event;
    // The keyboard opens a context menu without a pointer: it goes under the element in focus.
    if (event.button !== 2 && origin) {
      const rect = origin.getBoundingClientRect();
      x = rect.left + Math.min(24, rect.width / 2);
      y = rect.top + Math.min(rect.height - 4, 40);
    }
    const focused = document.activeElement instanceof HTMLElement && origin?.contains(document.activeElement)
      ? document.activeElement : origin;
    openings.current += 1;
    setCurrent({ ...spec, x, y, origin, returnFocus: focused, container: origin?.closest("dialog") ?? document.body,
      serial: openings.current });
    setShown(true);
  }, []);
  const close = useCallback(() => setShown(false), []);
  const menu = current && <ContextMenuPopup key={current.serial} menu={current} shown={shown} onClose={close} />;
  return { menu, open, close };
}

const TEXT_FIELD = "input:not([type]), input[type=text], input[type=search], input[type=number], textarea";

/** The text in a field the Windows way: WebView2's own menu is off in the app, so a field would get nothing. */
export function useTextFieldMenu() {
  const { menu, open } = useContextMenu();
  useEffect(() => {
    const show = (event: MouseEvent) => {
      const field = (event.target as Element | null)?.closest?.<HTMLInputElement | HTMLTextAreaElement>(TEXT_FIELD);
      if (!field || field.disabled) return;
      // A number field has no selection range: its commands stay on.
      const selected = field.selectionStart === null || field.selectionStart !== field.selectionEnd;
      const editable = !field.readOnly;
      open(event, {
        items: [
          { value: "cut", label: "Вырезать", icon: <Scissors size={16} />, disabled: !editable || !selected },
          { value: "copy", label: "Копировать", icon: <Copy size={16} />, disabled: !selected },
          { value: "paste", label: "Вставить", icon: <ClipboardPaste size={16} />, disabled: !editable },
          { value: "all", label: "Выделить всё", icon: <TextSelect size={16} />, separator: true, disabled: !field.value },
        ],
        onAction(value) {
          field.focus({ preventScroll: true });
          if (value === "cut" || value === "copy") document.execCommand(value);
          else if (value === "all") field.select();
          else void (window.pywebview?.api ? native("clipboard_text") : navigator.clipboard.readText())
            .then((text) => {
              field.focus({ preventScroll: true });
              // As if typed: the field's own change handler and Ctrl+Z see it.
              if (text) document.execCommand("insertText", false, text);
            }, () => {});
        },
      }, field);
    };
    document.addEventListener("contextmenu", show);
    return () => document.removeEventListener("contextmenu", show);
  }, [open]);
  return menu;
}

function ContextMenuPopup({ menu, shown, onClose }: { menu: OpenMenu; shown: boolean; onClose(): void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [submenu, setSubmenu] = useState<ActionMenuItem | null>(null);
  const [place, setPlace] = useState<ReturnType<typeof contextMenuPlace> | null>(null);
  const id = useId();
  const rendered = useAnimatedOpen(shown, ref, MENU_MOTION);
  useContainedWheel(ref, shown);
  function close(restoreFocus = false) {
    onClose();
    if (restoreFocus) menu.returnFocus?.focus({ preventScroll: true });
  }
  useLayoutEffect(() => {
    const element = ref.current;
    if (!shown || !element) return;
    setPlace(contextMenuPlace(menu.x, menu.y, { width: element.offsetWidth, height: element.scrollHeight },
      { width: innerWidth, height: innerHeight }));
  }, [shown, menu, submenu]);
  useLayoutEffect(() => {
    if (shown && place) ref.current?.querySelector<HTMLButtonElement>("[role=menuitem]:not(:disabled)")?.focus({ preventScroll: true });
  }, [shown, place]);
  useEffect(() => {
    const origin = menu.origin;
    if (!shown) return;
    origin?.setAttribute("data-context-target", "");
    const outside = (event: Event) => {
      if (!ref.current?.contains(event.target as Node)) close();
    };
    const away = () => close();
    document.addEventListener("pointerdown", outside, true);
    // The wheel outside the menu, not any scroll: the page also shifts by itself (scroll anchoring when the
    // editor panel docks), and that must not take the menu away from under the pointer.
    document.addEventListener("wheel", outside, { capture: true, passive: true });
    window.addEventListener("resize", away);
    window.addEventListener("blur", away);
    return () => {
      origin?.removeAttribute("data-context-target");
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("wheel", outside, { capture: true });
      window.removeEventListener("resize", away);
      window.removeEventListener("blur", away);
    };
  }, [shown, menu]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!rendered) return null;
  return createPortal(
    <div
      ref={ref}
      id={id}
      className="action-menu context-menu"
      role="menu"
      aria-label={menu.heading ?? "Действия"}
      // Measured off screen, then placed: the first frame must not flash at the corner of the window. Not
      // `visibility: hidden` — with Windows animations off every property eases for 0.01ms, and a menu that is
      // still hidden in that frame cannot take the focus.
      style={place ? { left: place.left, top: place.top, maxHeight: place.maxHeight } : { left: -10000, top: 0 }}
      data-side={place?.side ?? "bottom"}
      onContextMenu={(event) => event.preventDefault()}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) close();
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === "Escape") { event.preventDefault(); close(true); return; }
        if (event.key === "Tab") { event.preventDefault(); close(true); return; }
        if (event.key === "ArrowLeft" && submenu) { event.preventDefault(); setSubmenu(null); return; }
        stepMenuFocus(event, ref.current!);
      }}
    >
      {submenu ? (
        <button type="button" className="menu-back" role="menuitem" tabIndex={-1}
          aria-label="Назад к действиям" onClick={() => setSubmenu(null)}>
          <ArrowLeft size={14} /><span>{submenu.label}</span>
        </button>
      ) : menu.heading && <div className="action-menu-heading" title={menu.heading}>{menu.heading}</div>}
      {(submenu?.children ?? menu.items).map((item) => (
        <button key={item.value} type="button" role="menuitem" tabIndex={-1}
          className={`${item.danger ? "menu-danger" : ""} ${item.separator ? "menu-separated" : ""}`}
          disabled={item.disabled}
          aria-haspopup={item.children?.length ? "menu" : undefined}
          onKeyDown={(event) => {
            if (event.key === "ArrowRight" && item.children?.length) {
              event.preventDefault(); setSubmenu(item);
            }
          }}
          onClick={() => {
            if (item.children?.length) { setSubmenu(item); return; }
            close(true); menu.onAction(item.value);
          }}>
          {item.icon}<span>{item.label}</span>{item.children?.length ? <ChevronRight size={14} /> : null}
        </button>
      ))}
    </div>, menu.container,
  );
}
