import { useId, useMemo, useState } from "react";
import { ArrowRight, Check, ChevronDown, Layers, ScanEye, ScanSearch, TriangleAlert } from "lucide-react";
import "./review-summary.css";

function issueFrom(value: unknown, error: boolean) {
  const data = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const number = (value: unknown) => Number.isInteger(Number(value)) && Number(value) > 0 ? Number(value) : null;
  const detail = typeof value === "string" ? value :
    [data.message, data.error, data.reason].find((text) => typeof text === "string" && text.trim()) as string | undefined;
  const scan = /ocr|распознав|скан/iu.test(detail || "");
  const message = scan ? error ? "Не удалось обработать изображение на скане" : "Подпись на скане не распознана"
    : data.fallback ? "Изображение сохранено снимком области страницы"
    : detail || (error ? "Не удалось извлечь изображение" : "Проверьте изображение вручную");
  const diagnostic = scan ? detail : data.fallback ? String(data.fallback) : undefined;
  return { page: number(data.page), image: number(data.image), message, detail: diagnostic, error };
}

interface Props {
  review: number;
  unsigned: number;
  warnings: unknown[];
  errors: unknown[];
  serviceCount: number;
  canRestoreService: boolean;
  onReview(): void;
  onService(): void;
  onReveal(page: number, sequence: number | null): void;
}

export function ReviewSummary({ review, unsigned, warnings, errors, serviceCount, canRestoreService, onReview, onService, onReveal }: Props) {
  const id = useId();
  const [open, setOpen] = useState(errors.length > 0);
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set());
  const issues = useMemo(() => [...errors.map((item) => issueFrom(item, true)),
    ...warnings.map((item) => issueFrom(item, false))], [warnings, errors]);
  const toggle = (index: number) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(index)) next.delete(index); else next.add(index);
    return next;
  });
  const reviewCount = review + unsigned;
  const parts = [review && `${review} на проверку`, unsigned && `${unsigned} без подписи`].filter(Boolean).join(" и ");
  // Navigation already lists every count, so the panel keeps one task and only the diagnostics that exist.
  const diagnostics = issues.length > 0 || serviceCount > 0;
  return <section className={`review-summary${reviewCount ? "" : " is-complete"}`} aria-label="Сводка проверки">
    <div className="review-summary-header">
      <span className="review-summary-icon" aria-hidden="true">{reviewCount ? <ScanEye size={18} /> : <Check size={18} />}</span>
      <div className="review-summary-task">
        <strong className="review-summary-count">{reviewCount ? `Нужно проверить: ${reviewCount}` : "Всё распределено"}</strong>
        <p className="review-summary-help">{reviewCount ? `${parts}. Проверьте подписи и перенесите изображения в нужный вариант.`
          : "Изображений для распределения нет. Можно просмотреть готовые варианты."}</p>
      </div>
      {!!reviewCount && <button className="btn primary review-summary-start" onClick={onReview}>
        Начать проверку<ArrowRight size={14} />
      </button>}
    </div>
    {diagnostics && <div className="review-summary-diagnostics">
      {!!warnings.length && <button className="review-summary-warnings" aria-expanded={open}
        aria-controls={`${id}-issues`} onClick={() => setOpen((value) => !value)}>
        <TriangleAlert size={14} />Предупреждения: {warnings.length}<ChevronDown size={14} />
      </button>}
      {!!errors.length && <button className="review-summary-errors" aria-expanded={open}
        aria-controls={`${id}-issues`} onClick={() => setOpen((value) => !value)}>
        <TriangleAlert size={14} />Ошибки: {errors.length}<ChevronDown size={14} />
      </button>}
      {!!serviceCount && <span className="review-summary-services">
        <Layers size={14} /><span>Пропущены служебные элементы: <strong>{serviceCount}</strong></span>
        {canRestoreService && <button className="text-btn" onClick={onService}>Посмотреть и вернуть</button>}
      </span>}
    </div>}
    <div id={`${id}-issues`} hidden={!open}>
      {!!warnings.length && <p className="review-summary-recommendation">{warnings.length === 1
        ? "Проверьте это изображение вручную." : `Для всех ${warnings.length} предупреждений: проверьте изображения вручную.`}</p>}
      <ul className="review-issue-list">
        {issues.map((issue, index) => <li key={index} className={`review-issue${issue.error ? " is-error" : ""}`}>
          <div className="review-issue-row">
            <strong className="review-issue-page">{issue.page ? `Стр. ${issue.page}` : issue.error ? "Ошибка" : "PDF"}</strong>
            <span className="review-issue-message">{issue.message}</span>
            <div className="review-issue-actions">
              {issue.page && <button className="text-btn review-issue-find" onClick={() => onReveal(issue.page!, issue.image)}>
                <ScanSearch size={14} />Найти изображение
              </button>}
              {issue.detail && <button className="text-btn review-issue-toggle"
                aria-label={`Подробности ${issue.error ? "ошибки" : "предупреждения"}${issue.page ? ` на странице ${issue.page}` : ` ${index + 1}`}`}
                aria-expanded={expanded.has(index)} aria-controls={`${id}-detail-${index}`} onClick={() => toggle(index)}>
                Подробности<ChevronDown size={14} />
              </button>}
            </div>
          </div>
          {issue.detail && <div id={`${id}-detail-${index}`} className="review-issue-detail" hidden={!expanded.has(index)}>
            <p>{issue.detail}</p>
          </div>}
        </li>)}
      </ul>
    </div>
  </section>;
}
