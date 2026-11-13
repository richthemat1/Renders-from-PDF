import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
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
import { Dialog, Empty, Shell, Toast } from "./components";
import { useContextMenu, useTextFieldMenu } from "./contextMenu";
import { BANNER_MOTION, MotionDiv, Presence } from "./motion";
import type { HistoryItem, Notice, ProcessingFailure, Result, Snapshot } from "./types";
import { Viewer } from "./Viewer";
import { Settings } from "./Settings";
import { ProcessingPage } from "./ProcessingPage";
import { canRetry, failureMessage } from "./processing-model";
import { HelpDialog } from "./Help";
import type { HelpTopic } from "./Help";
import "./app-ux.css";
import "./history-visual.css";

type Page = "work" | "history" | "settings" | "viewer";
export function App() {
  const [state, setState] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState("");
  const [page, setPage] = useState<Page>("work");
  const [result, setResult] = useState<Result | null>(null);
  const [notice, setNotice] = useState<Notice | null>(
    null,
  );
  const [waiting, setWaiting] = useState(false);
  const [help, setHelp] = useState<HelpTopic | null>(null);
  const historyMenu = useContextMenu();
  const textMenu = useTextFieldMenu();
  // «Как пользоваться» in the menu of a result opens the section about the part of it on screen.
  const helpTopic = useRef<HelpTopic>("review");
  const trackHelpTopic = useCallback((topic: HelpTopic) => { helpTopic.current = topic; }, []);
  const [fileDrag, setFileDrag] = useState(false);
  const [, setDirty] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  // The pending action closes the window: the question names closing, not «продолжить».
  const [pendingClose, setPendingClose] = useState(false);
  useEffect(() => { if (!pending) setPendingClose(false); }, [pending]);
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
  // The pointer over a message, or the focus in it, holds it on screen: its action stays within reach.
  const [noticeHeld, setNoticeHeld] = useState(false);
  // A new message starts unheld: the one under the pointer may have gone without a «mouseleave».
  useEffect(() => setNoticeHeld(false), [notice]);
  useEffect(() => {
    if (!notice || notice.error || noticeHeld) return;
    // A notice with an action, such as «Отменить сохранение», stays long enough to reach for it.
    const timer = setTimeout(() => setNotice(null), notice.action ? 20000 : 6500);
    return () => clearTimeout(timer);
  }, [notice, noticeHeld]);
  // An error belongs to the page it happened on: another page closes it instead of carrying it along.
  const shownPage = useRef(page);
  useEffect(() => {
    if (shownPage.current === page) return;
    shownPage.current = page;
    setNotice((current) => current?.error ? null : current);
  }, [page]);
  useEffect(() => {
    // F6 moves to the message at the bottom of the window, as Windows moves between the panes of a window.
    const key = (event: KeyboardEvent) => {
      if (event.key !== "F6" || event.ctrlKey || event.altKey || event.metaKey) return;
      const target = document.querySelector<HTMLElement>(".toast .toast-action, .toast .toast-close");
      if (!target) return;
      event.preventDefault();
      target.focus();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const notify = useCallback(
    (text: string, error = false, action?: Notice["action"]) => setNotice({ text, error, action }),
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
  useEffect(() => {
    window.pdfPhotosRequestClose = () => {
      if (!dirtyRef.current) return false;
      setLeaveError(false);
      setPendingClose(true);
      setPending(() => () => void native("close_window").catch((error) => notify(errorText(error), true)));
      return true;
    };
    return () => { delete window.pdfPhotosRequestClose; };
  }, [notify]);
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
    // Ctrl+O while a queue runs used to do nothing at all: say why and what to do.
    if (state?.busy) {
      notify("Идёт обработка. Новые PDF можно добавить, когда она закончится или после «Остановить обработку».");
      return;
    }
    if (!waiting)
      guard(() => void run(async () => {
        const next = await native("choose_pdfs");
        setState(next);
        setPage("work");
      }));
  }, [state?.busy, waiting, run, guard, notify]);
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
        notify("Место сохранения изменено. Нажмите «Обработать заново» у нужного файла.");
    });
  }
  const matchingHistory = (state?.history ?? []).filter((item) => item.name.toLocaleLowerCase("ru").includes(historyQuery.toLocaleLowerCase("ru")));
  const matchingFailures = (state?.failures ?? []).filter((item) => item.name.toLocaleLowerCase("ru").includes(historyQuery.toLocaleLowerCase("ru")));
  function failureList(items: ProcessingFailure[]) {
    if (!items.length) return null;
    return <section className="failure-panel" aria-label="Необработанные файлы">
      <div className="section-title"><h2><AlertTriangle size={18} />Не обработано: {items.length}</h2></div>
      <p>Эти файлы не обработаны. Причины сохраняются между запусками.</p>
      {items.map((item) => <article className="failure-row" key={item.id}>
        <div><strong>{item.name}</strong><p>{failureMessage(item)}</p>
          {failureMessage(item) !== item.reason && <details className="failure-details"><summary>Технические подробности</summary><p>{item.reason}</p></details>}
          <small>{dateLabel(item.failed_at)}</small></div>
        <div className="failure-actions">
          {canRetry(item) && <button className="btn compact secondary" disabled={waiting || state?.busy} onClick={() => failureAction(item, "retry")}><RotateCcw size={15} />Обработать заново</button>}
          {item.action === "choose_output" && <button className="btn compact secondary" disabled={waiting || state?.busy} onClick={chooseFailureOutput}><FolderOpen size={15} />Выбрать другую папку…</button>}
          <button className="icon-btn" disabled={waiting} aria-label={`Убрать ошибку из истории: ${item.name}`} title="Убрать запись из истории" onClick={() => failureAction(item, "forget")}><X size={16} /></button>
        </div>
      </article>)}
    </section>;
  }
  /** A processed PDF in the history or among the latest results: the same actions as the buttons of its card. */
  function openHistoryMenu(event: ReactMouseEvent, item: HistoryItem) {
    historyMenu.open(event, {
      heading: item.name,
      items: [
        { value: "open", label: "Открыть результат", icon: <ArrowRight size={16} />, disabled: waiting || !item.available },
        item.available
          ? { value: "folder", label: "Открыть папку результата", icon: <FolderOpen size={16} />, disabled: waiting }
          : { value: "relocate", label: "Указать папку…", icon: <FolderOpen size={16} />, disabled: waiting || !!state?.busy },
        { value: "repeat", label: "Обработать заново", icon: <RotateCcw size={16} />, disabled: waiting || !!state?.busy },
        { value: "forget", label: "Убрать из истории", icon: <Trash2 size={16} />, danger: true, separator: true, disabled: waiting },
      ],
      onAction(value) {
        if (value === "open") void openHistory(item.id);
        else historyAction(item, value as "folder" | "repeat" | "relocate" | "forget");
      },
    });
  }
  function historyRow(item: HistoryItem) {
    const repeats = (state?.history ?? []).filter((other) => other.source.replaceAll("\\", "/").toLocaleLowerCase() === item.source.replaceAll("\\", "/").toLocaleLowerCase()).length > 1;
    return <article className="history-entry history-card" key={item.id} onContextMenu={(event) => openHistoryMenu(event, item)}>
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
          {!item.available && <span className="history-unavailable">Папка не найдена — нажмите «Указать папку…»</span>}
          {repeats && <span className="history-repeat-note">Есть другие выгрузки этого PDF</span>}
        </span>
      </button>
      <div className="history-actions">
        {item.available ? <button className="btn compact secondary" disabled={waiting} onClick={() => historyAction(item, "folder")} aria-label={`Открыть папку результата: ${item.name}`}><FolderOpen size={15} /><span>Открыть папку</span></button>
          : <button className="btn compact secondary" disabled={waiting || state?.busy} onClick={() => historyAction(item, "relocate")}><FolderOpen size={15} />Указать папку…</button>}
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
        aria-current={page === "work" ? "page" : undefined}
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
        aria-current={page === "history" ? "page" : undefined}
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
        aria-current={page === "settings" ? "page" : undefined}
        className={`project-link ${page === "settings" ? "active" : ""}`}
        title="Настройки"
        onClick={() => navigate("settings")}
      >
        <span>
          <SlidersHorizontal size={17} />
          Настройки
        </span>
      </button>
      <button className="project-link" title="Как пользоваться" onClick={() => setHelp(page === "viewer" ? helpTopic.current : "start")}>
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
      {historyMenu.menu}
      {textMenu}
      <Presence>{connection && (
        <MotionDiv motion={BANNER_MOTION} className="connection" role="alert">
          <AlertTriangle size={16} />
          Связь с приложением потеряна. Восстанавливаю… {connection}
        </MotionDiv>
      )}</Presence>
      {/* The region stays in the window, so a message that appears in it is read out (WCAG 4.1.3). */}
      <div className="toast-live" aria-live="polite">
      <Presence>{notice && <Toast notice={notice} busy={waiting} onHold={setNoticeHeld} onClose={() => { setNotice(null); setNoticeHeld(false); }} onAction={() => {
        const action = notice.action;
        if (action) void run(async () => {
          await action.run();
          setNotice((current) => current === notice ? null : current);
        });
      }} />}</Presence>
      </div>
      <Presence>{pending && (
        <Dialog
          title={pendingClose ? "Закрыть окно с несохранёнными правками?" : "Есть несохранённые изменения"}
          busy={leaving}
          onClose={() => setPending(null)}
        >
          <p>
            {pendingClose ? "Сохраните правки перед закрытием или закройте окно без них. Уже сохранённые PDF останутся на месте."
              : "Сохраните правки и продолжите выбранное действие либо вернитесь к редактору."}
          </p>
          {leaveError && <p className="dialog-error" role="alert">Не удалось сохранить правки. Вернитесь к редактору: ваши изменения остаются там.</p>}
          <div className="dialog-choices">
            <button className="btn primary" disabled={leaving || !saveCurrent.current} onClick={() => void saveAndContinue()}>
              {leaving && <LoaderCircle size={16} className="spin" />}{pendingClose ? "Сохранить и закрыть" : "Сохранить и продолжить"}
            </button>
            <div className="dialog-alternatives">
              <button className="btn secondary" data-autofocus disabled={leaving} onClick={() => setPending(null)}>
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
                {pendingClose ? "Закрыть без сохранения" : "Перейти без сохранения"}
              </button>
            </div>
          </div>
        </Dialog>
      )}</Presence>
      <Presence>{state?.pending_repeat && (() => {
        const repeat = state.pending_repeat;
        // Files of the same selection never processed: no choice about the old ones may drop them silently.
        const fresh = repeat.new ?? [];
        const confirm = (proceed: boolean | "new") => request<Snapshot>("jobs/confirm", { token: repeat.token, proceed });
        const previous = repeat.files.find((file) => file.available);
        return <Dialog title={fresh.length ? "Часть PDF уже обработана" : repeat.files.length === 1 ? "Этот PDF уже обработан" : "Эти PDF уже обработаны"}
          busy={waiting} onClose={() => void run(async () => setState(await confirm(false)))}>
          <p>{fresh.length
            ? `Новые PDF (${fresh.length}) можно обработать сразу. Для уже обработанных — открыть прежний результат или сделать новую выгрузку; готовые папки сохранятся.`
            : "Можно открыть прежний результат или создать новую выгрузку. Уже готовые папки сохранятся."}</p>
          <ul className="repeat-list">
            {repeat.files.map((file, index) => <li key={`${file.previous_id}-${index}`}><strong>{file.name}</strong><span>Обработан {dateLabel(file.finished_at)}</span>
              {/* Several earlier results: each opens on its own, so «Открыть прошлый результат» never guesses which. */}
              {repeat.files.length > 1 && file.available && <button className="text-btn" disabled={waiting} onClick={() =>
                void run(async () => {
                  setState(await confirm(fresh.length ? "new" : false));
                  const next = await request<Result>("open", { id: file.previous_id });
                  setResult(next); setPage("viewer");
                })}>Открыть прошлый результат</button>}</li>)}
            {fresh.map((name, index) => <li key={`new-${index}`} className="is-new"><strong>{name}</strong><span>Новый — ещё не обработан</span></li>)}
          </ul>
          <div className="dialog-choices">
            {fresh.length ? <button className="btn primary" data-autofocus disabled={waiting} onClick={() =>
              void run(async () => { setState(await confirm("new")); setPage("work"); })}>Обработать только новые</button>
              : <button className="btn primary" data-autofocus disabled={waiting} onClick={() =>
                void run(async () => { setState(await confirm(true)); setPage("work"); })}>Обработать заново</button>}
            <div className="dialog-alternatives">
              {!!fresh.length && <button className="btn secondary" disabled={waiting} onClick={() =>
                void run(async () => { setState(await confirm(true)); setPage("work"); })}>Обработать все заново</button>}
              {previous && repeat.files.length === 1 && <button className="btn secondary" disabled={waiting}
                title={fresh.length ? "Новые PDF обработаются, пока вы смотрите прежний результат" : undefined} onClick={() =>
                  void run(async () => {
                    setState(await confirm(fresh.length ? "new" : false));
                    const next = await request<Result>("open", { id: previous.previous_id });
                    setResult(next); setPage("viewer");
                  })}>{fresh.length ? "Открыть прошлый и обработать новые" : "Открыть прошлый результат"}</button>}
              <button className={`btn ${previous || fresh.length ? "quiet-text" : "secondary"}`} disabled={waiting}
                onClick={() => void run(async () => setState(await confirm(false)))}>Отмена</button>
            </div>
          </div>
        </Dialog>;
      })()}</Presence>
      <Presence>{help && <HelpDialog topic={help} onClose={() => setHelp(null)}
        onOpenGuide={() => void run(async () => { await native("open_help"); })} />}</Presence>
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
          onHelp={setHelp}
          onHelpTopic={trackHelpTopic}
        />
        {chrome}
      </>
    );
  // No top bar here: each page names itself in its heading, and the menu item «Обработка PDF» spins while a PDF is read.
  return (
    <>
      <Shell
        footer={appUtilities}
        version={state?.version}
        navigation={workNavigation}
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
                  onResume={() => void run(async () => setState(await request<Snapshot>("jobs/resume", {})))}
                  onSettings={() => navigate("settings")}
                  onOpenResult={() => {
                    const output = state.current_output?.replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase();
                    const record = state.history.find((item) => item.output.replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase() === output);
                    if (record) void openHistory(record.id);
                    else notify("Результат не найден в истории. Откройте его папку вручную.", true,
                      { label: "Открыть прошлый результат…", icon: <FolderOpen size={16} strokeWidth={2} absoluteStrokeWidth />, run: previous });
                  }}
                  onHistory={() => navigate("history")}
                  onOpenHistory={(item) => void openHistory(item.id)}
                  onHistoryMenu={openHistoryMenu}
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
                      Открыть прошлый результат…
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
                  onChooseDelivery={() => void run(async () => setState(await native("choose_delivery")))}
                  onClearDelivery={() => void run(async () => setState(await native("clear_delivery")))}
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
