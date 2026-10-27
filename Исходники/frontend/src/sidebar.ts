import { useEffect, useSyncExternalStore } from "react";
import { request } from "./api";

export const SIDEBAR_MIN_WIDTH = 248;
export const SIDEBAR_MAX_WIDTH = 520;
/** Width of the collapsed menu: section icons only, names in a hover popup. */
export const SIDEBAR_RAIL_WIDTH = 72;
const STORAGE_KEY = "pdf-photos.sidebar";

interface SidebarPreferences {
  width: number;
  hidden: boolean;
}
interface SidebarState extends SidebarPreferences {
  problem: string;
}

export function clampSidebarWidth(preferred: number, viewport: number): number {
  return Math.round(Math.max(SIDEBAR_MIN_WIDTH,
    Math.min(preferred, SIDEBAR_MAX_WIDTH, viewport - 640)));
}

function parsePreferences(value: unknown): SidebarPreferences {
  const candidate = value && typeof value === "object" ? value as Partial<SidebarPreferences> : {};
  return {
    width: typeof candidate.width === "number" && Number.isFinite(candidate.width)
      ? Math.round(Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, candidate.width)))
      : SIDEBAR_MIN_WIDTH,
    hidden: candidate.hidden === true,
  };
}

let state: SidebarState | undefined;
let hydration: Promise<void> | undefined;
let revision = 0;
let pending = false;
let saving = false;
let saveAgain = false;
const listeners = new Set<() => void>();

function snapshot(): SidebarState {
  if (!state) {
    let cached: unknown;
    try { cached = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch { /* Storage can be disabled. */ }
    pending = !!cached && typeof cached === "object" && "pending" in cached && cached.pending === true;
    state = { ...parsePreferences(cached), problem: "" };
  }
  return state;
}

function publish(next: SidebarState) {
  state = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ width: next.width, hidden: next.hidden, pending }));
  } catch { /* The desktop state file still preserves preferences. */ }
  listeners.forEach(listener => listener());
}

async function savePreferences() {
  if (saving) { saveAgain = true; return; }
  saving = true;
  const sentRevision = revision;
  const { width, hidden } = snapshot();
  try {
    await request<SidebarPreferences>("sidebar", { width, hidden });
    if (sentRevision === revision) {
      pending = false;
      publish({ ...snapshot(), problem: "" });
    }
  } catch {
    publish({ ...snapshot(), problem: "Не удалось сохранить настройки меню. Следующее изменение повторит попытку." });
  } finally {
    saving = false;
    if (saveAgain || sentRevision !== revision) {
      saveAgain = false;
      void savePreferences();
    }
  }
}

export function setSidebarPreferences(change: Partial<SidebarPreferences>) {
  const next = { ...snapshot(), ...change };
  revision += 1;
  pending = true;
  publish({ ...parsePreferences(next), problem: "" });
  void savePreferences();
}

function hydrate() {
  if (hydration) return;
  const startedAt = revision;
  const replayCache = pending;
  if (replayCache) void savePreferences();
  hydration = request<{ sidebar?: SidebarPreferences }>("state")
    .then(value => {
      // A slow initial response must never undo a resize or hide action.
      if (!replayCache && revision === startedAt && value.sidebar)
        publish({ ...parsePreferences(value.sidebar), problem: "" });
    })
    .catch(() => { /* The app already reports a lost local connection. Keep the cache. */ });
}

export function useSidebarPreferences(): SidebarState {
  const value = useSyncExternalStore(
    listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    snapshot,
  );
  useEffect(() => {
    hydrate();
    const flushPending = () => {
      if (!pending) return;
      const { width, hidden } = snapshot();
      const url = new URL("api/sidebar", document.baseURI);
      const body = JSON.stringify({ width, hidden });
      // Navigation or closing the native window may abort a normal fetch.
      const sent = navigator.sendBeacon?.(url, new Blob([body], { type: "application/json" }));
      if (!sent) void fetch(url, { method: "POST", keepalive: true,
        headers: { "Content-Type": "application/json" }, body }).catch(() => {});
    };
    window.addEventListener("pagehide", flushPending);
    return () => window.removeEventListener("pagehide", flushPending);
  }, []);
  return value;
}
