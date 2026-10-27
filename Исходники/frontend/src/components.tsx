import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent, PropsWithChildren, ReactNode } from "react";
import { createPortal } from "react-dom";
import { Layers3, Menu, PanelLeftClose, PanelLeftOpen, X, Check, ChevronDown, ChevronRight, ArrowLeft, LoaderCircle, Pencil } from "lucide-react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { APP_ICON_AVAILABLE } from "./app-icon.generated";
import { clampSidebarWidth, setSidebarPreferences, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, useSidebarPreferences } from "./sidebar";
import "./sidebar-visual.css";

gsap.registerPlugin(useGSAP);

export function Shell({
  navigation,
  footer,
  header,
  children,
  motionKey,
  version,
}: PropsWithChildren<{
  navigation: ReactNode;
  footer: ReactNode;
  header: ReactNode;
  motionKey: string;
  version?: string;
}>) {
  const scope = useRef<HTMLDivElement>(null);
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
  const visible = mobile ? drawerOpen : !preferences.hidden;
  const space = mobile || preferences.hidden ? 0 : width;
  const toggleLabel = mobile ? "Открыть меню"
    : preferences.hidden ? "Показать боковое меню" : "Скрыть боковое меню";

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
  useGSAP(
    () => {
      const media = gsap.matchMedia();
      media.add("(prefers-reduced-motion: no-preference)", () => {
        gsap.fromTo(
          ".content",
          { y: 8, opacity: 0.85 },
          {
            y: 0,
            opacity: 1,
            duration: 0.3,
            ease: "power3.out",
            clearProps: "all",
          },
        );
      });
      return () => media.revert();
    },
    { scope, dependencies: [motionKey], revertOnUpdate: true },
  );
  return (
    <div className={`app-shell${preferences.hidden ? " is-sidebar-hidden" : ""}${dragWidth !== null ? " is-resizing-sidebar" : ""}`}
      style={{ "--sidebar-width": `${width}px` } as CSSProperties} ref={scope}>
      <aside id={sidebarId} className={`sidebar${mobile && drawerOpen ? " is-open" : ""}`} ref={nav}
        hidden={!mobile && preferences.hidden} inert={!visible} aria-hidden={!visible} onClick={(event) => {
        if (mobile && (event.target as Element).closest("button")) closeDrawer();
      }}>
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
        {!mobile && !preferences.hidden && <div className="sidebar-resizer" role="separator" tabIndex={0}
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
      <button className="sidebar-scrim" aria-label="Закрыть боковую панель"
        tabIndex={-1} aria-hidden={!mobile || !drawerOpen} onClick={closeDrawer} />
      <div className="workspace-main">
        <header className="topbar">
          <div className="topbar-inner">
          <button
            ref={toggle} className="icon-btn sidebar-toggle"
            aria-label={toggleLabel} title={toggleLabel} aria-controls={sidebarId} aria-expanded={visible}
            onClick={() => mobile ? setDrawerOpen(!drawerOpen) : setSidebarPreferences({ hidden: !preferences.hidden })}
          >
            {mobile ? <Menu size={20} /> : preferences.hidden ? <PanelLeftOpen size={20} /> : <PanelLeftClose size={20} />}
          </button>
          {header}
          </div>
        </header>
        {preferences.problem && <p className="sidebar-save-warning" role="status">{preferences.problem}</p>}
        {children}
      </div>
    </div>
  );
}

export function Dialog({
  title,
  subtitle,
  headerActions,
  children,
  onClose,
  busy = false,
  wide = false,
  className = "",
}: PropsWithChildren<{
  title: string;
  subtitle?: ReactNode;
  headerActions?: ReactNode;
  onClose(): void;
  busy?: boolean;
  wide?: boolean;
  className?: string;
}>) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const el = ref.current!;
    el.showModal();
    return () => {
      el.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`${wide ? "dialog preview-dialog" : "dialog"} ${className}`}
      aria-labelledby={id}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="dialog-heading">
        {subtitle ? <div className="dialog-heading-copy"><h2 id={id}>{title}</h2>{subtitle}</div>
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
  function close(restoreFocus = false) {
    setOpen(false);
    setSubmenu(null);
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
            setOpen(true);
          }
        }}
      >
        {icon}<span>{label}{!icon && "…"}</span><ChevronDown size={12} />
      </button>
      {expanded && createPortal(
        <div
          ref={menu}
          id={id}
          className={`action-menu ${upwards ? "editor-action-menu" : ""}`}
          role="menu"
          aria-label={label}
          style={position}
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
