import { describe, expect, it } from "vitest";
import { failureMessage, processingView } from "./processing-model";
import type { HistoryItem, Snapshot } from "./types";

const result: HistoryItem = {
  id: "result-1", name: "Сохранённая презентация.pdf", output: "C:\\PDF\\Результат",
  source: "C:\\PDF\\Презентация.pdf", images: 33, documents: 25, variants: 3,
  unsigned: 11, review: 2, errors: 0, warnings: 4, size: 100, finished_at: "2026-10-27T12:00:00", available: true, imported: false,
};

describe("processing failure recovery", () => {
  it("explains a broken PDF without recommending another output folder", () => {
    expect(failureMessage({ reason: "No /Root object! - Is this really a PDF?", action: "retry" })).toContain("Файл может быть повреждён");
  });
  it("keeps the useful localized output error and translates unknown English failures", () => {
    expect(failureMessage({ reason: "Путь слишком длинный", action: "choose_output" })).toBe("Путь слишком длинный");
    expect(failureMessage({ reason: "Permission denied", action: "choose_output" })).toContain("доступ к папке");
    expect(failureMessage({ reason: "Unexpected failure", action: "retry" })).toContain("Не удалось обработать PDF");
  });
  it("offers a readable recovery for encrypted PDFs", () => {
    expect(failureMessage({ reason: "File is encrypted with a password", action: "retry" })).toContain("копию без пароля");
  });
  it("does not mistake Cyrillic filenames for a localized technical explanation", () => {
    const reason = "[Errno 13] Permission denied: 'C:\\Результаты\\файл.pdf'";
    expect(failureMessage({ reason, action: "choose_output" })).toContain("доступ к папке");
    expect(failureMessage({ reason: "Cannot read Презентация.pdf", action: "retry" })).toContain("Не удалось обработать PDF");
  });
});

function snapshot(patch: Partial<Snapshot> = {}): Snapshot {
  return {
    version: "3.26", busy: false, has_job: false, cancelling: false, closing: false,
    status: "Ожидаю выбор файлов", detail: "Выберите PDF или перетащите файлы и папки в окно.", progress: 0,
    totals: { images: 0, documents: 0, unsigned: 0, review: 0 }, logs: [],
    settings: { mode: "nearby", folder: "", label: "Рядом с исходным PDF" }, history: [], current_output: null, warning: "",
    ...patch,
  };
}

function job(outcome: NonNullable<Snapshot["job"]>["outcome"], patch: Partial<NonNullable<Snapshot["job"]>> = {}): NonNullable<Snapshot["job"]> {
  return { outcome, total: 1, completed: 0, failed: 0, warnings: 0, errors: 0, active_file: null, ...patch };
}

describe("processing page state", () => {
  it("keeps a fresh window in file selection without a job summary", () => {
    expect(processingView(snapshot({ job: job("idle", { total: 0 }) }))).toMatchObject({ outcome: "idle", showJob: false, fileName: null });
  });

  it("lets the live busy flag override a stale completion", () => {
    const view = processingView(snapshot({ busy: true, job: job("complete", { completed: 1 }), current_output: result.output, history: [result] }));
    expect(view).toMatchObject({ outcome: "processing", title: "Готовлю ваши материалы", fileName: null, showJob: true });
  });

  it("shows the real active file and removes only its exact prefix from the phase", () => {
    const view = processingView(snapshot({ busy: true, detail: "Новый файл.pdf · Чтение заголовков · страница 2 из 4", job: job("processing", { active_file: { name: "Новый файл.pdf", index: 2, total: 3 } }) }));
    expect(view).toMatchObject({ fileName: "Новый файл.pdf", phase: "Чтение заголовков · страница 2 из 4" });
  });

  it("does not guess legacy filenames or queue sizes from localized logs and detail", () => {
    const view = processingView(snapshot({ busy: true, detail: "Новый файл.pdf · Чтение страниц", logs: [{ id: 1, level: "head", text: "Файл 1 из 1: Новый файл.pdf" }] }));
    expect(view).toMatchObject({ fileName: null, phase: "Новый файл.pdf · Чтение страниц" });
    expect(view.activeFile).toBeNull();
  });

  it("matches a finished filename by the current output path, regardless of case and slash direction", () => {
    const view = processingView(snapshot({ job: job("complete", { completed: 1 }), current_output: "c:/pdf/результат/", history: [result], logs: [{ id: 1, level: "error", text: "Последний неуспешный.pdf" }] }));
    expect(view.fileName).toBe(result.name);
    expect(view.currentRecord?.id).toBe(result.id);
  });

  it.each(["partial", "cancelled", "failed"] as const)("never labels %s as ready while an older result is retained", (outcome) => {
    const view = processingView(snapshot({ job: job(outcome), current_output: result.output, history: [result] }));
    expect(view.outcome).toBe(outcome);
    expect(view.title).not.toBe("Материалы готовы");
    expect(view.canOpenResult).toBe(true);
  });

  it("keeps a result action available when its history record is missing", () => {
    const view = processingView(snapshot({ job: job("complete", { completed: 1 }), current_output: result.output }));
    expect(view).toMatchObject({ outcome: "complete", canOpenResult: true, currentRecord: null, fileName: null });
  });

  it("uses real job warning and error counts instead of counting log rows", () => {
    const view = processingView(snapshot({ job: job("partial", { warnings: 7, errors: 3, failed: 2 }), logs: [{ id: 1, level: "warn", text: "Сводка" }] }));
    expect(view).toMatchObject({ warnings: 7, errors: 3, failed: 2 });
  });

  it.each([
    ["Готово. Обработано файлов: 1 из 1", "complete"],
    ["Готово частично. Часть файлов или изображений не сохранена", "partial"],
    ["Остановлено. Готово файлов: 0 из 1", "cancelled"],
    ["Ошибка: PDF не обработан", "failed"],
  ] as const)("reads the legacy status %s before an older current output", (status, outcome) => {
    expect(processingView(snapshot({ status, current_output: result.output })).outcome).toBe(outcome);
  });

  it("keeps an unknown interrupted legacy job neutral instead of inventing success", () => {
    expect(processingView(snapshot({ has_job: true, progress: 42, status: "Проверка состояния" }))).toMatchObject({ outcome: "status", title: "Проверка состояния", showJob: true, canOpenResult: false });
  });
});
