import { FileText, Files, FolderOpen, FolderOutput, Info, ShieldCheck, X, ZoomIn } from "lucide-react";
import { setSidebarPreferences, useSidebarPreferences, ZOOM_STEPS } from "./sidebar";
import type { Snapshot } from "./types";
import "./settings-visual.css";

type OutputMode = Snapshot["settings"]["mode"];

interface SettingsProps {
  state: Snapshot;
  waiting: boolean;
  onModeChange(mode: OutputMode): void;
  onChooseFolder(): void;
  onChooseDelivery(): void;
  onClearDelivery(): void;
  onOpenLog(): void;
}

export function Settings({ state, waiting, onModeChange, onChooseFolder, onChooseDelivery, onClearDelivery, onOpenLog }: SettingsProps) {
  const delivery = state.settings.delivery || "";
  const { zoom } = useSidebarPreferences();
  const options: { mode: OutputMode; label: string; detail: string }[] = [
    { mode: "nearby", label: "Рядом с исходным PDF", detail: "…\\Папка с PDF\\Название — PDF" },
    { mode: "results", label: "В подпапке «Результаты»", detail: "…\\Папка с PDF\\Результаты\\Название — PDF" },
    { mode: "custom", label: "В выбранной папке", detail: state.settings.folder || "Выберите папку на этом компьютере." },
  ];

  return (
    <div className="settings-page">
      <header className="settings-heading">
        <div className="settings-heading-main">
          <div>
            <h1>Настройки</h1>
            <p>Как сохранять следующие результаты обработки.</p>
          </div>
        </div>
      </header>

      <section className="settings-panel settings-output" aria-labelledby="settings-output-title">
        <div className="settings-section-heading">
          <FolderOutput size={20} aria-hidden="true" />
          <div>
            <h2 id="settings-output-title">Папка результатов</h2>
            <p id="settings-output-description">Каждая презентация сохраняется в отдельную папку. Уже готовые результаты не перемещаются.</p>
          </div>
        </div>
        <fieldset className="settings-options" disabled={state.busy || waiting} aria-describedby="settings-output-description">
          <legend className="sr-only">Расположение новых результатов</legend>
          {options.map(({ mode, label, detail }) => (
            <div className={`setting-option${state.settings.mode === mode ? " selected" : ""}${mode === "custom" ? " setting-option-custom" : ""}`} key={mode}>
              <label className="setting-option-label">
                <input
                  type="radio"
                  name="output"
                  value={mode}
                  checked={state.settings.mode === mode}
                  aria-labelledby={`output-${mode}-label`}
                  aria-describedby={`output-${mode}-detail`}
                  onChange={() => onModeChange(mode)}
                />
                <span className="setting-option-copy">
                  <strong id={`output-${mode}-label`}>{label}</strong>
                  <small id={`output-${mode}-detail`}>{detail}</small>
                </span>
              </label>
              {mode === "custom" && (
                <button type="button" className="settings-choose" onClick={onChooseFolder}>
                  <FolderOpen size={16} aria-hidden="true" />Выбрать
                </button>
              )}
            </div>
          ))}
        </fieldset>
        {state.busy && <p className="settings-busy-note">Расположение можно изменить после завершения обработки.</p>}
      </section>

      <section className="settings-panel settings-delivery" aria-labelledby="settings-delivery-title">
        <div className="settings-section-heading">
          <Files size={20} aria-hidden="true" />
          <div>
            <h2 id="settings-delivery-title">Готовые варианты</h2>
            <p id="settings-delivery-description">Чистые PDF вариантов без служебных файлов и вложенных папок. Программа кладёт их в эту папку после обработки и обновляет после каждого «Сохранить».</p>
          </div>
        </div>
        <div className={`setting-option settings-delivery-row${delivery ? " selected" : ""}`}>
          <span className="setting-option-copy">
            <strong>{delivery ? "Сохранять в папку" : "Не сохранять отдельно"}</strong>
            <small>{delivery ? `${delivery}\\Название презентации — Вариант 1.pdf` : "Варианты лежат только в папке результата. Отдельный вариант можно сохранить кнопкой «В папку…»."}</small>
          </span>
          {delivery && <button type="button" className="settings-choose settings-delivery-off" disabled={waiting} onClick={onClearDelivery}>
            <X size={16} aria-hidden="true" />Отключить
          </button>}
          <button type="button" className="settings-choose" disabled={waiting} aria-describedby="settings-delivery-description" onClick={onChooseDelivery}>
            <FolderOpen size={16} aria-hidden="true" />{delivery ? "Изменить" : "Выбрать папку"}
          </button>
        </div>
      </section>

      <section className="settings-panel settings-zoom" aria-labelledby="settings-zoom-title">
        <div className="settings-section-heading">
          <ZoomIn size={20} aria-hidden="true" />
          <div>
            <h2 id="settings-zoom-title">Масштаб интерфейса</h2>
            <p>Крупнее текст, кнопки и карточки во всём окне. Также Ctrl+плюс, Ctrl+минус и Ctrl+0.</p>
          </div>
        </div>
        <div className="settings-zoom-choices" role="group" aria-labelledby="settings-zoom-title">
          {ZOOM_STEPS.map((step) => <button key={step} type="button" className="btn compact secondary" aria-pressed={zoom === step}
            onClick={() => setSidebarPreferences({ zoom: step })}>{Math.round(step * 100)} %</button>)}
        </div>
      </section>

      <section className="settings-panel settings-about" aria-labelledby="settings-about-title">
        <div className="settings-section-heading">
          <Info size={20} aria-hidden="true" />
          <h2 id="settings-about-title">О приложении</h2>
        </div>
        <dl className="settings-details">
          <div className="settings-detail-row"><dt>Фото из PDF</dt><dd className="settings-about-version">{state.version}</dd></div>
          <div className="settings-detail-row">
            <dt>Обработка файлов</dt>
            <dd className="settings-local"><ShieldCheck size={16} aria-hidden="true" /><span>Локально, без отправки в интернет</span></dd>
          </div>
        </dl>
        <footer className="settings-about-footer">
          <button type="button" className="settings-log" disabled={waiting} onClick={onOpenLog}>
            <FileText size={16} aria-hidden="true" />Открыть технический журнал
          </button>
        </footer>
      </section>
    </div>
  );
}
