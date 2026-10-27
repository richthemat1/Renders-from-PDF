import { plural } from "./api";
import type { Snapshot } from "./types";

type Outcome = NonNullable<Snapshot["job"]>["outcome"] | "status";

function canonicalPath(path: string): string {
  return path.replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase();
}

function legacyOutcome(state: Snapshot): Outcome {
  if (/частичн/iu.test(state.status)) return "partial";
  if (/^\s*останов/iu.test(state.status)) return "cancelled";
  if (/^\s*ошибка/iu.test(state.status)) return "failed";
  if (/^\s*готово/iu.test(state.status) || state.current_output) return "complete";
  return state.has_job || state.progress > 0 ? "status" : "idle";
}

export function processingView(state: Snapshot) {
  const outcome: Outcome = state.busy ? "processing" : state.job?.outcome ?? legacyOutcome(state);
  const currentRecord = state.current_output
    ? state.history.find((item) => canonicalPath(item.output) === canonicalPath(state.current_output!)) ?? null
    : null;
  const activeFile = outcome === "processing" ? state.job?.active_file ?? null : null;
  const fileName = outcome === "processing" ? activeFile?.name ?? null : currentRecord?.name ?? null;
  const phase = activeFile && state.detail === activeFile.name
    ? ""
    : activeFile && state.detail.startsWith(`${activeFile.name} · `)
      ? state.detail.slice(activeFile.name.length + 3)
      : state.detail;
  const warnings = state.job?.warnings ?? (outcome === "complete" ? currentRecord?.warnings ?? 0 : 0);
  const errors = state.job?.errors ?? (outcome === "complete" ? currentRecord?.errors ?? 0 : 0);
  const progress = Math.max(0, Math.min(100, state.progress));
  const titles: Record<Outcome, string> = {
    idle: "Новая обработка",
    processing: state.cancelling ? "Останавливаю обработку…" : "Готовлю ваши материалы",
    complete: "Материалы готовы",
    partial: "Обработка завершена частично",
    cancelled: "Обработка остановлена",
    failed: "Не удалось обработать PDF",
    status: state.status || "Состояние обработки",
  };
  return {
    outcome,
    showJob: outcome !== "idle",
    title: titles[outcome],
    subtitle: outcome === "processing"
      ? "Обработка на вашем компьютере"
      : outcome === "complete"
        ? `Сохранено ${state.totals.documents} PDF из ${state.totals.images} ${plural(state.totals.images, ["изображения", "изображений", "изображений"])}`
        : state.detail,
    currentRecord,
    activeFile,
    fileName,
    phase,
    canOpenResult: Boolean(state.current_output),
    warnings,
    errors,
    failed: state.job?.failed ?? 0,
    progress,
    percentage: Math.round(progress),
  };
}
