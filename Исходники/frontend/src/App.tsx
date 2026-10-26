import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  ChevronRight,
  CircleHelp,
  FileImage,
  FileText,
  FolderOpen,
  History,
  ImagePlus,
  Images,
  LoaderCircle,
  Settings2,
  ShieldCheck,
  Square,
  Upload,
  X,
  AlertTriangle,
} from "lucide-react";
import { dateLabel, errorText, native, request } from "./api";
import { Dialog, Empty, Shell, StatusNote } from "./components";
import type { Result, Snapshot } from "./types";
import { Viewer } from "./Viewer";

type Page = "work" | "history" | "settings" | "viewer";
export function App() {
  const [state, setState] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState("");
  const [page, setPage] = useState<Page>("work");
  const [result, setResult] = useState<Result | null>(null);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const [waiting, setWaiting] = useState(false);
  const [help, setHelp] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [historyQuery, setHistoryQuery] = useState("");
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
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
    (text: string, error = false) => setNotice({ text, error }),
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
  function guard(action: () => void) {
    if (dirtyRef.current) setPending(() => action);
    else action();
  }
  function navigate(next: Page) {
    guard(() => {
      setDirty(false);
      setPage(next);
    });
  }
  const choose = useCallback(() => {
    if (!state?.busy && !waiting)
      void run(async () => {
        const next = await native("choose_pdfs");
        setState(next);
        setPage("work");
      });
  }, [state?.busy, waiting, run]);
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
        event.ctrlKey &&
        event.code === "KeyO" &&
        !document.querySelector("dialog[open]")
      ) {
        event.preventDefault();
        if (!dirtyRef.current) choose();
      }
      if (
        event.code === "Escape" &&
        state?.busy &&
        !document.querySelector("dialog[open]")
      )
        void run(async () => setState(await request<Snapshot>("cancel", {})));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [choose, state?.busy, run]);
  async function openHistory(id: string) {
    await run(async () => {
      const next = await request<Result>("open", { id });
      setResult(next);
      setPage("viewer");
    });
  }
  function previous() {
    guard(
      () =>
        void run(async () => {
          const next = await native("choose_result");
          if (next) {
            setDirty(false);
            setResult(next);
            setPage("viewer");
          }
        }),
    );
  }
  const footer = (
    <>
      <button
        className={`project-link ${page === "work" ? "active" : ""}`}
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
        onClick={() => navigate("history")}
      >
        <span>
          <History size={17} />
          История обработки
        </span>
      </button>
      <button
        className={`project-link ${page === "settings" ? "active" : ""}`}
        onClick={() => navigate("settings")}
      >
        <span>
          <Settings2 size={17} />
          Настройки
        </span>
      </button>
      <button className="project-link" onClick={() => setHelp(true)}>
        <span>
          <CircleHelp size={17} />
          Как пользоваться
        </span>
      </button>
    </>
  );
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
          <button aria-label="Скрыть сообщение" onClick={() => setNotice(null)}>
            <X size={17} />
          </button>
        </div>
      )}
      {pending && (
        <Dialog
          title="Есть несохранённые изменения"
          onClose={() => setPending(null)}
        >
          <p>
            Вернуться к результату или перейти без сохранения? Уже сохранённые
            PDF останутся на месте.
          </p>
          <div className="dialog-actions">
            <button className="btn secondary" onClick={() => setPending(null)}>
              Вернуться к правкам
            </button>
            <button
              className="btn danger"
              onClick={() => {
                const action = pending;
                setPending(null);
                setDirty(false);
                action();
              }}
            >
              Перейти без сохранения
            </button>
          </div>
        </Dialog>
      )}
      {help && (
        <Dialog title="Как пользоваться" onClose={() => setHelp(false)}>
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
          onDirty={setDirty}
          onBack={() => navigate("history")}
        />
        {chrome}
      </>
    );
  const title =
    page === "history"
      ? "История обработки"
      : page === "settings"
        ? "Настройки"
        : "Рабочее пространство";
  return (
    <>
      <Shell
        motionKey={page}
        footer={footer}
        navigation={
          <>
            <div className="nav-label">Рабочее пространство</div>
            <button
              className="project-link active"
              onClick={() => navigate(page)}
            >
              <span>
                {page === "work" ? (
                  <FileImage size={17} />
                ) : page === "history" ? (
                  <History size={17} />
                ) : (
                  <Settings2 size={17} />
                )}
                {title}
              </span>
            </button>
            <div className="sidebar-tip">
              <ShieldCheck size={20} />
              <p>
                Файлы остаются у вас.
                <br />
                Вся обработка выполняется локально.
              </p>
            </div>
          </>
        }
        header={
          <>
            <span className="topbar-title">{title}</span>
            <StatusNote busy={state?.busy}>
              {state?.busy
                ? "Обработка PDF"
                : `Версия ${state?.version ?? "…"}`}
            </StatusNote>
          </>
        }
      >
        <div className="content">
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
                <>
                  <div className="page-heading">
                    <div>
                      <h1>Из PDF — в готовые материалы</h1>
                      <p>
                        Извлеките изображения. Проверьте подписи. Соберите
                        варианты.
                      </p>
                    </div>
                    <button
                      className="btn secondary"
                      onClick={previous}
                      disabled={waiting}
                    >
                      <FolderOpen size={17} />
                      Открыть прошлый результат
                    </button>
                  </div>
                  <section
                    className={`upload-zone ${state.busy ? "is-busy" : ""}`}
                    aria-labelledby="upload-title"
                  >
                    <div className="upload-icon">
                      <Upload size={30} strokeWidth={1.6} />
                    </div>
                    <h2 id="upload-title">
                      {state.busy
                        ? "Готовлю ваши материалы"
                        : "Перетащите PDF сюда"}
                    </h2>
                    <p>
                      {state.busy
                        ? state.detail
                        : "Один файл, несколько презентаций или папка из Проводника"}
                    </p>
                    {state.busy ? (
                      <button
                        className="btn secondary"
                        disabled={state.cancelling || waiting}
                        onClick={() =>
                          void run(async () =>
                            setState(await request<Snapshot>("cancel", {})),
                          )
                        }
                      >
                        <Square size={14} />
                        {state.cancelling
                          ? "Останавливаю…"
                          : "Остановить обработку"}
                      </button>
                    ) : (
                      <button
                        className="btn primary"
                        onClick={choose}
                        disabled={waiting}
                      >
                        <ImagePlus size={18} />
                        Выбрать PDF <kbd>Ctrl O</kbd>
                      </button>
                    )}
                    <span className="upload-note">
                      Исходные файлы не изменяются
                    </span>
                  </section>
                  <div className="destination">
                    <FolderOpen size={17} />
                    <span>
                      Сохранять:{" "}
                      <strong title={state.settings.label}>
                        {state.settings.label}
                      </strong>
                    </span>
                    <button
                      className="text-btn"
                      disabled={state.busy}
                      onClick={() => navigate("settings")}
                    >
                      Изменить
                      <ChevronRight size={14} />
                    </button>
                  </div>
                  <section className="job-panel" aria-labelledby="job-status">
                    <div className="section-title">
                      <h2 id="job-status" role="status">
                        {state.status}
                      </h2>
                      <span className="muted tabular">
                        {Math.round(state.progress)}%
                      </span>
                    </div>
                    <div
                      className="progress-track"
                      role="progressbar"
                      aria-label="Обработка PDF"
                      aria-valuenow={Math.round(state.progress)}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      <div
                        style={{ transform: `scaleX(${state.progress / 100})` }}
                      />
                    </div>
                    <div className="job-totals">
                      {[
                        ["Изображений", state.totals.images],
                        ["Готовых PDF", state.totals.documents],
                        ["Без подписи", state.totals.unsigned],
                        ["На проверку", state.totals.review],
                      ].map(([label, count]) => (
                        <div key={label}>
                          <strong>{count}</strong>
                          <span>{label}</span>
                        </div>
                      ))}
                    </div>
                    {state.current_output && (
                      <button
                        className="btn primary"
                        disabled={waiting}
                        onClick={() => {
                          const record = state.history.find(
                            (item) => item.output === state.current_output,
                          );
                          if (record) void openHistory(record.id);
                        }}
                      >
                        <Images size={17} />
                        Открыть результат
                        <ArrowRight size={17} />
                      </button>
                    )}
                  </section>
                  {!!state.logs.length && (
                    <section className="events">
                      <details open={state.busy}>
                        <summary>
                          События обработки<span>{state.logs.length}</span>
                        </summary>
                        <ol>
                          {state.logs.map((item) => (
                            <li key={item.id} className={`log-${item.level}`}>
                              {item.text}
                            </li>
                          ))}
                        </ol>
                      </details>
                    </section>
                  )}
                  {!!state.history.length && (
                    <section className="recent">
                      <div className="section-title">
                        <h2>Последние результаты</h2>
                        <button
                          className="text-btn"
                          onClick={() => navigate("history")}
                        >
                          Вся история
                          <ArrowRight size={15} />
                        </button>
                      </div>
                      {state.history.slice(0, 3).map((item) => (
                        <button
                          className="history-row"
                          key={item.id}
                          disabled={waiting || !item.available}
                          onClick={() => void openHistory(item.id)}
                        >
                          <span className="file-icon">
                            <FileText size={23} />
                          </span>
                          <span className="history-name">
                            <strong>{item.name}</strong>
                            <small>
                              {item.images} изображений · {item.variants}{" "}
                              варианта · {dateLabel(item.finished_at)}
                            </small>
                          </span>
                          <ChevronRight size={18} />
                        </button>
                      ))}
                    </section>
                  )}
                </>
              )}
              {page === "history" && (
                <>
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
                  <input
                    className="history-search"
                    type="search"
                    aria-label="Найти результат"
                    placeholder="Найти по названию PDF…"
                    value={historyQuery}
                    onChange={(e) => setHistoryQuery(e.target.value)}
                  />
                  {!state.history.length ? (
                    <Empty
                      icon={<History size={32} />}
                      title="Здесь появятся ваши результаты"
                      action={
                        <button
                          className="btn primary"
                          onClick={() => navigate("work")}
                        >
                          Обработать первый PDF
                          <ArrowRight size={17} />
                        </button>
                      }
                    >
                      Обработайте презентацию или откройте папку с прошлым
                      результатом.
                    </Empty>
                  ) : (
                    <div className="history-list">
                      {state.history
                        .filter((item) =>
                          item.name
                            .toLocaleLowerCase("ru")
                            .includes(historyQuery.toLocaleLowerCase("ru")),
                        )
                        .map((item) => (
                          <button
                            key={item.id}
                            className="history-row"
                            disabled={waiting || !item.available}
                            onClick={() => void openHistory(item.id)}
                          >
                            <span className="file-icon">
                              <FileText size={25} />
                            </span>
                            <span className="history-name">
                              <strong>{item.name}</strong>
                              <small>
                                {item.images} изображений · {item.documents} PDF
                                · {dateLabel(item.finished_at)}
                              </small>
                              <small
                                className="history-path"
                                title={item.output}
                              >
                                {item.output}
                              </small>
                            </span>
                            <span
                              className={`history-status ${item.review || item.unsigned ? "attention" : ""}`}
                            >
                              {!item.available
                                ? "Папка недоступна"
                                : item.review || item.unsigned
                                  ? `Проверить: ${item.review + item.unsigned}`
                                  : "Готово"}
                            </span>
                            <ChevronRight size={18} />
                          </button>
                        ))}
                    </div>
                  )}
                  {!!state.history.length &&
                    !state.history.some((item) =>
                      item.name
                        .toLocaleLowerCase("ru")
                        .includes(historyQuery.toLocaleLowerCase("ru")),
                    ) && (
                      <Empty
                        icon={<History size={30} />}
                        title="Результат не найден"
                      >
                        Попробуйте часть названия или откройте нужную папку
                        вручную.
                      </Empty>
                    )}
                </>
              )}
              {page === "settings" && (
                <>
                  <div className="page-heading">
                    <div>
                      <h1>Настройки</h1>
                      <p>Как сохранять следующие результаты обработки.</p>
                    </div>
                  </div>
                  <section className="settings-section">
                    <h2>Папка результатов</h2>
                    <p className="muted">
                      Каждая презентация сохраняется в отдельную папку. Уже
                      готовые результаты не перемещаются.
                    </p>
                    <fieldset disabled={state.busy || waiting}>
                      <legend className="sr-only">
                        Расположение новых результатов
                      </legend>
                      {[
                        [
                          "nearby",
                          "Рядом с исходным PDF",
                          "Папка «Название презентации — PDF» рядом с документом.",
                        ],
                        [
                          "results",
                          "В подпапке «Результаты»",
                          "Отдельная общая папка рядом с каждым исходным документом.",
                        ],
                        [
                          "custom",
                          "В выбранной папке",
                          state.settings.folder ||
                            "Выберите папку на этом компьютере.",
                        ],
                      ].map(([mode, label, detail]) => (
                        <label
                          className={`setting-option ${state.settings.mode === mode ? "selected" : ""}`}
                          key={mode}
                        >
                          <input
                            type="radio"
                            name="output"
                            checked={state.settings.mode === mode}
                            onChange={() =>
                              void run(async () =>
                                setState(
                                  mode === "custom"
                                    ? await native("choose_output")
                                    : await request<Snapshot>("settings", {
                                        mode,
                                      }),
                                ),
                              )
                            }
                          />
                          <span>
                            <strong>{label}</strong>
                            <small>{detail}</small>
                          </span>
                          {mode === "custom" && (
                            <button
                              type="button"
                              className="text-btn"
                              onClick={() =>
                                void run(async () =>
                                  setState(await native("choose_output")),
                                )
                              }
                            >
                              Выбрать
                              <FolderOpen size={16} />
                            </button>
                          )}
                        </label>
                      ))}
                    </fieldset>
                    {state.busy && (
                      <p className="muted">
                        Расположение можно изменить после завершения обработки.
                      </p>
                    )}
                  </section>
                  <section className="settings-section">
                    <h2>О приложении</h2>
                    <div className="about-row">
                      <span>Фото из PDF</span>
                      <strong>{state.version}</strong>
                    </div>
                    <div className="about-row">
                      <span>Обработка файлов</span>
                      <span>Локально, без отправки в интернет</span>
                    </div>
                    <button
                      className="btn secondary"
                      onClick={() =>
                        void run(async () => {
                          await native("open_log");
                        })
                      }
                    >
                      <FileText size={17} />
                      Открыть технический журнал
                    </button>
                  </section>
                </>
              )}
            </>
          )}
        </div>
      </Shell>
      {chrome}
    </>
  );
}
