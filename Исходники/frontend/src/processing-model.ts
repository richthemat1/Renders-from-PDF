import { plural } from "./api";
import type { HistoryItem, ProcessingFailure, Snapshot } from "./types";

export function failureMessage(item: Pick<ProcessingFailure, "reason" | "action">): string {
  // Cyrillic inside a quoted filename is not a localized explanation.
  const localized = /^(?:PDF\s+)?[а-яё]{2,}(?:\s|:)/iu.test(item.reason.trim());
  if (item.action === "choose_output") return localized ? item.reason
    : "Не удалось сохранить результат. Проверьте доступ к папке и длину пути.";
  if (/password|encrypted|парол/iu.test(item.reason))
    return "PDF защищён паролем. Сохраните копию без пароля и выберите её для обработки.";
  if (/no \/root|xref|invalid pdf|not a pdf|really a pdf|pdf.*(corrupt|damaged)|eof marker/iu.test(item.reason))
    return "Не удалось прочитать PDF. Файл может быть повреждён. Откройте его в программе просмотра и сохраните новую копию.";
  return localized ? item.reason
    : "Не удалось обработать PDF. Проверьте, открывается ли он в программе просмотра, и попробуйте сохранить новую копию.";
}

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
  // A finished queue of several PDFs lists each result: the totals above are sums, «Открыть результат» opens the last.
  const batch = outcome !== "processing" && (state.job?.outputs?.length ?? 0) > 1
    ? state.job!.outputs!.map((output) => state.history.find((item) => canonicalPath(item.output) === canonicalPath(output)))
      .filter((item): item is HistoryItem => Boolean(item))
    : [];
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
        // Not «N PDF из M изображений»: a variant joins many images, so fewer PDFs is no loss.
        ? `${state.totals.images} ${plural(state.totals.images, ["изображение разложено", "изображения разложены", "изображений разложены"])} по ${state.totals.documents} PDF`
        : state.detail,
    currentRecord,
    batch,
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
