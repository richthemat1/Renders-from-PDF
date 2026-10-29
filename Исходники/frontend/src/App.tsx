import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CircleHelp,
  FileText,
  Files,
  FolderOpen,
  FolderOutput,
  History,
  ImagePlus,
  Images,
  LoaderCircle,
  SlidersHorizontal,
  X,
  AlertTriangle,
  RotateCcw,
  ScanEye,
  Search,
  Trash2,
} from "lucide-react";
import { dateLabel, errorText, native, request } from "./api";
import { Dialog, Empty, Shell, StatusNote } from "./components";
import type { HistoryItem, NoticeAction, ProcessingFailure, Result, Snapshot } from "./types";
import { Viewer } from "./Viewer";
import { Settings } from "./Settings";
import { ProcessingPage } from "./ProcessingPage";
import { failureMessage } from "./processing-model";
import "./app-ux.css";
import "./history-visual.css";

type Page = "work" | "history" | "settings" | "viewer";
export function App() {
  const [state, setState] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState("");
  const [page, setPage] = useState<Page>("work");
  const [result, setResult] = useState<Result | null>(null);
  const [notice, setNotice] = useState<{ text: string; error: boolean; action?: NoticeAction } | null>(
    null,
  );
  const [waiting, setWaiting] = useState(false);
  const [help, setHelp] = useState(false);
  const [fileDrag, setFileDrag] = useState(false);
  const [, setDirty] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [historyQuery, setHistoryQuery] = useState("");
  const [leaving, setLeaving] = useState(false);
  const [leaveError, setLeaveError] = useState(false);
  const saveCurrent = useRef<(() => Promise<boolean>) | null>(null);
  const registerSave = useCallback((save: (() => Promise<boolean>) | null) => { saveCurrent.current = save; }, []);
  const dirtyRef = useRef(false);
  const trackDirty = useCallback((value: boolean) => {
    dirtyRef.current = value;
    setDirty(value);
  }, []);
  useLayoutEffect(() => { window.scrollTo(0, 0); }, [page, result?.id]);
  useEffect(() => {
    let depth = 0;
    const clear = () => { depth = 0; setFileDrag(false); };
    const enter = (event: DragEvent) => {
      if (page !== "work" || state?.busy || !event.dataTransfer?.types.includes("Files")) return;
      depth += 1;
      setFileDrag(true);
    };
    const leave = () => { if (--depth <= 0) clear(); };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", clear);
    window.addEventListener("dragend", clear);
    window.addEventListener("blur", clear);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", clear);
      window.removeEventListener("dragend", clear);
      window.removeEventListener("blur", clear);
      clear();
    };
  }, [page, state?.busy]);
  useEffect(() => {
    let cancelled = false,
      timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      let interval = 1300;
      try {
        const next = await request<Snapshot>(
          "state",
          undefined,
          controller.signal,
        );
        if (!cancelled) {
          setState(next);
          setConnection("");
        }
        interval = next.busy || next.closing ? 300 : 1300;
      } catch (error) {
        if (!cancelled) setConnection(errorText(error));
      }
      if (!cancelled) timer = setTimeout(poll, interval);
    }
    void poll();
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    if (!notice || notice.error) return;
    const timer = setTimeout(() => setNotice(null), 6500);
    return () => clearTimeout(timer);
  }, [notice]);
  const notify = useCallback(
    (text: string, error = false, action?: NoticeAction) => setNotice({ text, error, action }),
    [],
  );
  useEffect(() => {
    const nativeError = (event: Event) =>
      notify(String((event as CustomEvent<string>).detail), true);
    window.addEventListener("pdf-photos-error", nativeError);
    return () => window.removeEventListener("pdf-photos-error", nativeError);
  }, [notify]);
  const run = useCallback(
    async (fn: () => Promise<void>) => {
      setWaiting(true);
      try {
        await fn();
      } catch (error) {
        notify(errorText(error), true);
      } finally {
        setWaiting(false);
      }
    },
    [notify],
  );
  const guard = useCallback((action: () => void) => {
    if (dirtyRef.current) { setLeaveError(false); setPending(() => action); }
    else action();
  }, []);
  function navigate(next: Page) {
    guard(() => {
      trackDirty(false);
      setPage(next);
    });
  }
  const choose = useCallback(() => {
    if (!state?.busy && !waiting)
      guard(() => void run(async () => {
        const next = await native("choose_pdfs");
        setState(next);
        setPage("work");
      }));
  }, [state?.busy, waiting, run, guard]);
  useEffect(() => {
    const started = () => {
      if (!dirtyRef.current) setPage("work");
    };
    window.addEventListener("pdf-photos-job-started", started);
    return () => window.removeEventListener("pdf-photos-job-started", started);
  }, []);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (
        (event.ctrlKey || event.metaKey) &&
        event.code === "KeyO" &&
        !document.querySelector("dialog[open]")
      ) {
        event.preventDefault();
        choose();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [choose]);
  async function openHistory(id: string) {
    await run(async () => {
      const next = await request<Result>("open", { id });
      trackDirty(false);
      setResult(next);
      setPage("viewer");
    });
  }
  async function saveAndContinue() {
    const action = pending;
    if (!action || !saveCurrent.current || leaving) return;
    setLeaving(true);
    setLeaveError(false);
    try {
      if (await saveCurrent.current()) {
        setPending(null);
        trackDirty(false);
        action();
      } else setLeaveError(true);
    } catch (error) {
      notify(errorText(error), true);
      setLeaveError(true);
    } finally { setLeaving(false); }
  }
  function historyAction(item: HistoryItem, action: "folder" | "repeat" | "relocate" | "forget") {
    guard(() => void run(async () => {
      if (action === "folder") await native("open_history_folder", item.id);
      else if (action === "relocate") {
        const next = await native("relocate_result", item.id);
        if (next) { setResult(next); setPage("viewer"); }
      } else {
        setState(await request<Snapshot>(`history/${item.id}/${action}`, {}));
        if (action === "repeat") setPage("work");
        else notify("Запись убрана из истории. Файлы результата сохранены.");
      }
    }));
  }
  function failureAction(item: ProcessingFailure, action: "retry" | "forget") {
    void run(async () => {
      setState(await request<Snapshot>(`failures/${item.id}/${action}`, {}));
      if (action === "retry") setPage("work");
    });
  }
  function chooseFailureOutput() {
    void run(async () => {
      const next = await native("choose_output");
      setState(next);
      if (next.settings.mode !== state?.settings.mode || next.settings.folder !== state?.settings.folder)
        notify("Папка результатов изменена. Нажмите «Повторить» у нужного файла.");
    });
  }
  const matchingHistory = (state?.history ?? []).filter((item) => item.name.toLocaleLowerCase("ru").includes(historyQuery.toLocaleLowerCase("ru")));
  const matchingFailures = (state?.failures ?? []).filter((item) => item.name.toLocaleLowerCase("ru").includes(historyQuery.toLocaleLowerCase("ru")));
  function failureList(items: ProcessingFailure[]) {
    if (!items.length) return null;
    return <section className="failure-panel" aria-label="Необработанные файлы">
      <div className="section-title"><h2><AlertTriangle size={18} />Не обработано: {items.length}</h2></div>
      <p>Эти файлы требуют повторной обработки. Причины сохраняются между запусками приложения.</p>
      {items.map((item) => <article className="failure-row" key={item.id}>
        <div><strong>{item.name}</strong><p>{failureMessage(item)}</p>
          {failureMessage(item) !== item.reason && <details className="failure-details"><summary>Технические подробности</summary><p>{item.reason}</p></details>}
          <small>{dateLabel(item.failed_at)}</small></div>
        <div className="failure-actions">
          <button className="btn compact secondary" disabled={waiting || state?.busy} onClick={() => failureAction(item, "retry")}><RotateCcw size={15} />Повторить</button>
          {item.action === "choose_output" && <button className="btn compact secondary" disabled={waiting || state?.busy} onClick={chooseFailureOutput}><FolderOpen size={15} />Выбрать другую папку</button>}
          <button className="icon-btn" disabled={waiting} aria-label={`Убрать ошибку из истории: ${item.name}`} title="Убрать запись из истории" onClick={() => failureAction(item, "forget")}><X size={16} /></button>
        </div>
      </article>)}
    </section>;
  }
  function historyRow(item: HistoryItem) {
    const repeats = (state?.history ?? []).filter((other) => other.source.replaceAll("\\", "/").toLocaleLowerCase() === item.source.replaceAll("\\", "/").toLocaleLowerCase()).length > 1;
    return <article className="history-entry history-card" key={item.id}>
      <button className="history-row history-card-main" disabled={waiting || !item.available} onClick={() => void openHistory(item.id)}>
        <span className="history-card-top">
          <span className="file-icon"><FileText size={21} /></span>
          <span className={`history-card-state${!item.available || item.errors ? " needs-attention" : ""}`}>
            {!item.available || item.errors ? <AlertTriangle size={14} /> : item.review || item.unsigned ? <ScanEye size={14} /> : <Check size={14} />}
            {!item.available ? "Папка недоступна" : item.review || item.unsigned ? `Проверить: ${item.review + item.unsigned}` : item.errors ? `Ошибки: ${item.errors}` : "Готово"}
          </span>
          <time dateTime={item.finished_at}>{dateLabel(item.finished_at)}</time>
        </span>
        <strong className="history-card-title" title={item.name}>{item.name}</strong>
        <span className="history-card-metrics">
          <span><span>Изображения<Images size={15} /></span><strong>{item.images}</strong></span>
          <span><span>PDF<Files size={15} /></span><strong>{item.documents}</strong></span>
        </span>
        <span className="history-card-location">
          <span><FolderOutput size={14} />Папка результата</span>
          <span className="history-path" title={item.output}>{item.output}</span>
          {!item.available && <span className="history-unavailable">Укажите новое расположение папки</span>}
          {repeats && <span className="history-repeat-note">Есть другие выгрузки этого PDF</span>}
        </span>
      </button>
      <div className="history-actions">
        {item.available ? <button className="btn compact secondary" disabled={waiting} onClick={() => historyAction(item, "folder")} aria-label={`Открыть папку: ${item.name}`}><FolderOpen size={15} /><span>Папка</span></button>
          : <button className="btn compact secondary" disabled={waiting || state?.busy} onClick={() => historyAction(item, "relocate")}><FolderOpen size={15} />Указать папку</button>}
        <button className="btn compact secondary" disabled={waiting || state?.busy} aria-label={`Обработать заново: ${item.name}`} onClick={() => historyAction(item, "repeat")}><RotateCcw size={15} /><span>Заново</span></button>
        <button className="icon-btn" disabled={waiting} aria-label={`Убрать из истории: ${item.name}`} title="Убрать запись из истории, сохранив файлы" onClick={() => historyAction(item, "forget")}><Trash2 size={18} /></button>
      </div>
    </article>;
  }
  function previous() {
    guard(
      () =>
        void run(async () => {
          const next = await native("choose_result");
          if (next) {
            trackDirty(false);
            setResult(next);
            setPage("viewer");
          }
        }),
    );
  }
  const workNavigation = (
    <>
      <button
        className={`project-link ${page === "work" ? "active" : ""}`}
        title="Обработка PDF"
        onClick={() => navigate("work")}
      >
        <span>
          <ImagePlus size={17} />
          Обработка PDF
        </span>
        {state?.busy && <LoaderCircle size={14} className="spin" />}
      </button>
      <button
        className={`project-link ${page === "history" ? "active" : ""}`}
        title="История обработки"
        onClick={() => navigate("history")}
      >
        <span>
          <History size={17} />
          История обработки
        </span>
      </button>
    </>
  );
  const appUtilities = (
    <>
      <button
        className={`project-link ${page === "settings" ? "active" : ""}`}
        title="Настройки"
        onClick={() => navigate("settings")}
      >
        <span>
          <SlidersHorizontal size={17} />
          Настройки
        </span>
      </button>
      <button className="project-link" title="Как пользоваться" onClick={() => setHelp(true)}>
        <span>
          <CircleHelp size={17} />
          Как пользоваться
        </span>
      </button>
    </>
  );
  const footer = <>{workNavigation}{appUtilities}</>;
  const chrome = (
    <>
      {connection && (
        <div className="connection" role="alert">
          <AlertTriangle size={16} />
          Связь с приложением потеряна. Восстанавливаю… {connection}
        </div>
      )}
      {notice && (
        <div
          className={`toast ${notice.error ? "error" : ""}`}
          role={notice.error ? "alert" : "status"}
        >
          {notice.error ? <AlertTriangle size={18} /> : <Check size={18} />}
          <span>{notice.text}</span>
          {notice.action && <button className="toast-action" onClick={() => {
            const action = notice.action;
            if (action) void run(async () => {
              await action.run();
              setNotice((current) => current === notice ? null : current);
            });
          }} disabled={waiting}>{notice.action.label}</button>}
          <button aria-label="Скрыть сообщение" onClick={() => setNotice(null)}>
            <X size={17} />
          </button>
        </div>
      )}
      {pending && (
        <Dialog
          title="Есть несохранённые изменения"
          busy={leaving}
          onClose={() => setPending(null)}
        >
          <p>
            Сохраните правки и продолжите выбранное действие либо вернитесь к редактору.
          </p>
          {leaveError && <p className="dialog-error" role="alert">Не удалось сохранить правки. Вернитесь к редактору: ваши изменения остаются там.</p>}
          <div className="dialog-choices">
            <button className="btn primary" disabled={leaving || !saveCurrent.current} onClick={() => void saveAndContinue()}>
              {leaving && <LoaderCircle size={16} className="spin" />}Сохранить и продолжить
            </button>
            <div className="dialog-alternatives">
              <button className="btn secondary" disabled={leaving} onClick={() => setPending(null)}>
                Вернуться к правкам
              </button>
              <button
                className="btn danger-text"
                disabled={leaving}
                onClick={() => {
                  const action = pending;
                  setPending(null);
                  trackDirty(false);
                  action();
                }}
              >
                Перейти без сохранения
              </button>
            </div>
          </div>
        </Dialog>
      )}
      {state?.pending_repeat && <Dialog title={state.pending_repeat.files.length === 1 ? "Этот PDF уже обработан" : "Эти PDF уже обработаны"}
        busy={waiting} onClose={() => {
          const token = state.pending_repeat!.token;
          void run(async () => setState(await request<Snapshot>("jobs/confirm", {token, proceed:false})));
        }}>
        <p>Можно открыть прежний результат или создать новую выгрузку. Уже готовые папки сохранятся.</p>
        <ul className="repeat-list">{state.pending_repeat.files.map((file, index) => <li key={`${file.previous_id}-${index}`}><strong>{file.name}</strong><span>{dateLabel(file.finished_at)}</span></li>)}</ul>
        <div className="dialog-choices">
          <button className="btn primary" disabled={waiting} onClick={() => {
            const token = state.pending_repeat!.token;
            void run(async () => { setState(await request<Snapshot>("jobs/confirm", {token, proceed:true})); setPage("work"); });
          }}>Обработать заново</button>
          <div className="dialog-alternatives">
            {state.pending_repeat.files.some(file => file.available) && <button className="btn secondary" disabled={waiting} onClick={() => {
              const previous = state.pending_repeat!.files.find(file => file.available)!;
              const token = state.pending_repeat!.token;
              void run(async () => {
                setState(await request<Snapshot>("jobs/confirm", {token, proceed:false}));
                const next = await request<Result>("open", {id:previous.previous_id});
                setResult(next); setPage("viewer");
              });
            }}>Открыть прошлый результат</button>}
            <button className={`btn ${state.pending_repeat.files.some(file => file.available) ? "quiet-text" : "secondary"}`} disabled={waiting} onClick={() => {
              const token = state.pending_repeat!.token;
              void run(async () => setState(await request<Snapshot>("jobs/confirm", {token, proceed:false})));
            }}>Отмена</button>
          </div>
        </div>
      </Dialog>}
      {help && (
        <Dialog title="Как пользоваться" className="help-dialog" onClose={() => setHelp(false)}>
          <ol className="help-list">
            <li>
              Выберите PDF или перетащите файлы и папки из Проводника. Обработка
              начнётся сразу.
            </li>
            <li>
              Откройте результат в этом окне. Проверьте материалы без подписи и
              на проверку.
            </li>
            <li>
              Включите «Редактировать», выберите изображения и перенесите в
              нужный вариант. Сохраните правки.
            </li>
            <li>Выбор сохраняется между разделами. Флажки и Ctrl+щелчок добавляют карточки; счётчик «Скрыто» учитывает выбранное в других разделах.</li>
            <li>«Вернуть в папку подписи» убирает картинку из варианта. В разделе «Убранные» можно восстановить карточки, в том числе после сохранения.</li>
            <li>В крупном просмотре стрелки ←/→ листают изображения, колесо меняет масштаб, двойной щелчок приближает изображение.</li>
          </ol>
          <p>
            Исходные PDF не меняются. Предыдущие версии файлов результата
            сохраняются в резервных копиях. Переносимый «Просмотр.html»
            находится в папке результата.
          </p>
          <p className="muted">
            Ctrl+O — выбрать PDF · Ctrl+K — поиск · Ctrl+A — выделить видимые ·
            Ctrl+Z — отменить правку · Delete — убрать выбранное.
          </p>
          <div className="dialog-choices">
            <button className="btn primary" onClick={() => setHelp(false)}>Понятно</button>
            <div className="dialog-alternatives">
              <button className="btn secondary" onClick={() => void run(async () => { await native("open_help"); })}><FileText size={16} />Открыть полную инструкцию</button>
            </div>
          </div>
        </Dialog>
      )}
    </>
  );
  if (page === "viewer" && result)
    return (
      <>
        <Viewer
          key={result.id}
          result={result}
          footer={footer}
          notify={notify}
          onResult={setResult}
          onDirty={trackDirty}
          onBack={() => navigate("history")}
          onSaveReady={registerSave}
        />
        {chrome}
      </>
    );
  const title =
    page === "history"
      ? "История обработки"
      : page === "settings"
        ? "Настройки"
        : "Обработка PDF";
  return (
    <>
      <Shell
        footer={appUtilities}
        version={state?.version}
        navigation={workNavigation}
        header={
          <>
            <span className="topbar-title">{title}</span>
            {state?.busy && <StatusNote busy>Обработка PDF</StatusNote>}
          </>
        }
      >
        <div className={`content${page === "settings" ? " settings-content" : page === "work" ? " work-content" : " history-content"}`}>
          {!state ? (
            <Empty
              icon={
                connection ? (
                  <AlertTriangle size={32} />
                ) : (
                  <LoaderCircle size={32} className="spin" />
                )
              }
              title={
                connection
                  ? "Не удалось подключиться"
                  : "Открываю рабочее пространство"
              }
            >
              {connection || "Загружаю настройки и историю."}
            </Empty>
          ) : (
            <>
              {state.warning && (
                <div className="inline-warning" role="alert">
                  <AlertTriangle size={18} />
                  {state.warning}
                </div>
              )}
              {page === "work" && (
                <ProcessingPage state={state} waiting={waiting} fileDrag={fileDrag}
                  onChoose={choose} onPrevious={previous}
                  onCancel={() => void run(async () => setState(await request<Snapshot>("cancel", {})))}
                  onSettings={() => navigate("settings")}
                  onOpenResult={() => {
                    const output = state.current_output?.replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase();
                    const record = state.history.find((item) => item.output.replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase() === output);
                    if (record) void openHistory(record.id);
                    else notify("Результат не найден в истории. Откройте его папку вручную.", true,
                      { label: "Выбрать папку результата", run: previous });
                  }}
                  onHistory={() => navigate("history")}
                  onOpenHistory={(item) => void openHistory(item.id)}
                  failures={failureList(state.failures ?? [])}
                />
              )}
              {page === "history" && (
                <div className="history-page">
                  <div className="history-heading-search">
                  <div className="page-heading">
                    <div>
                      <h1>История обработки</h1>
                      <p>До 100 последних результатов на этом компьютере.</p>
                    </div>
                    <button
                      className="btn secondary"
                      onClick={previous}
                      disabled={waiting}
                    >
                      <FolderOpen size={17} />
                      Открыть папку результата
                    </button>
                  </div>
                  <label className="history-search-control"><Search size={18} />
                  <input
                    className="history-search"
                    type="search"
                    aria-label="Найти результат"
                    placeholder="Найти по названию PDF…"
                    value={historyQuery}
                    onChange={(e) => setHistoryQuery(e.target.value)}
                  />
                  </label>
                  </div>
                  {failureList(matchingFailures)}
                  {!state.history.length && !(state.failures?.length) ? (
                    <Empty icon={<History size={32} />} title="Здесь появятся ваши результаты"
                      action={<button className="btn primary" onClick={() => navigate("work")}>Обработать первый PDF<ArrowRight size={17} /></button>}>
                      Обработайте презентацию или откройте папку с прошлым результатом.
                    </Empty>
                  ) : (
                    <div className="history-list">{matchingHistory.map(historyRow)}</div>
                  )}
                  {!!(state.history.length || state.failures?.length) && !matchingHistory.length && !matchingFailures.length && (
                    <Empty icon={<History size={30} />} title="Результат не найден">
                      Попробуйте часть названия или откройте нужную папку вручную.
                    </Empty>
                  )}
                </div>
              )}
              {page === "settings" && (
                <Settings
                  state={state}
                  waiting={waiting}
                  onModeChange={(mode) => void run(async () => setState(
                    mode === "custom"
                      ? await native("choose_output")
                      : await request<Snapshot>("settings", { mode }),
                  ))}
                  onChooseFolder={() => void run(async () => setState(await native("choose_output")))}
                  onOpenLog={() => void run(async () => { await native("open_log"); })}
                />
              )}
            </>
          )}
        </div>
      </Shell>
      {chrome}
    </>
  );
}
