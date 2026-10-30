import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowDown, ArrowDownToLine, ArrowUp, ArrowUpToLine, Check, CircleHelp, Copy, CopyCheck, Crop, Ellipsis, FolderInput,
  LoaderCircle, Plus, RotateCcw, Save, Scissors, SquarePen, Trash2, TriangleAlert, Undo2,
} from "lucide-react";
import { ActionMenu, Dialog } from "./components";
import type { ActionMenuItem } from "./components";
import { DOCK_MOTION, Presence, useMotion, usePresence } from "./motion";

interface Props {
  selected: number;
  visibleSelected: number;
  visible: number;
  variants: { value: string; label: string }[];
  dirty: boolean;
  saving: boolean;
  canUndo: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  canMoveEdge: boolean;
  canReturn: boolean;
  selectedRemoved: number;
  selectedActive: number;
  removed: number;
  canUndoSave: boolean;
  conflict: boolean;
  onSelectVisible(): void;
  onClearSelection(): void;
  onMove(target: string): void;
  onNewVariant(): void;
  onReorder(direction: -1 | 1): void;
  onMoveEdge(edge: "start" | "end"): void;
  onReturn(): void;
  onRestore(): void;
  onShowRemoved(): void;
  onUndoSave(): void;
  onRemove(): void;
  onCopy(): void;
  /** Unselected images of the same pixel size as the selection: a key plan or a title strip repeated on every slide. */
  sameSize: number;
  onSelectSameSize(): void;
  /** Open the cut editor for the one selected picture. */
  onCut(): void;
  canCut: boolean;
  /** Open the page editor: on the one selected picture, else on the first page. */
  onCutout(): void;
  onUndo(): void;
  onReset(): void;
  onSave(): void;
}

