import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, MouseEvent, ReactNode } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Check,
  CheckSquare,
  ChevronRight,
  Clock3,
  ExternalLink,
  FileText,
  FolderOpen,
  GripVertical,
  ImageOff,
  Images,
  Layers3,
  LoaderCircle,
  Pencil,
  Plus,
  RotateCcw,
  Save,
  Search,
  Tag,
  Trash2,
  TriangleAlert,
  Undo2,
} from "lucide-react";
import { ApiError, errorText, fileUrl, native, request } from "./api";
import { Dialog, Empty, Shell, StatusNote } from "./components";
import {
  createDraft,
  draftVariant,
  editDraft,
  isDirty,
  orderedIds,
  payload,
  selectRange,
} from "./editor";
import type { Draft } from "./editor";
import type { Result } from "./types";

type Rename = {
  kind: "variant" | "folder" | "file";
  target: string | number;
  name: string;
};
const STATUS = {
  named: "С подписью",
  review: "На проверку",
  unsigned: "Без подписи",
};
let dirtySequence = Date.now() * 1000;
export function Viewer({
  result,
  footer,
  notify,
  onResult,
  onDirty,
  onBack,
}: {
  result: Result;
  footer: ReactNode;
  notify(text: string, error?: boolean): void;
  onResult(result: Result): void;
  onDirty(dirty: boolean): void;
  onBack(): void;
}) {
  const initial = useMemo(() => createDraft(result.report), [result]);
  const [draft, setDraft] = useState<Draft>(initial);
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [filter, setFilter] = useState("all");
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [rename, setRename] = useState<Rename | null>(null);
  const [newVariant, setNewVariant] = useState(false);
  const [number, setNumber] = useState("1");
  const [moveTarget, setMoveTarget] = useState("");
  const [preview, setPreview] = useState<number | null>(null);
  const [reset, setReset] = useState(false);
  const finishEditing = useRef(false);
  const anchor = useRef<number | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const dragging = useRef<number[]>([]);
  const dirty = isDirty(draft, initial);
  const { report } = result;
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);
  useEffect(() => {
    setDraft(initial);
    setSelected([]);
    setConflict(false);
  }, [initial]);
  useEffect(() => {
    onDirty(dirty);
    const bridge = window.pywebview?.api;
    if (bridge && "set_unsaved" in bridge)
      void (
        bridge as typeof bridge & {
          set_unsaved(value: boolean, sequence: number): Promise<boolean>;
        }
      )
        .set_unsaved(dirty, ++dirtySequence)
        .catch(() => {});
    return () => {
      onDirty(false);
      if (bridge && "set_unsaved" in bridge)
        void (
          bridge as typeof bridge & {
            set_unsaved(value: boolean, sequence: number): Promise<boolean>;
          }
        )
          .set_unsaved(false, ++dirtySequence)
          .catch(() => {});
    };
  }, [dirty, onDirty]);
  useEffect(() => {
    setSelected([]);
    anchor.current = null;
  }, [filter, status, query]);
  const rows = useMemo(
    () =>
      orderedIds(draft).map((id) => {
        const image = report.images[id];
        const variant = draftVariant(draft, id);
        const moved = variant !== image.variant;
        return {
          id,
          image,
          variant,
          folder:
            variant === null
              ? image.folder
              : report.variant_names?.[String(variant)] || `Вариант ${variant}`,
          status: moved ? "named" : image.status,
        };
      }),
    [draft, report],
  );
  const variants = useMemo(
    () => Object.keys(draft.variants).sort((a, b) => Number(a) - Number(b)),
    [draft],
  );
  const folders = useMemo(
    () =>
      [
        ...new Set(
          rows
            .filter(
              (row) => row.variant === null && row.image.status === "named",
            )
            .map((row) => row.folder),
        ),
      ].sort((a, b) => a.localeCompare(b, "ru")),
    [rows],
  );
  const visible = rows.filter((row) => {
    const matches =
      filter === "all" ||
      (filter === "needs-review" && row.status !== "named") ||
      filter === row.status ||
      filter === `variant:${row.variant}` ||
      filter === `folder:${row.folder}`;
    const text =
      `${row.image.title || ""} ${row.folder} ${row.image.page} ${row.image.file}`.toLocaleLowerCase(
        "ru",
      );
    return (
      matches &&
      (status === "all" || status === row.status) &&
      text.includes(query.trim().toLocaleLowerCase("ru"))
    );
  });
  const visibleIds = visible.map((row) => row.id);
  const counts = {
    named: rows.filter((row) => row.status === "named").length,
    review: rows.filter((row) => row.status === "review").length,
    unsigned: rows.filter((row) => row.status === "unsigned").length,
  };
  const activeTitle = filter.startsWith("variant:")
    ? report.variant_names?.[filter.slice(8)] || `Вариант ${filter.slice(8)}`
    : filter.startsWith("folder:")
      ? filter.slice(7)
      : filter === "needs-review"
        ? "Нужно проверить"
        : filter === "all"
          ? "Изображения"
          : STATUS[filter as keyof typeof STATUS];
  function select(
    id: number,
    event?: MouseEvent | React.ChangeEvent<HTMLInputElement>,
  ) {
    const click = event as MouseEvent | undefined;
    if (click?.shiftKey && anchor.current !== null)
      setSelected(selectRange(visibleIds, anchor.current, id));
    else if (click?.ctrlKey || click?.metaKey || event?.type === "change")
      setSelected((ids) =>
        ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id],
      );
    else setSelected((ids) => (ids.length === 1 && ids[0] === id ? [] : [id]));
    anchor.current = id;
  }
  function change(edit: Parameters<typeof editDraft>[1]) {
    try {
      setDraft((previous) => editDraft(previous, edit));
      setSelected([]);
    } catch (error) {
      notify(errorText(error), true);
    }
  }
  function move(target: string, ids = selected, before?: number) {
    if (ids.length) {
      change({ type: "move", ids, target, before });
      setMoveTarget("");
    }
  }
  function reorder(direction: -1 | 1) {
    if (selected.length !== 1) return;
    const id = selected[0],
      key = draftVariant(draft, id);
    if (key === null) return;
    const members = draft.variants[String(key)],
      position = members.indexOf(id);
    if (position + direction < 0 || position + direction >= members.length)
      return;
    const before =
      direction === -1 ? members[position - 1] : members[position + 2];
    setDraft((previous) =>
      editDraft(previous, {
        type: "move",
        ids: [id],
        target: String(key),
        before,
      }),
    );
  }
  async function save() {
    setSaving(true);
    try {
      const value = await request<{ result: Result; message: string }>(
        `results/${result.id}/apply`,
        payload(draft, report.revision),
      );
      onResult(value.result);
      setSelected([]);
      onDirty(false);
      notify(value.message);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      notify(errorText(error), true);
    } finally {
      setSaving(false);
    }
  }
  async function renameItem(event: React.FormEvent) {
    event.preventDefault();
    if (!rename) return;
    setSaving(true);
    try {
      const value = await request<{ result: Result; message: string }>(
        `results/${result.id}/rename`,
        { revision: report.revision, ...rename },
      );
      onResult(value.result);
      setFilter("all");
      setRename(null);
      notify(value.message);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) setConflict(true);
      notify(errorText(error), true);
    } finally {
      setSaving(false);
    }
  }
  function open(relative = "") {
    void native("open_result", result.id, relative).catch((error) =>
      notify(errorText(error), true),
    );
  }
  function startDrag(event: DragEvent, id: number) {
    const ids = selected.includes(id) ? selected : [id];
    dragging.current = ids;
    setSelected(ids);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-pdf-photos", ids.join(","));
  }
  function allowDrop(event: DragEvent) {
    if (
      editing &&
      !saving &&
      event.dataTransfer.types.includes("application/x-pdf-photos")
    ) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
    }
  }
  function drop(event: DragEvent, target: string, before?: number) {
    if (
      !editing ||
      saving ||
      !event.dataTransfer.types.includes("application/x-pdf-photos")
    )
      return;
    event.preventDefault();
    event.stopPropagation();
    if (before !== undefined && dragging.current.includes(before)) return;
    move(target, dragging.current, before);
    dragging.current = [];
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const input = (event.target as HTMLElement).closest(
        "input,textarea,select,[contenteditable]",
      );
      if (document.querySelector("dialog[open]")) return;
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyK") {
        event.preventDefault();
        search.current?.focus();
        return;
      }
      if (!editing || input || saving) return;
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyA") {
        event.preventDefault();
        setSelected(visibleIds);
      }
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyZ") {
        event.preventDefault();
        change({ type: "undo" });
      }
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyS") {
        event.preventDefault();
        if (dirty) void save();
      }
      if (event.code === "Delete" && selected.length) {
        event.preventDefault();
        change({ type: "remove", ids: selected });
      }
      if (event.code === "Escape") setSelected([]);
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });
  const previewRow = rows.find((row) => row.id === preview);
  function stepPreview(direction: number) {
    const index = visibleIds.indexOf(preview!);
    if (index >= 0 && visibleIds[index + direction] !== undefined)
      setPreview(visibleIds[index + direction]);
  }
  function renameGroup() {
    if (filter.startsWith("variant:"))
      setRename({
        kind: "variant",
        target: filter.slice(8),
        name: activeTitle,
      });
    else if (filter.startsWith("folder:"))
      setRename({ kind: "folder", target: filter.slice(7), name: activeTitle });
  }
  function sidebar(
    key: string,
    title: string,
    count: number,
    icon: ReactNode,
    variant?: string,
  ) {
    return (
      <button
        key={key}
        className={`project-link ${filter === key ? "active" : ""}`}
        onClick={() => {
          setFilter(key);
          setStatus("all");
        }}
        onDragOver={variant ? allowDrop : undefined}
        onDrop={variant ? (event) => drop(event, variant) : undefined}
      >
        <span>
          {icon}
          <span className="nav-text">{title}</span>
        </span>
        <b>{count}</b>
      </button>
    );
  }
  return (
    <Shell
      motionKey={result.id}
      footer={footer}
      navigation={
        <>
          <button className="project-link back-link" onClick={onBack}>
            <span>
              <ArrowLeft size={16} />К результатам
            </span>
          </button>
          <div className="nav-label">Проект</div>
          {sidebar("all", "Все изображения", rows.length, <Images size={17} />)}
          <div className="nav-label spaced">Варианты</div>
          {variants.map((key, i) =>
            sidebar(
              `variant:${key}`,
              report.variant_names?.[key] || `Вариант ${key}`,
              draft.variants[key].length,
              <span className={`variant-dot color-${i % 4}`} />,
              key,
            ),
          )}
          {!variants.length && (
            <p className="nav-empty">Варианты пока не определены</p>
          )}
          <div className="nav-label spaced">Статус</div>
          {sidebar(
            "review",
            "На проверку",
            counts.review,
            <Clock3 size={17} />,
          )}
          {sidebar(
            "unsigned",
            "Без подписи",
            counts.unsigned,
            <Tag size={17} />,
          )}
          {!!folders.length && (
            <>
              <div className="nav-label spaced">Подписи</div>
              <div className="category-list">
                {folders.map((folder) =>
                  sidebar(
                    `folder:${folder}`,
                    folder,
                    rows.filter(
                      (row) => row.folder === folder && row.variant === null,
                    ).length,
                    null,
                  ),
                )}
              </div>
            </>
          )}
        </>
      }
      header={
        <>
          <label className="searchbox">
            <Search size={17} />
            <input
              ref={search}
              type="search"
              placeholder="Найти изображение или подпись"
              aria-label="Найти изображение или подпись"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <kbd>Ctrl K</kbd>
          </label>
          <div className="topbar-actions">
            <StatusNote busy={saving}>
              {saving
                ? "Сохраняю…"
                : dirty
                  ? "Есть изменения"
                  : "Изменения сохранены"}
            </StatusNote>
            <button
              className={`btn ${editing ? "primary" : "secondary"}`}
              disabled={saving}
              onClick={() => {
                if (editing && dirty) {
                  finishEditing.current = true;
                  setReset(true);
                } else {
                  setEditing(!editing);
                  setSelected([]);
                }
              }}
            >
              <Pencil size={16} />
              {editing ? "Завершить правки" : "Редактировать"}
            </button>
          </div>
        </>
      }
    >
      <div className="content viewer-content">
        <div className="breadcrumbs">
          <button onClick={onBack}>Результаты</button>
          <ChevronRight size={12} />
          <span title={result.title}>{result.title}</span>
        </div>
        <div className="page-heading viewer-heading">
          <div>
            <h1>{result.title}</h1>
            <p>
              {report.pages} страниц PDF · {report.documents.length} готовых PDF
            </p>
          </div>
          <button className="btn secondary" onClick={() => open()}>
            <FolderOpen size={16} />
            <span>Папка результата</span>
          </button>
        </div>
        <div className="stats" aria-label="Итоги обработки">
          <button
            className={filter === "all" ? "selected" : ""}
            onClick={() => {
              setFilter("all");
              setStatus("all");
            }}
          >
            <span className="stat-icon blue">
              <Images size={20} />
            </span>
            <span>
              <strong>{rows.length}</strong>
              <small>Изображений</small>
            </span>
          </button>
          <div>
            <span className="stat-icon violet">
              <FileText size={20} />
            </span>
            <span>
              <strong>{report.pages}</strong>
              <small>Страниц PDF</small>
            </span>
          </div>
          <div>
            <span className="stat-icon teal">
              <Layers3 size={20} />
            </span>
            <span>
              <strong>
                {variants.filter((key) => draft.variants[key].length).length}
              </strong>
              <small>Вариантов</small>
            </span>
          </div>
          <button
            className={filter === "review" ? "selected" : ""}
            onClick={() => {
              setFilter("review");
              setStatus("all");
            }}
          >
            <span className="stat-icon amber">
              <Clock3 size={20} />
            </span>
            <span>
              <strong>{counts.review}</strong>
              <small>На проверку</small>
            </span>
          </button>
          <button
            className={filter === "unsigned" ? "selected" : ""}
            onClick={() => {
              setFilter("unsigned");
              setStatus("all");
            }}
          >
            <span className="stat-icon rose">
              <Tag size={20} />
            </span>
            <span>
              <strong>{counts.unsigned}</strong>
              <small>Без подписи</small>
            </span>
          </button>
        </div>
        {!!(counts.review + counts.unsigned) && (
          <div className="review-banner">
            <span className="review-icon">
              <TriangleAlert size={20} />
            </span>
            <div>
              <strong>
                Нужно проверить: {counts.review + counts.unsigned}
              </strong>
              <span>
                Проверьте подписи и перенесите изображения в нужный вариант.
              </span>
            </div>
            <button
              onClick={() => {
                setFilter("needs-review");
                setStatus("all");
              }}
            >
              Начать проверку
              <ChevronRight size={16} />
            </button>
          </div>
        )}
        {!!(report.errors?.length || report.warnings?.length) && (
          <details className="report-notes">
            <summary>
              <TriangleAlert size={15} />
              Предупреждения: {report.warnings?.length || 0} · Ошибки:{" "}
              {report.errors?.length || 0}
            </summary>
            <p>Подробности сохранены в «Отчёт.json» в папке результата.</p>
            <pre>
              {JSON.stringify(
                { warnings: report.warnings, errors: report.errors },
                null,
                2,
              )}
            </pre>
          </details>
        )}
        {editing && (
          <section className="edit-panel" aria-label="Инструменты редактора">
            <div className="edit-panel-top">
              <span>
                <Pencil size={16} />
                <strong>
                  {dirty
                    ? "Правки ещё не сохранены"
                    : "Редактирование результата"}
                </strong>
              </span>
              <div>
                <button
                  className="btn compact secondary"
                  disabled={!draft.past.length || saving}
                  onClick={() => change({ type: "undo" })}
                >
                  <Undo2 size={15} />
                  Отменить шаг
                </button>
                <button
                  className="btn compact secondary"
                  disabled={!dirty || saving}
                  onClick={() => setReset(true)}
                >
                  <RotateCcw size={15} />
                  Сбросить
                </button>
                <button
                  className="btn compact primary"
                  disabled={!dirty || saving}
                  onClick={() => void save()}
                >
                  {saving ? (
                    <LoaderCircle size={16} className="spin" />
                  ) : (
                    <Save size={16} />
                  )}
                  Сохранить
                </button>
              </div>
            </div>
            <p>
              Выберите карточки, чтобы перенести или убрать. Порядок в варианте
              можно менять перетаскиванием.
            </p>
            <div className="selection-tools">
              <button
                className="btn compact secondary"
                disabled={saving || !visible.length}
                onClick={() =>
                  setSelected(
                    selected.length === visible.length ? [] : visibleIds,
                  )
                }
              >
                <CheckSquare size={15} />
                {selected.length === visible.length && visible.length
                  ? "Снять выделение"
                  : "Выбрать видимые"}
              </button>
              <span className="selection-count">
                Выбрано: {selected.length}
              </span>
              <select
                aria-label="Перенести в вариант"
                value={moveTarget}
                disabled={!selected.length || saving}
                onChange={(e) => {
                  setMoveTarget(e.target.value);
                  if (e.target.value) move(e.target.value);
                }}
              >
                <option value="">Перенести в вариант…</option>
                {variants.map((key) => (
                  <option key={key} value={key}>
                    {report.variant_names?.[key] || `Вариант ${key}`}
                  </option>
                ))}
              </select>
              <button
                className="btn compact secondary"
                disabled={!selected.length || saving}
                onClick={() => {
                  setNumber(String(Math.max(0, ...variants.map(Number)) + 1));
                  setNewVariant(true);
                }}
              >
                <Plus size={15} />
                Новый вариант
              </button>
              <button
                className="icon-btn"
                title="Раньше в варианте"
                aria-label="Раньше в варианте"
                disabled={
                  selected.length !== 1 ||
                  draftVariant(draft, selected[0]) === null ||
                  saving
                }
                onClick={() => reorder(-1)}
              >
                <ArrowUp size={17} />
              </button>
              <button
                className="icon-btn"
                title="Позже в варианте"
                aria-label="Позже в варианте"
                disabled={
                  selected.length !== 1 ||
                  draftVariant(draft, selected[0]) === null ||
                  saving
                }
                onClick={() => reorder(1)}
              >
                <ArrowDown size={17} />
              </button>
              <button
                className="btn compact danger-quiet"
                disabled={!selected.length || saving}
                onClick={() => change({ type: "remove", ids: selected })}
              >
                <Trash2 size={15} />
                Убрать
              </button>
            </div>
            {!!draft.removed.length && (
              <span className="muted">
                Убрано из результата: {draft.removed.length}. До сохранения
                можно отменить шаг или сбросить правки.
              </span>
            )}
            {conflict && (
              <div className="inline-warning">
                <TriangleAlert size={17} />
                <span>
                  Результат изменён в другом окне. Загрузите актуальную версию и
                  повторите правки.
                </span>
                <button className="text-btn" onClick={() => setReset(true)}>
                  Обновить
                </button>
              </div>
            )}
          </section>
        )}
        <div className="library-header">
          <div>
            <h2>{activeTitle}</h2>
            <span>
              {visible.length} из {rows.length}
            </span>
            {editing &&
              (filter.startsWith("variant:") ||
                filter.startsWith("folder:")) && (
                <button
                  className="icon-btn"
                  aria-label="Переименовать группу"
                  title={
                    dirty
                      ? "Сначала сохраните или сбросьте правки"
                      : "Переименовать группу"
                  }
                  disabled={dirty || saving}
                  onClick={renameGroup}
                >
                  <Pencil size={15} />
                </button>
              )}
          </div>
          <div
            className="filter-tabs"
            role="group"
            aria-label="Фильтр по статусу"
          >
            {[
              ["all", "Все изображения"],
              ["named", "С подписью"],
              ["review", "На проверку"],
              ["unsigned", "Без подписи"],
            ].map(([key, title]) => (
              <button
                key={key}
                className={status === key ? "active" : ""}
                aria-pressed={status === key}
                onClick={() => setStatus(key)}
              >
                {title}
              </button>
            ))}
          </div>
        </div>
        {visible.length ? (
          <div className="asset-grid">
            {visible.map((row) => (
              <article
                key={row.id}
                data-id={row.id}
                className={`asset-card ${selected.includes(row.id) ? "selected-card" : ""}`}
                draggable={editing && !saving}
                onDragStart={(e) => startDrag(e, row.id)}
                onDragEnd={() => {
                  dragging.current = [];
                }}
                onDragOver={row.variant !== null ? allowDrop : undefined}
                onDrop={
                  row.variant !== null
                    ? (e) => drop(e, String(row.variant), row.id)
                    : undefined
                }
              >
                <div className="asset-image">
                  <button
                    className="cover-button"
                    aria-label={`${editing ? "Выбрать" : "Просмотреть"}: ${row.image.title || row.folder}, страница ${row.image.page}`}
                    disabled={saving}
                    onClick={(e) =>
                      editing ? select(row.id, e) : setPreview(row.id)
                    }
                    onDoubleClick={() => {
                      if (editing) setPreview(row.id);
                    }}
                  >
                    {row.image.preview ? (
                      <img
                        draggable={false}
                        src={fileUrl(result.base_url, row.image.preview)}
                        alt={row.image.title || row.folder}
                        loading="lazy"
                        decoding="async"
                      />
                    ) : (
                      <span className="no-preview">
                        <ImageOff size={30} />
                        Без миниатюры
                      </span>
                    )}
                  </button>
                  {editing && (
                    <label className="pick">
                      <input
                        type="checkbox"
                        aria-label={`Выбрать изображение ${row.id + 1}`}
                        checked={selected.includes(row.id)}
                        disabled={saving}
                        onChange={(e) => select(row.id, e)}
                      />
                      <span />
                    </label>
                  )}
                  <span className={`status-badge ${row.status}`}>
                    {row.status === "named" ? (
                      <Check size={12} />
                    ) : row.status === "review" ? (
                      <Clock3 size={12} />
                    ) : (
                      <Tag size={12} />
                    )}
                    {STATUS[row.status as keyof typeof STATUS]}
                  </span>
                  {editing && (
                    <span className="drag-handle" title="Перетащите карточку">
                      <GripVertical size={16} />
                    </span>
                  )}
                </div>
                <div className="asset-info">
                  <span className="asset-group" title={row.folder}>
                    {row.folder}
                  </span>
                  <div className="asset-title">
                    <button
                      onClick={() => setPreview(row.id)}
                      title={row.image.title || row.folder}
                    >
                      {row.image.title || row.folder}
                    </button>
                    {editing && row.variant === null && (
                      <button
                        className="icon-btn"
                        aria-label={`Переименовать PDF ${row.id + 1}`}
                        disabled={dirty || saving}
                        onClick={() =>
                          setRename({
                            kind: "file",
                            target: row.id,
                            name:
                              row.image.file
                                .replaceAll("\\", "/")
                                .split("/")
                                .at(-1)
                                ?.replace(/\.pdf$/i, "") || "",
                          })
                        }
                      >
                        <Pencil size={13} />
                      </button>
                    )}
                  </div>
                  <div className="asset-meta">
                    <span>Страница {row.image.page}</span>
                    <span>{row.image.size?.join(" × ")}</span>
                  </div>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            icon={<Images size={32} />}
            title="Изображения не найдены"
            action={
              <button
                className="btn secondary"
                onClick={() => {
                  setFilter("all");
                  setStatus("all");
                  setQuery("");
                }}
              >
                Показать все изображения
              </button>
            }
          >
            Измените поиск или фильтр. Убранные изображения можно вернуть до
            сохранения.
          </Empty>
        )}
      </div>
      {previewRow && (
        <Dialog
          wide
          title={previewRow.image.title || previewRow.folder}
          onClose={() => setPreview(null)}
        >
          <div className="preview-stage">
            <button
              className="icon-btn"
              aria-label="Предыдущее изображение"
              disabled={visibleIds.indexOf(previewRow.id) <= 0}
              onClick={() => stepPreview(-1)}
            >
              <ArrowLeft size={22} />
            </button>
            {previewRow.image.preview ? (
              <img
                src={fileUrl(result.base_url, previewRow.image.preview)}
                alt={previewRow.image.title || previewRow.folder}
              />
            ) : (
              <ImageOff size={48} />
            )}
            <button
              className="icon-btn"
              aria-label="Следующее изображение"
              disabled={
                visibleIds.indexOf(previewRow.id) >= visibleIds.length - 1
              }
              onClick={() => stepPreview(1)}
            >
              <ArrowRight size={22} />
            </button>
          </div>
          <div className="preview-footer">
            <span>
              Страница {previewRow.image.page} ·{" "}
              {previewRow.image.size?.join(" × ")} px
              {previewRow.image.reason && (
                <small>{previewRow.image.reason}</small>
              )}
            </span>
            <button
              className="btn primary"
              onClick={() => open(previewRow.image.file)}
            >
              <ExternalLink size={16} />
              Открыть PDF
            </button>
          </div>
        </Dialog>
      )}
      {rename && (
        <Dialog
          title="Переименовать"
          busy={saving}
          onClose={() => setRename(null)}
        >
          <form onSubmit={(e) => void renameItem(e)}>
            <label className="form-label" htmlFor="rename-input">
              Новое имя
            </label>
            <input
              autoFocus
              id="rename-input"
              className="form-input"
              value={rename.name}
              maxLength={120}
              onChange={(e) => setRename({ ...rename, name: e.target.value })}
            />
            <p className="muted">
              Имя папки и связанного PDF обновится в результате.
            </p>
            <div className="dialog-actions">
              <button
                type="button"
                className="btn secondary"
                disabled={saving}
                onClick={() => setRename(null)}
              >
                Отмена
              </button>
              <button
                className="btn primary"
                disabled={saving || !rename.name.trim()}
              >
                <Pencil size={16} />
                Переименовать
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {newVariant && (
        <Dialog
          title="Перенести в новый вариант"
          onClose={() => setNewVariant(false)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!/^\d+$/.test(number) || +number < 1 || +number > 999) {
                notify("Номер варианта должен быть от 1 до 999.", true);
                return;
              }
              if (variants.includes(String(Number(number)))) {
                notify(
                  "Такой вариант уже существует. Выберите его в списке переноса.",
                  true,
                );
                return;
              }
              move(number);
              setFilter(`variant:${Number(number)}`);
              setStatus("all");
              setNewVariant(false);
            }}
          >
            <label htmlFor="variant-number" className="form-label">
              Номер варианта
            </label>
            <input
              autoFocus
              id="variant-number"
              className="form-input"
              type="number"
              min="1"
              max="999"
              required
              value={number}
              onChange={(e) => setNumber(e.target.value)}
            />
            <p className="muted">
              Выбранных изображений: {selected.length}. PDF будет создан после
              сохранения.
            </p>
            <div className="dialog-actions">
              <button
                type="button"
                className="btn secondary"
                onClick={() => setNewVariant(false)}
              >
                Отмена
              </button>
              <button className="btn primary">
                <Plus size={16} />
                Создать и перенести
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {reset && (
        <Dialog
          title={
            conflict
              ? "Загрузить актуальный результат?"
              : "Сбросить несохранённые правки?"
          }
          onClose={() => {
            finishEditing.current = false;
            setReset(false);
          }}
        >
          <p>
            Несохранённые перемещения, удаления и порядок будут отменены.
            Сохранённые файлы останутся на месте.
          </p>
          <div className="dialog-actions">
            <button
              className="btn secondary"
              onClick={() => {
                finishEditing.current = false;
                setReset(false);
              }}
            >
              Вернуться
            </button>
            <button
              className="btn danger"
              onClick={() => {
                setReset(false);
                if (conflict) {
                  setSaving(true);
                  void request<Result>(`results/${result.id}`)
                    .then(onResult)
                    .catch((error) => notify(errorText(error), true))
                    .finally(() => setSaving(false));
                } else {
                  setDraft(initial);
                  setSelected([]);
                  if (finishEditing.current) {
                    setEditing(false);
                    finishEditing.current = false;
                  }
                }
              }}
            >
              {conflict ? "Загрузить" : "Сбросить правки"}
            </button>
          </div>
        </Dialog>
      )}
    </Shell>
  );
}
