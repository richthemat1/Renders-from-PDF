import { FileText, FolderOpen, FolderOutput, Info, ShieldCheck } from "lucide-react";
import type { Snapshot } from "./types";
import "./settings-visual.css";

type OutputMode = Snapshot["settings"]["mode"];

interface SettingsProps {
  state: Snapshot;
  waiting: boolean;
  onModeChange(mode: OutputMode): void;
  onChooseFolder(): void;
  onOpenLog(): void;
}

export function Settings({ state, waiting, onModeChange, onChooseFolder, onOpenLog }: SettingsProps) {
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
          <FolderOutput size={21} aria-hidden="true" />
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

      <section className="settings-panel settings-about" aria-labelledby="settings-about-title">
        <div className="settings-section-heading">
          <Info size={21} aria-hidden="true" />
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