export function EditorToolbar(props: Props) {
  const anchor = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const [floating, setFloating] = useState(false);
  const [inlineHeight, setInlineHeight] = useState<number>();
  const [help, setHelp] = useState(false);
  const floatingRef = useRef(false);
  floatingRef.current = floating;
  // The panel fades in with editing; when editing ends the floating dock sinks away, the inline one goes at once
  // so the gallery does not wait under an empty space.
  const presence = usePresence();
  useMotion(panel, presence.show, floating ? DOCK_MOTION : { ...DOCK_MOTION, exit: 0 }, presence.exited);
  useLayoutEffect(() => {
    const element = panel.current!;
    const measure = () => {
      if (!floatingRef.current) setInlineHeight(element.getBoundingClientRect().height);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const header = document.querySelector(".topbar")!;
    let observer: IntersectionObserver;
    const watch = () => {
      observer?.disconnect();
      const top = header.getBoundingClientRect().height;
      observer = new IntersectionObserver(([entry]) => {
        setFloating(entry.boundingClientRect.top < top && !entry.isIntersecting);
      }, { rootMargin: `-${top}px 0px 0px 0px`, threshold: 0 });
      observer.observe(sentinel.current!);
    };
    watch();
    const resize = new ResizeObserver(watch);
    resize.observe(header);
    return () => { observer.disconnect(); resize.disconnect(); };
  }, []);
  const allSelected = props.visible > 0 && props.visibleSelected === props.visible;
  const hiddenSelected = props.selected - props.visibleSelected;
  const noSelection = !props.selected || props.saving;
  const moveItems: ActionMenuItem[] = [
    ...props.variants,
    { value: "return-to-misc", label: "Вернуть в папку подписи / Все остальное", separator: true, disabled: !props.canReturn },
  ];
  const actions: ActionMenuItem[] = [
    { value: "move", label: "Перенести в вариант", icon: <FolderInput size={16} />,
      disabled: noSelection,
      children: moveItems.map((variant) => ({ ...variant, value: `move:${variant.value}` })) },
    { value: "new", label: "Новый вариант", icon: <Plus size={16} />, disabled: noSelection },
    { value: "copy", label: "Создать копии выбранных", icon: <Copy size={16} />,
      disabled: props.saving || !props.selectedActive },
    { value: "same", label: props.sameSize ? `Выбрать такие же по размеру (${props.sameSize})` : "Выбрать такие же по размеру",
      icon: <CopyCheck size={16} />, disabled: props.saving || !props.sameSize },
    { value: "up", label: "Раньше в варианте", icon: <ArrowUp size={16} />,
      disabled: props.saving || !props.canMoveUp },
    { value: "down", label: "Позже в варианте", icon: <ArrowDown size={16} />,
      disabled: props.saving || !props.canMoveDown },
    { value: "start", label: "В начало варианта", icon: <ArrowUpToLine size={16} />,
      disabled: props.saving || !props.canMoveEdge },
    { value: "end", label: "В конец варианта", icon: <ArrowDownToLine size={16} />,
      disabled: props.saving || !props.canMoveEdge },
    { value: "restore", label: "Вернуть убранные изображения", icon: <RotateCcw size={16} />,
      disabled: props.saving || !props.selectedRemoved },
    { value: "remove", label: "Убрать", icon: <Trash2 size={16} />, disabled: props.saving || !props.selectedActive, danger: true },
    // Cutting stays below the everyday actions, so a low window still shows «Убрать» without scrolling.
    { value: "cut", label: "Разрезать склейку…", icon: <Scissors size={16} />, disabled: props.saving || !props.canCut, separator: true },
    { value: "cutout", label: "Вырезать часть со страницы…", icon: <Crop size={16} />, disabled: props.saving },
    { value: "undo", label: "Отменить шаг", icon: <Undo2 size={16} />,
      disabled: props.saving || !props.canUndo, separator: true },
    { value: "reset", label: "Сбросить", icon: <RotateCcw size={16} />,
      disabled: props.saving || !props.dirty },
    { value: "removed", label: `Показать убранные (${props.removed})`, icon: <Trash2 size={16} /> },
    { value: "undo-save", label: "Отменить последнее сохранение", icon: <RotateCcw size={16} />,
      disabled: props.saving || props.dirty || !props.canUndoSave, separator: true },
    { value: "help", label: "Помощь", icon: <CircleHelp size={16} />, separator: true },
  ];
  function action(value: string) {
    if (value === "move:return-to-misc") props.onReturn();
    else if (value.startsWith("move:")) props.onMove(value.slice(5));
    else if (value === "new") props.onNewVariant();
    else if (value === "copy") props.onCopy();
    else if (value === "same") props.onSelectSameSize();
    else if (value === "cut") props.onCut();
    else if (value === "cutout") props.onCutout();
    else if (value === "up") props.onReorder(-1);
    else if (value === "down") props.onReorder(1);
    else if (value === "start" || value === "end") props.onMoveEdge(value);
    else if (value === "restore") props.onRestore();
    else if (value === "remove") props.onRemove();
    else if (value === "undo") props.onUndo();
    else if (value === "reset") props.onReset();
    else if (value === "removed") props.onShowRemoved();
    else if (value === "undo-save") props.onUndoSave();
    else if (value === "help") setHelp(true);
  }
  return (
    <>
      <div ref={anchor} className="editor-tools-anchor" style={{ height: floating ? inlineHeight : undefined }}>
        <div ref={sentinel} className="editor-dock-sentinel" aria-hidden="true" />
        <section ref={panel} className={`edit-panel${floating ? " is-floating" : ""}`}
          aria-label="Инструменты редактора" aria-busy={props.saving}>
          <div className="edit-panel-top">
            <SquarePen size={16} aria-hidden="true" />
            <strong>Редактирование результата</strong>
          </div>
          <p className="editor-instructions">
            Выберите карточки, чтобы перенести или убрать. Порядок в варианте можно менять перетаскиванием.
          </p>
          <div className="selection-tools">
            <div className="selection-summary">
              <button className="btn compact secondary select-visible"
                aria-label={allSelected ? "Снять выделение" : "Выбрать видимые"}
                title={allSelected ? "Снять выделение только с видимых карточек" : "Добавить видимые карточки к выделению"}
                aria-pressed={allSelected} disabled={props.saving || !props.visible} onClick={props.onSelectVisible}>
                <span className={`toolbar-checkbox${allSelected ? " checked" : ""}`} aria-hidden="true">
                  {allSelected && <Check size={11} />}
                </span>
                <span>{allSelected ? "Снять выделение" : "Выбрать видимые"}</span>
              </button>
              <span className="selection-count" aria-live="polite">
                Выбрано: {props.selected}
                {hiddenSelected > 0 && <span className="selection-hidden"
                  title="Эти выбранные карточки скрыты текущим разделом или поиском. Действия применяются ко всему выделению.">
                  Скрыто: {hiddenSelected}
                </span>}
              </span>
              {!!props.selected && <button className="text-btn clear-selection" aria-label="Снять всё выделение"
                title="Снять всё выделение, включая скрытые карточки · Escape"
                disabled={props.saving} onClick={props.onClearSelection}>Снять всё</button>}
            </div>
            <ActionMenu className="editor-primary-action editor-move" label="Перенести в вариант"
              disabled={noSelection} items={moveItems} onAction={(value) => value === "return-to-misc" ? props.onReturn() : props.onMove(value)}
              upwards={floating} placementKey={floating} />
            <button className="btn compact secondary editor-primary-action editor-new" aria-label="Новый вариант"
              title="Новый вариант" disabled={noSelection} onClick={props.onNewVariant}>
              <Plus size={16} /><span>Новый вариант</span>
            </button>
            <div className="editor-reorder editor-primary-action">
              <button className="icon-btn" aria-label="Раньше в варианте" title="Раньше в варианте"
                disabled={props.saving || !props.canMoveUp} onClick={() => props.onReorder(-1)}><ArrowUp size={16} /></button>
              <button className="icon-btn" aria-label="Позже в варианте" title="Позже в варианте"
                disabled={props.saving || !props.canMoveDown} onClick={() => props.onReorder(1)}><ArrowDown size={16} /></button>
            </div>
            <button className="btn compact danger-quiet editor-primary-action editor-remove" aria-label={props.selectedRemoved && !props.selectedActive ? "Вернуть убранные изображения" : "Убрать"}
              title={props.selectedRemoved && !props.selectedActive ? "Вернуть выбранные убранные изображения" : "Убрать выбранные изображения"}
              disabled={noSelection} onClick={props.selectedRemoved && !props.selectedActive ? props.onRestore : props.onRemove}>
              {props.selectedRemoved && !props.selectedActive ? <RotateCcw size={16} /> : <Trash2 size={16} />}
              <span>{props.selectedRemoved && !props.selectedActive ? "Вернуть" : "Убрать"}</span>
            </button>
            <button className="btn compact secondary editor-primary-action editor-undo" aria-label="Отменить шаг"
              title="Отменить шаг · Ctrl+Z" disabled={props.saving || !props.canUndo} onClick={props.onUndo}>
              <Undo2 size={16} /><span>Отменить шаг</span>
            </button>
            <button className="btn compact secondary editor-primary-action editor-reset" aria-label="Сбросить"
              title="Сбросить все несохранённые правки" disabled={props.saving || !props.dirty} onClick={props.onReset}>
              <RotateCcw size={16} /><span>Сбросить</span>
            </button>
            <button className="icon-btn editor-primary-action editor-help" aria-label="Помощь по редактору"
              title="Помощь по редактору" onClick={() => setHelp(true)}><CircleHelp size={16} /></button>
            <ActionMenu className="editor-overflow" label="Ещё" icon={<Ellipsis size={16} />}
              heading="Редактирование результата" items={actions} onAction={action}
              disabled={props.saving} upwards={floating} placementKey={floating} />
            <button className="btn compact primary editor-save" disabled={props.saving || !props.dirty}
              onClick={props.onSave} title="Сохранить · Ctrl+S">
              {props.saving ? <LoaderCircle size={16} className="spin" /> : <Save size={16} />}
              <span>{props.saving ? "Сохраняю…" : "Сохранить"}</span>
            </button>
          </div>
          {!!props.removed && <span className="muted editor-removed">
            Убрано из результата: {props.removed}. <button className="text-btn" onClick={props.onShowRemoved}>Показать и вернуть</button>
          </span>}
          {props.conflict && <div className="inline-warning editor-conflict" role="alert">
            <TriangleAlert size={17} /><span>Результат изменён в другом окне. Загрузите актуальную версию и повторите правки.</span>
            <button className="text-btn" onClick={props.onReset}>Обновить</button>
          </div>}
        </section>
      </div>
      <Presence>{help && <Dialog title="Как редактировать результат" onClose={() => setHelp(false)}>
        <p>Флажки и Ctrl+щелчок добавляют карточки к выделению. Выбор сохраняется между разделами и при поиске. Shift+щелчок выбирает диапазон в текущем разделе, Ctrl+A добавляет все видимые карточки.</p>
        <p>Счётчик показывает всё выделение, в том числе скрытые карточки. Перенос и удаление применяются ко всему выбору. «Снять выделение» убирает выбор только с видимых карточек; «Снять всё» и Escape снимают всё выделение.</p>
        <p>Перенесите выбранные в существующий или новый вариант. В меню переноса можно вернуть их в папку подписи / «Все остальное». Стрелки меняют порядок одной страницы, а «Ещё → В начало / В конец варианта» перемещает всю выбранную группу.</p>
        <p>«Ещё → Создать копии выбранных» дублирует выбранные карточки на месте или в другом варианте. Кнопка «Копия PDF» рядом с названием варианта дублирует весь его PDF, включая скрытые поиском страницы. Оригиналы сохраняются. Копии записываются по кнопке «Сохранить»; Ctrl+Z отменяет создание до сохранения.</p>
        <p>«Ещё → Вырезать часть со страницы…» и «Вырезать часть…» на карточке открывают страницы исходного PDF: обведите рамкой нужную часть картинки, например рендер без схемы поверх него. Вырезка встаёт в вариант на место картинки, а сама картинка хранится в разделе «Вырезки», откуда её можно вернуть.</p>
        <p>«Ещё → Выбрать такие же по размеру» добавляет к выбору все изображения того же размера в пикселях — например, план участка или плашку заголовка, повторённые на каждом слайде, — чтобы убрать их одним действием.</p>
        <p>Delete убирает выбранные из результата. Раздел «Убранные» позволяет вернуть отдельные карточки и после сохранения. В «Ещё» можно отменить последнее сохранение, пока нет новых правок.</p>
        <p>Двойной щелчок по карточке открывает подробный просмотр. ←/→ листают изображения; 1–9 переносят открытое изображение в существующий вариант с этим номером. Колесо мыши и двойной щелчок меняют масштаб.</p>
        <p>Ctrl+Z отменяет шаг. «Сбросить» отменяет все несохранённые правки. Ctrl+S сохраняет изменения в PDF с резервной копией. Escape снимает выделение.</p>
        <div className="dialog-choices"><button className="btn primary" onClick={() => setHelp(false)}>Понятно</button></div>
      </Dialog>}</Presence>
    </>
  );
}
