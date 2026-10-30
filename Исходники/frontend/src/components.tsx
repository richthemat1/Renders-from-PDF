import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent, PropsWithChildren, ReactNode, RefObject } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Layers3, Menu, PanelLeftClose, PanelLeftOpen, X, Check, ChevronDown, ChevronRight, ArrowLeft, LoaderCircle, Pencil, Plus } from "lucide-react";
import { noticeParts } from "./api";
import { APP_ICON_AVAILABLE } from "./app-icon.generated";
import { DIALOG_MOTION, MENU_MOTION, TOAST_MOTION, useAnimatedOpen, useMotion, usePresence, useRefreshMotion } from "./motion";
import type { Notice } from "./types";
import { nextSubvariant, nextVariant, variantKey, variantParent, VARIANT_KEY_ERROR } from "./editor";
import { clampSidebarWidth, setSidebarPreferences, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, SIDEBAR_RAIL_WIDTH,
  useSidebarPreferences } from "./sidebar";
import "./sidebar-visual.css";

function canScroll(node: HTMLElement, dx: number, dy: number) {
  const style = getComputedStyle(node);
  const scrolls = (overflow: string) => /auto|scroll|overlay/.test(overflow);
  return (dy !== 0 && scrolls(style.overflowY) && (dy < 0 ? node.scrollTop > 0
      : node.scrollTop + node.clientHeight < node.scrollHeight - 1))
    || (dx !== 0 && scrolls(style.overflowX) && (dx < 0 ? node.scrollLeft > 0
      : node.scrollLeft + node.clientWidth < node.scrollWidth - 1));
}

/** The wheel over a panel scrolls only the panel's own lists, never the page behind it. */
export function useContainedWheel(ref: RefObject<HTMLElement | null>, active = true) {
  useEffect(() => {
    const panel = ref.current;
    if (!panel || !active) return;
    const onWheel = (event: WheelEvent) => {
      // Ctrl+wheel belongs to zoom (the preview and editors handle it themselves).
      if (event.ctrlKey) return;
      for (let node = event.target as Element | null; node; node = node.parentElement) {
        if (node instanceof HTMLElement && canScroll(node, event.deltaX, event.deltaY)) return;
        if (node === panel) break;
      }
      event.preventDefault();
    };
    panel.addEventListener("wheel", onWheel, { passive: false });
    return () => panel.removeEventListener("wheel", onWheel);
  }, [ref, active]);
}

