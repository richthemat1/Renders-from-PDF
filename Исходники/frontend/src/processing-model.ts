import { plural } from "./api";
import type { HistoryItem, ProcessingFailure, Snapshot } from "./types";

const ENCRYPTED = /password|encrypted|парол/iu;
const DAMAGED = /no \/root|xref|invalid pdf|not a pdf|really a pdf|pdf.*(corrupt|damaged)|eof marker/iu;
// The file itself has nothing to extract: processing the same file again ends the same way.
const NO_IMAGES = /не найдено отдельных растровых изображений/iu;

export function failureMessage(item: Pick<ProcessingFailure, "reason" | "action">): string {
  // A localized explanation starts with a Russian word, a one-letter preposition («В PDF не найдено…») included;
  // Cyrillic inside a quoted filename is not one.
  const localized = /^(?:PDF\s+)?[а-яё]+(?:\s|:)/iu.test(item.reason.trim());
  if (item.action === "choose_output") return localized ? item.reason
    : "Не удалось сохранить результат. Проверьте доступ к папке и длину пути.";
  if (ENCRYPTED.test(item.reason))
    return "PDF защищён паролем. Сохраните копию без пароля и выберите её для обработки.";
  if (DAMAGED.test(item.reason))
    return "Не удалось прочитать PDF. Файл может быть повреждён. Откройте его в программе просмотра и сохраните новую копию.";
  if (NO_IMAGES.test(item.reason))
    return "В этом PDF нет растровых изображений — только текст или векторная графика. Программа извлекает растровые изображения, поэтому повторная обработка не поможет.";
  return localized ? item.reason
    : "Не удалось обработать PDF. Проверьте, открывается ли он в программе просмотра, и попробуйте сохранить новую копию.";
}

/** «Обработать заново» helps only when the cause is outside the file: a damaged, locked or empty PDF fails the same way. */
export function canRetry(item: Pick<ProcessingFailure, "reason" | "action">): boolean {
  return item.action === "choose_output" || !(ENCRYPTED.test(item.reason) || DAMAGED.test(item.reason) || NO_IMAGES.test(item.reason));
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
  // Several PDFs at once each show their own stage; a single file keeps the one line of name and stage.
  const activeFiles = outcome === "processing" ? state.job?.active_files ?? [] : [];
  const fileName = outcome === "processing" ? activeFile?.name ?? null : currentRecord?.name ?? null;
  const phase = activeFile && state.detail === activeFile.name
    ? ""
    : activeFile && state.detail.startsWith(`${activeFile.name} · `)
      ? state.detail.slice(activeFile.name.length + 3)
      : state.detail;
  const warnings = state.job?.warnings ?? (outcome === "complete" ? currentRecord?.warnings ?? 0 : 0);
  const errors = state.job?.errors ?? (outcome === "complete" ? currentRecord?.errors ?? 0 : 0);
  const progress = Math.max(0, Math.min(100, state.progress));
  // Variants are what the user takes away; PDFs of other captions are a side count.
  const variants = batch.length > 1
    ? batch.reduce((sum, item) => sum + (item.variants ?? 0), 0)
    : currentRecord?.variants ?? 0;
  const images = `${state.totals.images} ${plural(state.totals.images, ["изображение", "изображения", "изображений"])}`;
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
      ? "Можно открыть историю или результат — обработка продолжится."
      : outcome === "complete"
        ? variants > 0
          ? `${variants} ${plural(variants, ["вариант готов", "варианта готовы", "вариантов готово"])} · ${images}`
          : `${images} · ${state.totals.documents} готовых PDF`
        : state.detail,
    currentRecord,
    batch,
    activeFile,
    activeFiles: activeFiles.length > 1 ? activeFiles : [],
    fileName,
    phase,
    canOpenResult: Boolean(state.current_output),
    // A stopped queue keeps the files it did not reach, to go on without choosing them again.
    remaining: outcome === "cancelled" ? state.job?.remaining ?? 0 : 0,
    warnings,
    errors,
    failed: state.job?.failed ?? 0,
    progress,
    percentage: Math.round(progress),
  };
}
