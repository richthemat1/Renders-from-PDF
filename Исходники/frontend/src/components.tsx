import { useEffect, useId, useRef } from "react";
import type { PropsWithChildren, ReactNode } from "react";
import { Layers3, Menu, X, Check, LoaderCircle } from "lucide-react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { APP_ICON_AVAILABLE } from "./app-icon.generated";

gsap.registerPlugin(useGSAP);

export function Shell({
  navigation,
  footer,
  header,
  children,
  motionKey,
}: PropsWithChildren<{
  navigation: ReactNode;
  footer: ReactNode;
  header: ReactNode;
  motionKey: string;
}>) {
  const scope = useRef<HTMLDivElement>(null);
  const nav = useRef<HTMLElement>(null);
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
    <div className="app-shell" ref={scope}>
      <aside className="sidebar" ref={nav}>
        <div className="brand">
          <span className={`brand-mark${APP_ICON_AVAILABLE ? " custom-logo" : ""}`}>
            {APP_ICON_AVAILABLE ? <img src={`${import.meta.env.BASE_URL}app-icon.ico`} alt="" width={36} height={36} /> : <Layers3 size={22} />}
          </span>
          <div>
            <strong>Фото из PDF</strong>
            <span>Ваши материалы. В одном месте.</span>
          </div>
          <button
            className="mobile-only icon-btn"
            aria-label="Закрыть меню"
            onClick={() => nav.current?.classList.remove("is-open")}
          >
            <X size={18} />
          </button>
        </div>
        <nav
          aria-label="Навигация"
          onClick={(e) => {
            if ((e.target as HTMLElement).closest("button"))
              nav.current?.classList.remove("is-open");
          }}
        >
          {navigation}
        </nav>
        <div className="sidebar-bottom">{footer}</div>
        <div className="sidebar-footer">
          <span className="online-dot" />
          Обработка на вашем компьютере
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <button
            className="icon-btn mobile-only"
            aria-label="Открыть меню"
            onClick={() => nav.current?.classList.add("is-open")}
          >
            <Menu size={20} />
          </button>
          {header}
        </header>
        {children}
      </div>
    </div>
  );
}

export function Dialog({
  title,
  children,
  onClose,
  busy = false,
  wide = false,
}: PropsWithChildren<{
  title: string;
  onClose(): void;
  busy?: boolean;
  wide?: boolean;
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
      className={wide ? "dialog preview-dialog" : "dialog"}
      aria-labelledby={id}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="dialog-heading">
        <h2 id={id}>{title}</h2>
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
}: PropsWithChildren<{ busy?: boolean }>) {
  return (
    <span className="status-note">
      {busy ? <LoaderCircle size={14} className="spin" /> : <Check size={14} />}
      {children}
    </span>
  );
}