export function Shell({
  navigation,
  footer,
  header,
  children,
  version,
}: PropsWithChildren<{
  navigation: ReactNode;
  footer: ReactNode;
  /** Search and actions of the page. Without them there is no top bar: the page shows its own title,
   * and a narrow window keeps only the menu button there. */
  header?: ReactNode;
  version?: string;
}>) {
  const nav = useRef<HTMLElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const sidebarId = useId();
  const preferences = useSidebarPreferences();
  const [viewport, setViewport] = useState(() => innerWidth);
  const mobile = viewport <= 960;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const drag = useRef<{
    pointerId: number;
    startX: number;
    startWidth: number;
    width: number;
    element: HTMLDivElement;
  } | null>(null);
  const width = clampSidebarWidth(dragWidth ?? preferences.width, viewport);
  const maxWidth = clampSidebarWidth(SIDEBAR_MAX_WIDTH, viewport);
  // The saved «hidden» preference now means a rail of section icons, never a missing menu.
  const collapsed = !mobile && preferences.hidden;
  const visible = mobile ? drawerOpen : true;
  const space = mobile ? 0 : collapsed ? SIDEBAR_RAIL_WIDTH : width;
  const collapseLabel = collapsed ? "Развернуть меню" : "Свернуть меню";
  const [tip, setTip] = useState<{ text: string; count: string; top: number; left: number } | null>(null);
  const tipOwner = useRef<HTMLElement | null>(null);
  const scrim = useRef<HTMLButtonElement>(null);
  useContainedWheel(nav);
  useContainedWheel(scrim, mobile && drawerOpen);

  function hideTip() {
    const owner = tipOwner.current;
    if (owner?.dataset.nativeTitle !== undefined) {
      owner.title = owner.dataset.nativeTitle;
      delete owner.dataset.nativeTitle;
    }
    tipOwner.current = null;
    setTip(null);
  }
  function showTip(target: EventTarget | null) {
    const owner = target instanceof Element ? target.closest<HTMLElement>("button") : null;
    if (!collapsed || !owner || !nav.current?.contains(owner)) return hideTip();
    if (owner === tipOwner.current) return;
    hideTip();
    const count = owner.querySelector(":scope > b")?.textContent?.trim() ?? "";
    const text = owner.dataset.tooltip || owner.querySelector(".nav-text")?.textContent?.trim()
      || owner.getAttribute("aria-label") || owner.title || owner.textContent?.replace(count, "").trim() || "";
    if (!text) return;
    // The custom popup replaces the slow native tooltip while the rail is collapsed.
    if (owner.title) {
      owner.dataset.nativeTitle = owner.title;
      owner.removeAttribute("title");
    }
    tipOwner.current = owner;
    const rect = owner.getBoundingClientRect();
    setTip({ text, count, top: rect.top + rect.height / 2, left: rect.right + 10 });
  }

  function closeDrawer() {
    setDrawerOpen(false);
    toggle.current?.focus({ preventScroll: true });
  }
  function finishResize(event: ReactPointerEvent<HTMLDivElement>, cancelled = false) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    drag.current = null;
    if (!cancelled && current.width !== current.startWidth)
      setSidebarPreferences({ width: current.width });
    setDragWidth(null);
    if (current.element.hasPointerCapture(event.pointerId))
      current.element.releasePointerCapture(event.pointerId);
  }
  useEffect(() => {
    const onResize = () => {
      setViewport(innerWidth);
      setDrawerOpen(false);
      const current = drag.current;
      if (current) {
        drag.current = null;
        setDragWidth(null);
        if (current.element.hasPointerCapture(current.pointerId))
          current.element.releasePointerCapture(current.pointerId);
      }
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      const current = drag.current;
      if (current?.element.hasPointerCapture(current.pointerId))
        current.element.releasePointerCapture(current.pointerId);
      drag.current = null;
    };
  }, []);
  useLayoutEffect(() => {
    document.documentElement.style.setProperty("--sidebar-space", `${space}px`);
    return () => { document.documentElement.style.removeProperty("--sidebar-space"); };
  }, [space]);
  useLayoutEffect(() => {
    if (!visible && nav.current?.contains(document.activeElement))
      toggle.current?.focus({ preventScroll: true });
  }, [visible]);
  useEffect(() => {
    if (!collapsed) hideTip();
  }, [collapsed]);
  useEffect(() => {
    if (!mobile || !drawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        closeDrawer();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [mobile, drawerOpen]);
  useEffect(() => {
    if (!APP_ICON_AVAILABLE) return;
    const link = document.createElement("link");
    link.rel = "icon";
    link.type = "image/x-icon";
    link.href = `${import.meta.env.BASE_URL}app-icon.ico`;
    document.head.appendChild(link);
    return () => link.remove();
  }, []);
  return (
    <div className={`app-shell${collapsed ? " is-sidebar-collapsed" : ""}${dragWidth !== null ? " is-resizing-sidebar" : ""}`}
      style={{ "--sidebar-width": `${collapsed ? SIDEBAR_RAIL_WIDTH : width}px` } as CSSProperties}>
      <aside id={sidebarId} className={`sidebar${mobile && drawerOpen ? " is-open" : ""}`} ref={nav}
        inert={!visible} aria-hidden={!visible} onClick={(event) => {
          if (mobile && (event.target as Element).closest("button")) closeDrawer();
          else hideTip();
        }}
        onPointerOver={event => showTip(event.target)} onPointerLeave={hideTip}
        onFocus={event => showTip(event.target)} onBlur={hideTip} onScroll={hideTip}>
        <div className="brand">
          <span className={`brand-mark${APP_ICON_AVAILABLE ? " custom-logo" : ""}`}>
            {APP_ICON_AVAILABLE ? <img src={`${import.meta.env.BASE_URL}app-icon.ico`} alt="" width={36} height={36} /> : <Layers3 size={22} />}
          </span>
          <div className="brand-copy">
            <strong>Фото из PDF</strong>
            <span>Материалы в одном месте.</span>
          </div>
          <button
            className="mobile-only icon-btn"
            aria-label="Закрыть меню"
            onClick={closeDrawer}
          >
            <X size={18} />
          </button>
          {!mobile && <button className="icon-btn sidebar-collapse" aria-label={collapseLabel}
            title={collapseLabel} aria-controls={sidebarId} aria-expanded={!collapsed}
            onClick={() => setSidebarPreferences({ hidden: !collapsed })}>
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>}
        </div>
        <nav aria-label="Навигация">
          {navigation}
        </nav>
        <div className="sidebar-utilities">
          <div className="sidebar-bottom">{footer}</div>
          <div className="sidebar-footer">
            <span className="online-dot" />
            Обработка на вашем компьютере
          </div>
          {version && <div className="sidebar-version">Версия {version}</div>}
        </div>
        {!mobile && !collapsed && <div className="sidebar-resizer" role="separator" tabIndex={0}
          aria-label="Ширина бокового меню" aria-controls={sidebarId} aria-orientation="vertical"
          aria-valuemin={SIDEBAR_MIN_WIDTH} aria-valuemax={maxWidth} aria-valuenow={width}
          aria-valuetext={`${width} пикселей`}
          title="Перетащите границу или используйте стрелки. Двойной щелчок — исходная ширина."
          onPointerDown={event => {
            if (event.button !== 0) return;
            event.preventDefault();
            event.currentTarget.focus({ preventScroll: true });
            event.currentTarget.setPointerCapture(event.pointerId);
            drag.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: width,
              width, element: event.currentTarget };
            setDragWidth(width);
          }}
          onPointerMove={event => {
            const current = drag.current;
            if (!current || current.pointerId !== event.pointerId) return;
            current.width = clampSidebarWidth(current.startWidth + event.clientX - current.startX, viewport);
            setDragWidth(current.width);
          }}
          onPointerUp={event => finishResize(event)}
          onPointerCancel={event => finishResize(event, true)}
          onLostPointerCapture={event => finishResize(event, true)}
          onDoubleClick={() => setSidebarPreferences({ width: SIDEBAR_MIN_WIDTH })}
          onKeyDown={event => {
            const next = event.key === "ArrowLeft" ? width - 16
              : event.key === "ArrowRight" ? width + 16
              : event.key === "Home" ? SIDEBAR_MIN_WIDTH
              : event.key === "End" ? maxWidth : null;
            if (next === null) return;
            event.preventDefault();
            event.stopPropagation();
            setSidebarPreferences({ width: clampSidebarWidth(next, viewport) });
          }}
        />}
      </aside>
      <button ref={scrim} className="sidebar-scrim" aria-label="Закрыть боковую панель"
        tabIndex={-1} aria-hidden={!mobile || !drawerOpen} onClick={closeDrawer} />
      <div className="workspace-main">
        {(header || mobile) && <header className={`topbar${header ? "" : " is-bare"}`}>
          <div className="topbar-inner">
          {mobile && <button
            ref={toggle} className="icon-btn sidebar-toggle"
            aria-label="Открыть меню" title="Открыть меню" aria-controls={sidebarId} aria-expanded={visible}
            onClick={() => setDrawerOpen(!drawerOpen)}
          >
            <Menu size={20} />
          </button>}
          {header}
          </div>
        </header>}
        {preferences.problem && <p className="sidebar-save-warning" role="status">{preferences.problem}</p>}
        {children}
      </div>
      {tip && createPortal(<div className="sidebar-tooltip" role="tooltip" style={{ top: tip.top, left: tip.left }}>
        <span>{tip.text}</span>{tip.count && <b>{tip.count}</b>}
      </div>, document.body)}
    </div>
  );
}

