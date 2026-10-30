import { createContext, useContext, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { HTMLAttributes, ReactNode, RefObject } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";

gsap.registerPlugin(useGSAP);

/** How a panel comes and goes: the state it enters from and leaves to, and the two durations in seconds. */
export interface Motion {
  hidden: gsap.TweenVars;
  shown: gsap.TweenVars;
  enter: number;
  /** 0 removes the panel at once. */
  exit: number;
  /** Already open on the first render: animate in (a dialog) or just be there (a group in the menu). */
  appear?: boolean;
  /** The hidden state depends on layout the first render has not applied yet (which side a menu opens to). */
  deferred?: boolean;
}

// Opacity rather than GSAP's autoAlpha: a field in a window must take the focus while the window fades in.
export const TOAST_MOTION: Motion = {
  hidden: { opacity: 0, y: 16, scale: 0.98 }, shown: { opacity: 1, y: 0, scale: 1 }, enter: 0.34, exit: 0.22,
};
export const DIALOG_MOTION: Motion = {
  hidden: { opacity: 0, y: 12, scale: 0.98 }, shown: { opacity: 1, y: 0, scale: 1 }, enter: 0.28, exit: 0.18,
};
const opensUp = (target: HTMLElement) => target.dataset.side === "top";
export const MENU_MOTION: Motion = {
  hidden: {
    opacity: 0, scale: 0.98,
    y: (_: number, target: HTMLElement) => (opensUp(target) ? 4 : -4),
    transformOrigin: (_: number, target: HTMLElement) => (opensUp(target) ? "50% 100%" : "50% 0%"),
  },
  shown: { opacity: 1, scale: 1, y: 0 }, enter: 0.18, exit: 0.12, deferred: true,
};
export const DOCK_MOTION: Motion = {
  hidden: { opacity: 0, y: 8 }, shown: { opacity: 1, y: 0 }, enter: 0.22, exit: 0.18,
};
export const BANNER_MOTION: Motion = {
  hidden: { yPercent: 100 }, shown: { yPercent: 0 }, enter: 0.3, exit: 0.22,
};
export const COLLAPSE_MOTION: Motion = {
  hidden: { height: 0, opacity: 0 }, shown: { height: "auto", opacity: 1 }, enter: 0.26, exit: 0.2, appear: false,
};

const ANIMATED = "opacity,transform,transformOrigin,height,overflow,pointerEvents";

/** «Show animations in Windows» is off. */
export function reducedMotion() {
  return matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Plays `motion` on the element in `ref`: in whenever `show` turns true, out when it turns false, then `onExited`.
 * With reduced motion in Windows both happen at once, as the CSS animations do. */
export function useMotion(ref: RefObject<HTMLElement | null>, show: boolean, motion: Motion, onExited?: () => void) {
  const entered = useRef<HTMLElement | null>(null);
  const firstRun = useRef(true);
  const exited = useRef(onExited);
  useLayoutEffect(() => { exited.current = onExited; });
  useGSAP((context: gsap.Context) => {
    const el = ref.current;
    const appearing = firstRun.current;
    firstRun.current = false;
    if (!el) return;
    const reduced = reducedMotion();
    if (show) {
      const fresh = entered.current !== el;
      // The tweens of an earlier element (a menu closed and opened again) are over: let them go.
      if (fresh) context.clear();
      entered.current = el;
      gsap.set(el, { clearProps: "pointerEvents" });
      if (reduced || !motion.enter || (appearing && motion.appear === false)) {
        gsap.set(el, { clearProps: ANIMATED });
        return;
      }
      if ("height" in motion.hidden) gsap.set(el, { overflow: "hidden" });
      const vars = { ...motion.shown, duration: motion.enter, ease: "power3.out", overwrite: true, clearProps: ANIMATED };
      if (!fresh) gsap.to(el, vars);
      else if (!motion.deferred) gsap.fromTo(el, motion.hidden, vars);
      else {
        gsap.set(el, { opacity: 0 });
        gsap.fromTo(el, motion.hidden, { ...vars, immediateRender: false });
      }
    } else if (entered.current === el) {
      // Leaving: no clicks on what is going away.
      gsap.set(el, { pointerEvents: "none" });
      if (reduced || !motion.exit) {
        exited.current?.();
        return;
      }
      if ("height" in motion.hidden) gsap.set(el, { overflow: "hidden" });
      gsap.to(el, { ...motion.hidden, duration: motion.exit, ease: "power2.in", overwrite: true,
        onComplete: () => exited.current?.() });
    }
  }, { dependencies: [show] });
}

/** New content in a panel already on screen (the next notice): its parts fade in again instead of swapping at once. */
export function useRefreshMotion(ref: RefObject<HTMLElement | null>, content: unknown) {
  const previous = useRef(content);
  useGSAP(() => {
    if (previous.current === content) return;
    previous.current = content;
    if (!ref.current || reducedMotion()) return;
    gsap.fromTo(ref.current.children, { opacity: 0, y: 4 }, { opacity: 1, y: 0, duration: 0.24, ease: "power2.out",
      stagger: 0.03, overwrite: true, clearProps: "opacity,transform" });
  }, { dependencies: [content] });
}

const PresenceContext = createContext({ show: true, exited: () => {} });

/** Keeps a closing dialog or notice on screen until its exit has played: `<Presence>{open && <Dialog …/>}</Presence>`.
 * While it leaves, the last rendered element stays as it was. */
export function Presence({ children }: { children: ReactNode }) {
  const show = Boolean(children);
  const last = useRef(children);
  const [gone, setGone] = useState<ReactNode>(null);
  useLayoutEffect(() => { if (show) last.current = children; });
  const value = useMemo(() => ({ show, exited: () => setGone(last.current) }), [show]);
  const content = show ? children : last.current === gone ? null : last.current;
  return content ? <PresenceContext.Provider value={value}>{content}</PresenceContext.Provider> : null;
}

/** Whether the enclosing Presence still shows this panel, and the call that removes it once it has left. */
export function usePresence() {
  return useContext(PresenceContext);
}

/** For a panel a component renders itself (a menu, a group): whether it is still on screen while `motion` plays. */
export function useAnimatedOpen(open: boolean, ref: RefObject<HTMLElement | null>, motion: Motion) {
  const [rendered, setRendered] = useState(open);
  if (open && !rendered) setRendered(true);
  useMotion(ref, open, motion, () => setRendered(false));
  return open || rendered;
}

/** A plain block that plays `motion` as its Presence shows and hides it. */
export function MotionDiv({ motion, ...props }: { motion: Motion } & HTMLAttributes<HTMLDivElement>) {
  const ref = useRef<HTMLDivElement>(null);
  const presence = usePresence();
  useMotion(ref, presence.show, motion, presence.exited);
  return <div ref={ref} {...props} />;
}

/** A group that slides open and shut instead of appearing at once, and is `hidden` once shut.
 * Its class goes on an inner block, so padding does not show while the height animates. */
export function Collapsible({ open, className, children, ...props }: { open: boolean } & HTMLAttributes<HTMLDivElement>) {
  const ref = useRef<HTMLDivElement>(null);
  const shown = useAnimatedOpen(open, ref, COLLAPSE_MOTION);
  return (
    <div ref={ref} {...props} hidden={!shown}>
      {className ? <div className={className}>{children}</div> : children}
    </div>
  );
}
