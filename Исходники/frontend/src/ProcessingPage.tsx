import type { ReactNode } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BadgeCheck,
  ChevronRight,
  ChevronUp,
  CircleStop,
  FileInput,
  FilePlus2,
  Files,
  FileText,
  FolderOpen,
  FolderOutput,
  Images,
  ListCollapse,
  LoaderCircle,
  LockKeyhole,
  ScanEye,
  ShieldCheck,
  Square,
  Tag,
  TriangleAlert,
} from "lucide-react";
import { dateLabel, plural } from "./api";
import { processingView } from "./processing-model";
import type { HistoryItem, Snapshot } from "./types";
import "./processing-page.css";

interface ProcessingPageProps {
  state: Snapshot;
  waiting: boolean;
  fileDrag: boolean;
  onChoose(): void;
  onPrevious(): void;
  onCancel(): void;
  onSettings(): void;
  onOpenResult(): void;
  onHistory(): void;
  onOpenHistory(item: HistoryItem): void;
  failures: ReactNode;
}

function UploadZone({ fileDrag, waiting, onChoose }: {
  fileDrag: boolean;
  waiting: boolean;
  onChoose(): void;
}) {
  return <section className={`upload-zone processing-upload${fileDrag ? " is-drag-over" : ""}`} aria-labelledby="upload-title">
    <span className="processing-upload-icon"><FileInput size={30} strokeWidth={1.7} /></span>
    <div className="processing-upload-copy">
      <h3 id="upload-title">{fileDrag ? "Отпустите PDF для обработки" : "Перетащите PDF сюда"}</h3>
      <p>Один файл, несколько презентаций или папка из Проводника</p>
    </div>
    <button className="btn primary" onClick={onChoose} disabled={waiting} aria-keyshortcuts="Control+O">
      <FilePlus2 size={17} />Выбрать PDF<kbd aria-hidden="true">Ctrl O</kbd>
    </button>
  </section>;
}

function OriginalsNote() {
  return <p className="processing-originals"><LockKeyhole size={14} /><span>Исходные файлы не изменяются</span></p>;
}