export function Dialog({
  title,
  subtitle,
  icon,
  headerActions,
  children,
  onClose,
  busy = false,
  wide = false,
  className = "",
}: PropsWithChildren<{
  title: string;
  subtitle?: ReactNode;
  /** A tool mark before the title, as in the page editor. */
  icon?: ReactNode;
  headerActions?: ReactNode;
  onClose(): void;
  busy?: boolean;
  wide?: boolean;
  className?: string;
}>) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  // Over the window or its backdrop the gallery behind stays where it was.
  useContainedWheel(ref);
  useLayoutEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const el = ref.current!;
    el.showModal();
    // A field marked data-autofocus takes the focus once the window is shown: React's autoFocus would run while
    // it is still closed and hidden, and showModal alone picks the close button.
    el.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    return () => {
      el.close();
      previous?.focus();
    };
  }, []);
  // Under <Presence> the window fades in and, once closed, out again; its backdrop follows in CSS (data-leaving).
  const presence = usePresence();
  useMotion(ref, presence.show, DIALOG_MOTION, presence.exited);
  const leaving = !presence.show;
  return (
    <dialog
      ref={ref}
      className={`${wide ? "dialog preview-dialog" : "dialog"} ${className}`}
      data-leaving={leaving || undefined}
      inert={leaving}
      aria-labelledby={id}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="dialog-heading">
        {icon && <span className="dialog-icon" aria-hidden="true">{icon}</span>}
        {subtitle ? <div className="dialog-heading-copy"><h2 id={id} title={title}>{title}</h2>{subtitle}</div>
          : <h2 id={id}>{title}</h2>}
        {headerActions}
        <button
          className="icon-btn"
          aria-label="Закрыть диалог"
          disabled={busy}
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

/** The notice at the bottom of the window (Figma 85:855 / 85:842): a white card with its mark and the first sentence
 * as the title. Under <Presence> it rises in and sinks away; the next notice fades into the card already shown. */
export function Toast({ notice, busy, onAction, onClose }: {
  notice: Notice; busy: boolean; onAction(): void; onClose(): void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const presence = usePresence();
  useMotion(ref, presence.show, TOAST_MOTION, presence.exited);
  useRefreshMotion(ref, notice);
  const { title, detail } = noticeParts(notice.text);
  return (
    <div ref={ref} className={`toast${notice.error ? " error" : ""}`} role={notice.error ? "alert" : "status"}>
      <span className="toast-mark" aria-hidden="true">
        {notice.error ? <AlertTriangle size={16} strokeWidth={2} absoluteStrokeWidth />
          : <Check size={16} strokeWidth={2} absoluteStrokeWidth />}
      </span>
      <div className="toast-body">
        <p className="toast-message"><strong>{title}</strong>{detail && <span>{detail}</span>}</p>
        {notice.action && <button className="toast-action" onClick={onAction} disabled={busy}>
          {notice.action.icon}{notice.action.label}</button>}
      </div>
      <button className="toast-close" aria-label="Скрыть сообщение" onClick={onClose}>
        <X size={16} strokeWidth={2} absoluteStrokeWidth />
      </button>
    </div>
  );
}

export interface ActionMenuItem {
  value: string;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
  danger?: boolean;
  separator?: boolean;
  children?: ActionMenuItem[];
}

export function ActionMenu({
  label,
  items,
  disabled = false,
  onAction,
  className = "",
  icon,
  heading,
  upwards = false,
  placementKey,
}: {
  label: string;
  items: ActionMenuItem[];
  disabled?: boolean;
  onAction(value: string): void;
  className?: string;
  icon?: ReactNode;
  heading?: string;
  upwards?: boolean;
  placementKey?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [submenu, setSubmenu] = useState<ActionMenuItem | null>(null);
  const [position, setPosition] = useState<React.CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const focusLast = useRef(false);
  const id = useId();
  const expanded = open && !disabled;
  useContainedWheel(menu, expanded);
  const menuShown = useAnimatedOpen(expanded, menu, MENU_MOTION);
  // The submenu is reset when the menu opens again, not on closing: the closing menu fades out as it was.
  function close(restoreFocus = false) {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
  }
  useLayoutEffect(() => {
    if (!expanded) return;
    function place() {
      const rect = trigger.current!.getBoundingClientRect();
      const dock = trigger.current!.closest(".edit-panel.is-floating")?.getBoundingClientRect();
      const edgeTop = upwards && dock ? dock.top : rect.top;
      const edgeBottom = upwards && dock ? dock.bottom : rect.bottom;
      const width = Math.min(Math.max(rect.width, 284), 360, innerWidth - 32);
      const below = innerHeight - edgeBottom - 20;
      const above = edgeTop - 20;
      const aboveTrigger = upwards || (below < 200 && above > below);
      setPosition({
        width,
        left: Math.max(16, Math.min(rect.left, innerWidth - width - 16)),
        top: aboveTrigger ? undefined : edgeBottom + 8,
        bottom: aboveTrigger ? innerHeight - edgeTop + 8 : undefined,
        maxHeight: Math.max(80, Math.min(400, aboveTrigger ? above : below)),
      });
    }
    place();
    const buttons = menu.current!.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not(:disabled)");
    buttons[focusLast.current ? buttons.length - 1 : 0]?.focus({ preventScroll: true });
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [expanded, submenu, placementKey, upwards]);
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node) &&
          !trigger.current?.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [expanded]);
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={`btn compact secondary action-menu-trigger ${className}`}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={expanded}
        aria-controls={expanded ? id : undefined}
        disabled={disabled || !items.length}
        onClick={() => { focusLast.current = false; setSubmenu(null); setOpen(!open); }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            event.stopPropagation();
            focusLast.current = event.key === "ArrowUp";
            if (!open) setSubmenu(null);
            setOpen(true);
          }
        }}
      >
        {icon}<span>{label}{!icon && "…"}</span><ChevronDown size={12} />
      </button>
      {menuShown && createPortal(
        <div
          ref={menu}
          id={id}
          className={`action-menu ${upwards ? "editor-action-menu" : ""}`}
          role="menu"
          aria-label={label}
          style={position}
          data-side={position.bottom !== undefined ? "top" : "bottom"}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node) &&
                event.relatedTarget !== trigger.current) close();
          }}
          onKeyDown={(event) => {
            event.stopPropagation();
            if (event.key === "Escape") { event.preventDefault(); close(true); }
            if (event.key === "Tab") { close(true); return; }
            if (event.key === "ArrowLeft" && submenu) { event.preventDefault(); setSubmenu(null); return; }
            const buttons = [...menu.current!.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not(:disabled)")];
            const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
            let next = index;
            if (event.key === "ArrowDown") next = (index + 1) % buttons.length;
            else if (event.key === "ArrowUp") next = (index - 1 + buttons.length) % buttons.length;
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = buttons.length - 1;
            else return;
            event.preventDefault();
            buttons[next]?.focus({ preventScroll: true });
            buttons[next]?.scrollIntoView({ block: "nearest" });
          }}
        >
          {submenu ? (
            <button type="button" className="menu-back" role="menuitem" tabIndex={-1}
              aria-label="Назад к действиям" onClick={() => setSubmenu(null)}>
              <ArrowLeft size={14} /><span>{submenu.label}</span>
            </button>
          ) : heading && <div className="action-menu-heading">{heading}</div>}
          {(submenu?.children ?? items).map((item) => (
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
                close(true); onAction(item.value);
              }}>
              {item.icon}<span>{item.label}</span>{item.children?.length ? <ChevronRight size={14} /> : null}
            </button>
          ))}
        </div>, trigger.current?.closest("dialog") ?? document.body,
      )}
    </>
  );
}

export interface SelectOption {
  value: string;
  label: string;
  /** A variant colour class (`color-1`…) shows a dot before the label, as in the sidebar. */
  dot?: string;
  icon?: ReactNode;
  /** A command rather than a value, such as «Новый вариант…»: set apart from the values above it. */
  separator?: boolean;
}

/** Free numbers one click away: the next whole variant, a subvariant of it and the next subvariants of nearby variants. */
export function VariantSuggestions({ variants, current, preferred, onPick }: {
  variants: string[]; current: string; preferred: string | null; onPick(value: string): void;
}) {
  const parents = [...new Set([...(preferred ? [variantParent(preferred)] : []), ...variants.map(variantParent)])];
  const next = nextVariant(variants);
  const options = [next, ...parents.slice(0, 3).map((key) => nextSubvariant(variants, key)), `${next}.1`]
    .filter((key, index, all) => all.indexOf(key) === index && !variants.includes(key));
  const chosen = variantKey(current);
  return <div className="variant-suggestions" role="group" aria-label="Свободные номера">
    {options.map((key) => <button key={key} type="button" className={`variant-suggestion${chosen === key ? " is-chosen" : ""}`}
      aria-pressed={chosen === key} onClick={() => onPick(key)}>
      {key.includes(".") ? "Подвариант" : "Вариант"} {key}
    </button>)}
  </div>;
}