export function ProcessingPage({ state, waiting, fileDrag, onChoose, onPrevious, onCancel, onSettings, onOpenResult, onHistory, onOpenHistory, failures }: ProcessingPageProps) {
  const view = processingView(state);
  const processing = view.outcome === "processing";
  const complete = view.outcome === "complete";
  const destination = state.settings.mode === "nearby"
    ? "Рядом с исходным PDF"
    : state.settings.mode === "results"
      ? "В подпапке «Результаты» рядом с каждым PDF"
      : state.settings.folder || "В выбранной папке";
  const reviewParts = [
    state.totals.unsigned > 0 ? `без подписи — ${state.totals.unsigned}` : "",
    state.totals.review > 0 ? `на проверку — ${state.totals.review}` : "",
  ].filter(Boolean);
  const attention = !processing && (reviewParts.length > 0 || view.warnings > 0 || view.errors > 0 || view.failed > 0);
  const countNotes = [
    view.failed > 0 ? `Не обработано: ${view.failed}` : "",
    view.errors > 0 ? `${view.errors} ${plural(view.errors, ["ошибка", "ошибки", "ошибок"])}` : "",
    view.warnings > 0 ? `${view.warnings} ${plural(view.warnings, ["предупреждение", "предупреждения", "предупреждений"])}` : "",
  ].filter(Boolean);
  const eventsOpen = processing || state.logs.some((item) => item.level === "warn" || item.level === "error") || view.warnings > 0 || view.errors > 0;
  const totals = [
    { label: "Изображения", value: state.totals.images, Icon: Images },
    { label: "Готовых PDF", value: state.totals.documents, Icon: Files },
    { label: "Без подписи", value: state.totals.unsigned, Icon: Tag },
    { label: "На проверку", value: state.totals.review, Icon: ScanEye },
  ];

  return <div className="processing-page">
    <header className="page-heading processing-heading">
      <div>
        <h1>Обработка PDF</h1>
        <p>Извлеките изображения. Проверьте подписи. Соберите варианты.</p>
      </div>
      <button className="btn secondary" onClick={onPrevious} disabled={waiting}><FolderOpen size={16} />Открыть прошлый результат</button>
    </header>

    {failures}

    <div className={`processing-layout${view.showJob && !processing ? " has-next-pdf" : ""}`}>
    <section className={`processing-operation${view.showJob ? ` job-panel is-${view.outcome}` : ""}`} data-outcome={view.outcome} aria-labelledby={view.showJob ? "job-status" : "new-processing-title"}>
      <div className="processing-operation-body">
        {view.showJob ? <>
          <div className="processing-operation-header">
            <span className="processing-status-icon">
              {processing ? <LoaderCircle size={25} className="spin" /> : complete ? <BadgeCheck size={25} /> : view.outcome === "cancelled" ? <CircleStop size={25} /> : <TriangleAlert size={25} />}
            </span>
            <div className="processing-status-copy">
              <h2 id="job-status" role="status">{view.title}</h2>
              {view.subtitle && <p>{view.subtitle}</p>}
            </div>
            {processing ? <button className="btn secondary processing-main-action" onClick={onCancel} disabled={waiting || state.cancelling}>
              <Square size={16} />{state.cancelling ? "Останавливаю…" : "Остановить обработку"}
            </button> : view.canOpenResult && <button className={`btn ${complete ? "primary" : "secondary"} processing-main-action`} disabled={waiting} onClick={onOpenResult}>
              <ArrowUpRight size={16} />{complete ? "Открыть результат" : "Открыть сохранённый результат"}
            </button>}
          </div>

          {processing ? (view.fileName || view.phase) && <div className="processing-file-panel">
            {view.fileName && <p className="processing-file-line"><FileText size={17} /><span className="processing-file-name">{view.fileName}</span></p>}
            {view.phase && <p className="processing-phase">{view.phase}</p>}
          </div> : view.fileName && <div className="processing-finished-file">
            {(!complete || (state.job?.total ?? 0) > 1) && <p className="processing-file-context">Последний сохранённый результат</p>}
            <p className="processing-file-line"><FileText size={17} /><span className="processing-file-name">{view.fileName}</span></p>
          </div>}

          <div className="processing-progress">
            <div className="processing-progress-label"><p>{state.status}</p><strong>{view.percentage}%</strong></div>
            <div className="progress-track" role="progressbar" aria-label="Обработка PDF" aria-valuenow={view.percentage} aria-valuemin={0} aria-valuemax={100} aria-valuetext={`${state.status} · ${view.percentage}%`}>
              <div style={{ transform: `scaleX(${view.progress / 100})` }} />
            </div>
          </div>

          <div className="job-totals">
            {totals.map(({ label, value, Icon }, index) => <div key={label} className={complete && index === 1 ? "processing-total-ready" : ""}>
              <span className="processing-total-label"><span>{label}</span><Icon size={15} /></span>
              <strong>{value}</strong>
            </div>)}
          </div>

          {attention && <div className={`processing-review${view.errors > 0 || view.failed > 0 ? " has-errors" : ""}`} role="note">
            <TriangleAlert size={18} />
            <p>{reviewParts.length > 0 ? `Проверьте вручную: ${reviewParts.join(" · ")}` : view.errors > 0 || view.failed > 0 ? "Проверьте ошибки обработки. Готовые результаты доступны в истории." : "Есть предупреждения. Подробности — в событиях обработки."}</p>
            {countNotes.length > 0 && <span>{countNotes.join(" · ")}</span>}
          </div>}
          <div className="processing-body-footer">
            <OriginalsNote />
            {processing && view.canOpenResult && <button className="text-btn processing-live-result" onClick={onOpenResult} disabled={waiting}><ArrowUpRight size={15} />Открыть сохранённый результат</button>}
          </div>
        </> : <>
          <div className="processing-new-title"><h2 id="new-processing-title">Новая обработка</h2><p>PDF → изображения и варианты</p></div>
          <UploadZone fileDrag={fileDrag} waiting={waiting} onChoose={onChoose} />
          <OriginalsNote />
        </>}
      </div>
      <footer className="destination processing-destination">
        <FolderOutput size={20} />
        <div><span>Сохранять</span><strong title={state.settings.folder || state.settings.label}>{destination}</strong></div>
        {processing && <p className="processing-settings-lock">Недоступно во время обработки</p>}
        <button className="btn secondary" onClick={onSettings} disabled={processing || waiting}>Изменить</button>
      </footer>
    </section>

    {view.showJob && !processing && <aside className="processing-next-panel" aria-labelledby="next-processing-title">
      <h2 id="next-processing-title"><FilePlus2 size={18} />Новая обработка</h2>
      <UploadZone fileDrag={fileDrag} waiting={waiting} onChoose={onChoose} />
      <p className="processing-next-local"><ShieldCheck size={14} /><span>Обработка на вашем компьютере</span></p>
    </aside>}
    </div>

    {state.logs.length > 0 && <section className="events processing-events" aria-label="События обработки">
      <details open={eventsOpen}>
        <summary><ListCollapse size={17} /><strong>События обработки</strong><span className="processing-events-count">{state.logs.length}</span><ChevronUp className="processing-events-chevron" size={16} /></summary>
        <ol className="processing-log">
          {state.logs.map((item) => <li key={item.id} className={`log-${["head", "ok", "warn", "error", "muted"].includes(item.level) ? item.level : "muted"}`}>
            {item.level === "head" && <FileText size={16} />}<span>{item.text}</span>
          </li>)}
        </ol>
      </details>
    </section>}

    {state.history.length > 0 && <section className="recent processing-recent" aria-labelledby="recent-results-title">
      <div className="section-title"><h2 id="recent-results-title">Последние результаты</h2><button className="text-btn" onClick={onHistory}>Вся история<ArrowRight size={14} /></button></div>
      <div className="processing-recent-list">
        {state.history.slice(0, 3).map((item) => {
          const current = !processing && item.id === view.currentRecord?.id;
          return <button className="history-row processing-history-row" key={item.id} disabled={waiting || !item.available} onClick={() => onOpenHistory(item)}>
            <span className="file-icon"><FileText size={21} /></span>
            <span className="history-name"><strong>{item.name}</strong>
              <small>{item.images} {plural(item.images, ["изображение", "изображения", "изображений"])} · {current || !item.variants ? `${item.documents} PDF` : `${item.variants} ${plural(item.variants, ["вариант", "варианта", "вариантов"])}`}{!current && ` · ${dateLabel(item.finished_at)}`}</small>
              {!item.available && <small className="history-unavailable">Папка недоступна — укажите новое расположение в истории</small>}
            </span>
            <ChevronRight size={18} />
          </button>;
        })}
      </div>
    </section>}

    <p className="processing-privacy"><ShieldCheck size={15} /><span>Локально, без отправки в интернет</span></p>
  </div>;
}