/** A number for a variant not made yet, whole (4) or a subvariant (4.1), for cutouts or frames to go to. */
export function NewVariantDialog({ variants, preferred, subject, onCreate, onClose }: {
  variants: string[];
  preferred: string | null;
  /** What goes there: «Вырезка 2 встанет в новый вариант.» */
  subject: string;
  onCreate(key: string): void;
  onClose(): void;
}) {
  const [number, setNumber] = useState(() => nextVariant(variants));
  const [error, setError] = useState("");
  const input = useId();
  return (
    <Dialog title="Новый вариант" onClose={onClose}>
      <form onSubmit={(event) => {
        event.preventDefault();
        // The form sits in the editor dialog; its keys and submit stay here.
        event.stopPropagation();
        const key = variantKey(number);
        if (key === null) { setError(VARIANT_KEY_ERROR); return; }
        onCreate(key);
      }}>
        <p>{subject} Целый номер создаёт вариант, номер с точкой — подвариант. Папка и PDF появятся после сохранения.</p>
        <label htmlFor={input} className="form-label new-variant-label">Номер варианта или подварианта</label>
        <input id={input} data-autofocus className="form-input" type="text" inputMode="decimal" autoComplete="off" required
          aria-invalid={!!error} value={number} onChange={(event) => { setNumber(event.target.value); setError(""); }} />
        {error && <p className="dialog-error" role="alert">{error}</p>}
        <VariantSuggestions variants={variants} current={number} preferred={preferred}
          onPick={(value) => { setNumber(value); setError(""); }} />
        <div className="dialog-choices">
          <button className="btn primary"><Plus size={16} />{variants.includes(variantKey(number) ?? "") ? "Выбрать этот вариант" : "Создать вариант"}</button>
          <div className="dialog-alternatives">
            <button type="button" className="btn secondary" onClick={onClose}>Отмена</button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}

/** A dropdown in the style of the app menus: a field showing the choice and a list of options under it. */
export function Select({ value, options, onChange, label, placeholder = "Выберите…", disabled = false, className = "" }: {
  value: string;
  options: SelectOption[];
  onChange(value: string): void;
  label: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();
  const current = options.find((option) => option.value === value);
  const expanded = open && !disabled;
  useContainedWheel(list, expanded);
  const listShown = useAnimatedOpen(expanded, list, MENU_MOTION);
  function close(restoreFocus = false) {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
  }
  useLayoutEffect(() => {
    if (!expanded) return;
    function place() {
      const rect = trigger.current!.getBoundingClientRect();
      const below = innerHeight - rect.bottom - 16;
      const above = rect.top - 16;
      const upwards = below < 180 && above > below;
      const width = Math.min(Math.max(rect.width, 200), innerWidth - 32);
      setPosition({
        width,
        left: Math.max(16, Math.min(rect.left, innerWidth - width - 16)),
        top: upwards ? undefined : rect.bottom + 6,
        bottom: upwards ? innerHeight - rect.top + 6 : undefined,
        maxHeight: Math.max(120, Math.min(320, upwards ? above : below)),
      });
    }
    place();
    const options = list.current!.querySelectorAll<HTMLButtonElement>("[role=option]");
    const chosen = list.current!.querySelector<HTMLButtonElement>("[aria-selected=true]") ?? options[0];
    chosen?.focus({ preventScroll: true });
    chosen?.scrollIntoView({ block: "nearest" });
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [expanded]);
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => {
      if (!list.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [expanded]);
  return (
    <>
      <button ref={trigger} type="button" className={`select-field ${className}`} role="combobox" aria-label={label}
        aria-haspopup="listbox" aria-expanded={expanded} aria-controls={expanded ? id : undefined} data-value={value}
        disabled={disabled} title={current?.label}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); event.stopPropagation(); setOpen(true); }
        }}>
        {current?.dot && <span className={`variant-dot ${current.dot}`} aria-hidden="true" />}
        <span className={current ? "select-value" : "select-value is-placeholder"}>{current?.label ?? placeholder}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {listShown && createPortal(
        <div ref={list} id={id} role="listbox" aria-label={label} className="action-menu select-menu" style={position}
          data-side={position.bottom !== undefined ? "top" : "bottom"}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node) && event.relatedTarget !== trigger.current) close();
          }}
          onKeyDown={(event) => {
            event.stopPropagation();
            if (event.key === "Escape") { event.preventDefault(); close(true); return; }
            if (event.key === "Tab") { close(true); return; }
            const items = [...list.current!.querySelectorAll<HTMLButtonElement>("[role=option]")];
            const index = items.indexOf(document.activeElement as HTMLButtonElement);
            let next = index;
            if (event.key === "ArrowDown") next = Math.min(items.length - 1, index + 1);
            else if (event.key === "ArrowUp") next = Math.max(0, index - 1);
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = items.length - 1;
            else return;
            event.preventDefault();
            items[next]?.focus({ preventScroll: true });
            items[next]?.scrollIntoView({ block: "nearest" });
          }}>
          {options.map((option) => (
            <button key={option.value} type="button" role="option" tabIndex={-1} aria-selected={option.value === value}
              className={option.separator ? "menu-separated" : undefined}
              onClick={() => { close(true); if (option.value !== value) onChange(option.value); }}>
              {option.dot && <span className={`variant-dot ${option.dot}`} aria-hidden="true" />}
              {option.icon}
              <span>{option.label}</span>
              {option.value === value && <Check size={14} aria-hidden="true" />}
            </button>
          ))}
        </div>, trigger.current?.closest("dialog") ?? document.body,
      )}
    </>
  );
}

export function Empty({
  icon,
  title,
  children,
  action,
}: PropsWithChildren<{ icon: ReactNode; title: string; action?: ReactNode }>) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function StatusNote({
  children,
  busy,
  dirty = false,
}: PropsWithChildren<{ busy?: boolean; dirty?: boolean }>) {
  return (
    <span className={`status-note${dirty && !busy ? " dirty" : ""}`}>
      {busy ? <LoaderCircle size={14} className="spin" /> : dirty ? <Pencil size={14} /> : <Check size={14} />}
      {children}
    </span>
  );
}
