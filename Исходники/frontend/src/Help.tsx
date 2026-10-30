import { Fragment, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import {
  Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Clock3, Crop, Ellipsis, FileText, ImagePlus, Images,
  Keyboard, Layers, Lightbulb, Maximize2, Minus, Plus, Save, Scan, ScanEye, Scissors, Shapes, ShieldCheck, SquarePen,
  Tag, Trash2, TriangleAlert,
} from "lucide-react";
import { Dialog } from "./components";
import { useRefreshMotion } from "./motion";
import "./help.css";

export type HelpTopic = "start" | "review" | "edit" | "collages" | "cutouts" | "viewer" | "keys";

const TOPICS: { id: HelpTopic; label: string; icon: ReactNode }[] = [
  { id: "start", label: "Начало работы", icon: <ImagePlus size={16} /> },
  { id: "review", label: "Проверка результата", icon: <ScanEye size={16} /> },
  { id: "edit", label: "Редактирование", icon: <SquarePen size={16} /> },
  { id: "collages", label: "Склейки", icon: <Scissors size={16} /> },
  { id: "cutouts", label: "Вырезки", icon: <Crop size={16} /> },
  { id: "viewer", label: "Крупный просмотр", icon: <Maximize2 size={16} /> },
  { id: "keys", label: "Клавиши", icon: <Keyboard size={16} /> },
];

/** One help window for the whole app: the menu opens it on «Начало работы», the editor's «?» on «Редактирование».
 * Sections are tabs; the numbers on each section's sketch are the numbers of its steps. */
export function HelpDialog({ topic: initial, onClose, onOpenGuide }: {
  topic: HelpTopic;
  onClose(): void;
  onOpenGuide(): void;
}) {
  const [topic, setTopic] = useState(initial);
  const panel = useRef<HTMLDivElement>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  useRefreshMotion(panel, topic);
  // A new section starts from its top, not where the previous one was scrolled to.
  useLayoutEffect(() => { panel.current?.scrollTo(0, 0); }, [topic]);
  const index = TOPICS.findIndex((item) => item.id === topic);
  const go = (next: HelpTopic) => {
    setTopic(next);
    tabs.current[TOPICS.findIndex((item) => item.id === next)]?.focus();
  };
  function key(event: KeyboardEvent<HTMLDivElement>) {
    const last = TOPICS.length - 1;
    const next = event.key === "ArrowDown" || event.key === "ArrowRight" ? (index + 1) % TOPICS.length
      : event.key === "ArrowUp" || event.key === "ArrowLeft" ? (index + last) % TOPICS.length
        : event.key === "Home" ? 0 : event.key === "End" ? last : -1;
    if (next < 0) return;
    event.preventDefault();
    go(TOPICS[next].id);
  }
  const Section = SECTIONS[topic];
  return (
    <Dialog title="Как пользоваться" icon={<CircleHelp size={18} />} className="help-center" onClose={onClose}>
      <div className="help-body">
        <div className="help-nav">
          <div className="help-tabs" role="tablist" aria-label="Разделы справки" aria-orientation="vertical" onKeyDown={key}>
            {TOPICS.map((item, i) => (
              <button
                key={item.id}
                ref={(element) => { tabs.current[i] = element; }}
                className="help-tab"
                role="tab"
                id={`help-tab-${item.id}`}
                aria-selected={item.id === topic}
                aria-controls="help-panel"
                tabIndex={item.id === topic ? 0 : -1}
                data-autofocus={item.id === initial || undefined}
                onClick={() => setTopic(item.id)}
              >
                <span className="help-tab-icon" aria-hidden="true">{item.icon}</span>
                <span>{item.label}</span>
              </button>
            ))}
          </div>
          <button className="help-guide" onClick={onOpenGuide}>
            <FileText size={16} aria-hidden="true" />Полная инструкция
          </button>
        </div>
        <div ref={panel} className="help-panel" id="help-panel" role="tabpanel" aria-labelledby={`help-tab-${topic}`} tabIndex={0}>
          <article className="help-article"><Section go={go} /></article>
        </div>
      </div>
      <footer className="help-footer">
        <span className="help-safe"><ShieldCheck size={16} aria-hidden="true" />Исходные PDF не меняются, прежние версии файлов остаются в резервных копиях.</span>
        <button className="btn primary" onClick={onClose}>Понятно</button>
      </footer>
    </Dialog>
  );
}

type SectionProps = { go(topic: HelpTopic): void };

const SECTIONS: Record<HelpTopic, (props: SectionProps) => ReactNode> = {
  start: ({ go }) => <>
    <Heading title="Начало работы">
      Программа достаёт изображения из PDF-презентации и раскладывает их по подписям и вариантам. Всё работает на этом
      компьютере, без интернета.
    </Heading>
    <figure className="help-figure" aria-hidden="true">
      <span className="hf-doc">
        <span className="hf-doc-label">PDF</span>
        <span className="hf-bar" /><span className="hf-bar is-short" /><span className="hf-bar" />
        <Pin n={1} />
      </span>
      <ChevronRight className="hf-arrow" size={20} />
      <span className="hf-grid">
        {[1, 2, 3, 4, 1, 3].map((tone, i) => <span key={i} className={`hf-thumb tone-${tone}${i === 4 ? " is-flagged" : ""}`} />)}
        <Pin n={2} />
      </span>
      <ChevronRight className="hf-arrow" size={20} />
      <span className="hf-variants">
        <span className="hf-variant"><i className="dot-1" />Вариант 1</span>
        <span className="hf-variant"><i className="dot-2" />Вариант 2</span>
        <span className="hf-variant"><i className="dot-3" />Вариант 3</span>
        <Pin n={3} />
      </span>
    </figure>
    <ol className="help-steps">
      <Step n={1} title="Добавьте PDF">
        Нажмите <Ui>Выбрать PDF</Ui> или <Keys keys={["Ctrl", "O"]} />, либо перетащите в окно файлы и папки из
        Проводника. Обработка начнётся сразу, в очередь можно поставить несколько PDF.
      </Step>
      <Step n={2} title="Откройте результат">
        Когда обработка закончится, нажмите <Ui>Открыть результат</Ui>. Изображения уже разложены по подписям, а
        спорные отмечены.
      </Step>
      <Step n={3} title="Проверьте и сохраните">
        Разберите спорные изображения, перенесите лишние в нужные варианты и нажмите <Ui>Сохранить</Ui>. Каждый
        вариант — отдельный PDF.
      </Step>
    </ol>
    <Note>
      <li>Пока идёт обработка, можно открыть историю и вернуться — очередь продолжится.</li>
      <li>Где сохранять результаты, выбирается в «Настройках»: рядом с PDF, в подпапке «Результаты» или в общей папке.</li>
      <li>Прошлые результаты — в «Истории обработки». Старую папку можно добавить через «Открыть прошлый результат».</li>
    </Note>
    <Next to="review" go={go} />
  </>,

  review: ({ go }) => <>
    <Heading title="Проверка результата">
      Большинство изображений программа распределяет сама. Проверить нужно только отмеченные.
    </Heading>
    <figure className="help-figure" aria-hidden="true">
      <span className="hf-window hf-review">
        <span className="hf-row"><span className="hf-bar is-title" /><span className="hf-btn is-primary">Редактировать</span></span>
        <span className="hf-strip">
          <ScanEye size={14} /><span className="hf-bar" />
          <span className="hf-btn is-target">Разобрать<Pin n={1} /></span>
        </span>
        <span className="hf-cards">
          <span className="hf-card"><span className="hf-img tone-1" /><span className="hf-tag review"><Clock3 size={12} />На проверку</span><Pin n={2} /></span>
          <span className="hf-card"><span className="hf-img tone-3" /><span className="hf-tag unsigned"><Tag size={12} />Без подписи</span></span>
          <span className="hf-card"><span className="hf-img tone-2" /><span className="hf-open"><Maximize2 size={12} /></span><Pin n={3} /></span>
        </span>
      </span>
    </figure>
    <ol className="help-steps">
      <Step n={1} title="Откройте спорные">
        <Ui>Разобрать</Ui> на панели проверки показывает изображения «На проверку» и «Без подписи» — сразу в режиме
        правки.
      </Step>
      <Step n={2} title="Решите по каждому">
        <p>Метка на карточке подсказывает, что не так:</p>
        <dl className="help-terms">
          <div>
            <dt><span className="help-badge review"><Clock3 size={12} />На проверку</span></dt>
            <dd>подпись найдена не наверняка — в просмотре видны возможные варианты</dd>
          </div>
          <div>
            <dt><span className="help-badge unsigned"><Tag size={12} />Без подписи</span></dt>
            <dd>подпись не найдена — перенесите изображение в нужный вариант</dd>
          </div>
          <div>
            <dt><span className="help-badge removed"><Shapes size={12} />Служебный</span></dt>
            <dd>логотип, повторяющийся значок или мелкий фрагмент — программа отложила его в «Служебные»</dd>
          </div>
          <div>
            <dt><span className="help-badge removed"><Trash2 size={12} />Убрано</span></dt>
            <dd>вы убрали изображение — его можно вернуть</dd>
          </div>
        </dl>
        <p>У карточек с найденной подписью метки нет.</p>
      </Step>
      <Step n={3} title="Сверьтесь со слайдом">
        Двойной щелчок открывает карточку крупно, а <Ui>Исходная страница</Ui> показывает слайд целиком — видно, откуда
        взята картинка.
      </Step>
    </ol>
    <Note>
      <li>Предупреждения и подробности распознавания текста появляются на панели проверки, только если они есть.</li>
      <li>Для исходной страницы нужен первоначальный PDF на прежнем месте.</li>
    </Note>
    <Next to="edit" go={go} />
  </>,

  edit: ({ go }) => <>
    <Heading title="Редактирование">
      Нажмите <Ui>Редактировать</Ui> в шапке результата. Файлы на диске не меняются, пока вы не нажмёте «Сохранить».
    </Heading>
    <figure className="help-figure is-stacked" aria-hidden="true">
      <span className="hf-cards">
        <span className="hf-card is-picked"><span className="hf-img tone-1" /><span className="hf-check is-on"><Check size={10} strokeWidth={3} /></span><Pin n={1} /></span>
        <span className="hf-card is-picked"><span className="hf-img tone-2" /><span className="hf-check is-on"><Check size={10} strokeWidth={3} /></span></span>
        <span className="hf-card"><span className="hf-img tone-3" /><span className="hf-check" /></span>
        <span className="hf-card"><span className="hf-img tone-4" /><span className="hf-check" /></span>
      </span>
      <span className="hf-dock">
        <span className="hf-dock-count">Выбрано: 2</span>
        <span className="hf-btn is-target">Перенести в вариант<ChevronDown size={12} /><Pin n={2} /></span>
        <span className="hf-btn"><Trash2 size={12} />Убрать</span>
        <span className="hf-btn"><Ellipsis size={12} />Ещё</span>
        <span className="hf-btn is-primary"><Save size={12} />Сохранить<Pin n={3} /></span>
      </span>
    </figure>
    <ol className="help-steps">
      <Step n={1} title="Выберите карточки">
        Флажок или <Keys keys={["Ctrl"]} /> + щелчок — по одной, <Keys keys={["Shift"]} /> + щелчок — диапазон,{" "}
        <Keys keys={["Ctrl", "A"]} /> — все видимые. Выбор сохраняется при переходе между разделами и при поиске.
      </Step>
      <Step n={2} title="Перенесите или уберите">
        Нажмите <Ui>Перенести в вариант</Ui> или <Ui>Новый вариант</Ui>. <Ui>Убрать</Ui> или{" "}
        <Keys keys={["Delete"]} /> убирает лишнее из результата. Порядок внутри варианта меняют стрелки ↑ ↓ на панели
        или перетаскивание.
      </Step>
      <Step n={3} title="Сохраните">
        <Ui>Сохранить</Ui> или <Keys keys={["Ctrl", "S"]} /> записывает PDF вариантов, прежние версии уходят в
        резервные копии. <Keys keys={["Ctrl", "Z"]} /> отменяет шаг, <Ui>Сбросить</Ui> — все несохранённые правки.
      </Step>
    </ol>
    <section className="help-group">
      <h4>В меню «Ещё»</h4>
      <dl className="help-menu">
        <div><dt><Ui>Создать копии выбранных</Ui></dt><dd>копии на месте или в другом варианте, оригиналы остаются</dd></div>
        <div><dt><Ui>В начало варианта</Ui> <Ui>В конец варианта</Ui></dt><dd>переносит всю выбранную группу</dd></div>
        <div>
          <dt><Ui>Выбрать такие же по размеру</Ui></dt>
          <dd>добавляет к выбору изображения того же размера — например, план участка с каждого слайда, чтобы убрать их разом</dd>
        </div>
        <div>
          <dt><Ui>Разрезать склейку…</Ui></dt>
          <dd>делит картинку из нескольких рендеров на кадры. <Link to="collages" go={go}>Подробнее о склейках</Link></dd>
        </div>
        <div>
          <dt><Ui>Вырезать часть со страницы…</Ui></dt>
          <dd>берёт со страницы PDF только нужную часть. <Link to="cutouts" go={go}>Подробнее о вырезках</Link></dd>
        </div>
        <div><dt><Ui>Отменить последнее сохранение</Ui></dt><dd>доступно, пока нет новых правок</dd></div>
      </dl>
    </section>
    <Note>
      <li>Копия всего варианта — кнопка «Копия PDF» рядом с его названием.</li>
      <li>Убранные изображения лежат в разделе «Убранные» — их можно вернуть и после сохранения.</li>
    </Note>
    <Next to="collages" go={go} />
  </>,

  collages: ({ go }) => <>
    <Heading title="Склейки">
      Склейка — картинка, собранная из нескольких рендеров. Программа сама разрезает её на кадры, а вы проверяете
      результат.
    </Heading>
    <figure className="help-figure" aria-hidden="true">
      <span className="hf-window hf-collages">
        <span className="hf-tabs">
          <span>Все</span>
          <span className="is-active">Нужно проверить<Pin n={1} /></span>
          <span>Готовы</span>
        </span>
        <span className="hf-collage">
          <span className="hf-split"><span className="tone-1" /><span className="tone-2" /><span className="tone-3" /></span>
          <span className="hf-collage-side">
            <span className="hf-chip blue"><Scissors size={12} />Разрезано автоматически<Pin n={2} /></span>
            <span className="hf-row">
              <span className="hf-btn is-target">Проверить разрезку<Pin n={3} /></span>
              <span className="hf-btn is-quiet"><Check size={12} />Всё верно</span>
            </span>
          </span>
        </span>
      </span>
    </figure>
    <ol className="help-steps">
      <Step n={1} title="Откройте раздел «Склейки»">
        Он есть в боковом меню результата. Вкладка <Ui>Нужно проверить</Ui> оставляет только то, что ждёт решения.
      </Step>
      <Step n={2} title="Проверьте каждую склейку">
        <p>Метка показывает, что программа уже сделала:</p>
        <dl className="help-terms">
          <div>
            <dt><span className="collage-chip blue"><Scissors size={14} />Разрезано автоматически</span></dt>
            <dd>посмотрите кадры и нажмите <Ui>Всё верно</Ui> или <Ui>Проверить разрезку</Ui></dd>
          </div>
          <div>
            <dt><span className="collage-chip amber"><TriangleAlert size={14} />Возможно, склейка</span></dt>
            <dd>программа не уверена: <Ui>Разрезать…</Ui> или <Ui>Это одно фото</Ui></dd>
          </div>
          <div>
            <dt><span className="collage-chip green"><Check size={14} />Проверено</span></dt>
            <dd>готово; разрезку можно изменить или <Ui>Вернуть целиком</Ui></dd>
          </div>
          <div>
            <dt><span className="collage-chip neutral"><Layers size={14} />Целиком</span></dt>
            <dd>оставлена одной картинкой</dd>
          </div>
        </dl>
      </Step>
      <Step n={3} title="Поправьте линии">
        В редакторе <Ui>Поровну</Ui> делит картинку на колонки и ряды, <Ui>Найти швы</Ui> ищет светлые полосы, а линии
        можно двигать мышью. Выберите вариант для кадров и нажмите <Ui>Разрезать на N кадров</Ui>.
      </Step>
    </ol>
    <Note>
      <li>Чтобы разложить кадры по разным вариантам, отметьте «каждый кадр — в свой подвариант».</li>
      <li>В режиме правки у подозрительной карточки в галерее тоже есть кнопка «Разрезать…».</li>
      <li>Исходная картинка склейки хранится в папке результата «_Служебное/Склейки».</li>
    </Note>
    <Next to="cutouts" go={go} />
  </>,

  cutouts: ({ go }) => <>
    <Heading title="Вырезки">
      Когда поверх рендера лежит схема, подпись или план, со страницы PDF можно взять только нужную часть.
    </Heading>
    <figure className="help-figure" aria-hidden="true">
      <span className="hf-card is-large">
        <span className="hf-img tone-2" />
        <span className="hf-btn hf-over">Вырезать часть…<Pin n={1} /></span>
      </span>
      <ChevronRight className="hf-arrow" size={20} />
      <span className="hf-cutout">
        <span className="hf-page">
          <span className="hf-page-picture" />
          <span className="hf-page-scheme"><i /><i /><i /></span>
          <span className="hf-frame"><i /><i /><i /><i /><Pin n={2} /></span>
        </span>
        <span className="hf-btn is-primary"><Crop size={12} />Добавить 1 вырезку<Pin n={3} /></span>
      </span>
    </figure>
    <ol className="help-steps">
      <Step n={1} title="Откройте страницы PDF">
        В режиме правки — <Ui>Вырезать часть…</Ui> на карточке или <Path items={["Ещё", "Вырезать часть со страницы…"]} />.
      </Step>
      <Step n={2} title="Обведите нужное">
        Нарисуйте рамку мышью. <Ui>Найденные картинки</Ui> показывают границы картинок на странице, а{" "}
        <Ui>Прилипать к краям</Ui> помогает попасть точно в край. Рамок может быть несколько.
      </Step>
      <Step n={3} title="Добавьте вырезки">
        <Ui>Добавить N вырезки</Ui> ставит вырезку в вариант на место картинки.
      </Step>
    </ol>
    <Note>
      <li>Исходная картинка не пропадает: она в разделе «Вырезки», оттуда её можно вернуть.</li>
      <li>Рамка за пределами картинок даёт снимок части страницы.</li>
    </Note>
    <Next to="viewer" go={go} />
  </>,

  viewer: ({ go }) => <>
    <Heading title="Крупный просмотр">
      Двойной щелчок по карточке открывает её крупно: удобно разглядеть детали и сразу решить, куда отнести изображение.
    </Heading>
    <figure className="help-figure" aria-hidden="true">
      <span className="hf-stage">
        <span className="hf-switch">
          <span><Images size={12} />Извлечённое изображение</span>
          <span className="is-target"><Scan size={12} />Исходная страница<Pin n={3} /></span>
        </span>
        <span className="hf-round is-left"><ChevronLeft size={16} /></span>
        <span className="hf-stage-image tone-2" />
        <span className="hf-round is-right"><ChevronRight size={16} /><Pin n={1} /></span>
        <span className="hf-zoom"><Minus size={12} /><b>100%</b><Plus size={12} /><i /><Scan size={12} /><Pin n={2} /></span>
      </span>
    </figure>
    <ol className="help-steps">
      <Step n={1} title="Листайте">
        <Keys keys={["←"]} /> <Keys keys={["→"]} /> или стрелки по бокам — к соседнему изображению.
      </Step>
      <Step n={2} title="Меняйте масштаб">
        Колесо мыши, <Ui>−</Ui> <Ui>+</Ui> или двойной щелчок — от 25 до 500 % от размера по окну. Кнопка справа от
        процента снова вписывает изображение целиком.
      </Step>
      <Step n={3} title="Смотрите слайд целиком">
        <Ui>Исходная страница</Ui> показывает страницу презентации, <Ui>Извлечённое изображение</Ui> возвращает к
        карточке.
      </Step>
    </ol>
    <Note title="В режиме правки">
      <li><Keys keys={["1"]} />–<Keys keys={["9"]} /> переносят открытое изображение в вариант с этим номером.</li>
      <li>Перенос, «Убрать» и «Вернуть» доступны прямо в просмотре.</li>
    </Note>
    <Next to="keys" go={go} />
  </>,

  keys: () => <>
    <Heading title="Клавиши">
      У многих кнопок сочетание указано и в подсказке — наведите на кнопку указатель.
    </Heading>
    <div className="help-shortcuts">
      <section className="help-group">
        <h4>Везде</h4>
        <dl className="help-keymap">
          <div><dt>Выбрать PDF</dt><dd><Keys keys={["Ctrl", "O"]} /></dd></div>
          <div><dt>Поиск по результату</dt><dd><Keys keys={["Ctrl", "K"]} /></dd></div>
          <div><dt>Закрыть окно</dt><dd><Keys keys={["Esc"]} /></dd></div>
        </dl>
      </section>
      <section className="help-group">
        <h4>Режим правки</h4>
        <dl className="help-keymap">
          <div><dt>Добавить карточку к выбору</dt><dd><Keys keys={["Ctrl"]} /> + щелчок</dd></div>
          <div><dt>Выбрать диапазон</dt><dd><Keys keys={["Shift"]} /> + щелчок</dd></div>
          <div><dt>Выбрать все видимые</dt><dd><Keys keys={["Ctrl", "A"]} /></dd></div>
          <div><dt>Снять всё выделение</dt><dd><Keys keys={["Esc"]} /></dd></div>
          <div><dt>Убрать выбранные</dt><dd><Keys keys={["Delete"]} /></dd></div>
          <div><dt>Отменить шаг</dt><dd><Keys keys={["Ctrl", "Z"]} /></dd></div>
          <div><dt>Сохранить</dt><dd><Keys keys={["Ctrl", "S"]} /></dd></div>
        </dl>
      </section>
      <section className="help-group">
        <h4>Крупный просмотр</h4>
        <dl className="help-keymap">
          <div><dt>Соседнее изображение</dt><dd><Keys keys={["←"]} /> <Keys keys={["→"]} /></dd></div>
          <div><dt>Масштаб</dt><dd>колесо мыши</dd></div>
          <div><dt>В вариант с номером (в режиме правки)</dt><dd><Keys keys={["1"]} />–<Keys keys={["9"]} /></dd></div>
        </dl>
      </section>
    </div>
  </>,
};

function Heading({ title, children }: { title: string; children: ReactNode }) {
  return <header className="help-heading"><h3>{title}</h3><p>{children}</p></header>;
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="help-step">
      <span className="help-number" aria-hidden="true">{n}</span>
      <div className="help-step-copy"><h4>{title}</h4><div className="help-step-text">{children}</div></div>
    </li>
  );
}

function Note({ title = "Полезно знать", children }: { title?: string; children: ReactNode }) {
  return <aside className="help-note"><h4><Lightbulb size={16} aria-hidden="true" />{title}</h4><ul>{children}</ul></aside>;
}

function Next({ to, go }: { to: HelpTopic } & SectionProps) {
  const next = TOPICS.find((item) => item.id === to)!;
  return (
    <button className="help-next" onClick={() => go(to)}>
      <span>Дальше: {next.label}</span><ChevronRight size={16} aria-hidden="true" />
    </button>
  );
}

function Link({ to, go, children }: { to: HelpTopic; children: ReactNode } & SectionProps) {
  return <button className="help-link" onClick={() => go(to)}>{children}</button>;
}

/** The name of a button or menu item as it reads in the window. */
function Ui({ children }: { children: ReactNode }) {
  return <span className="help-ui">{children}</span>;
}

function Path({ items }: { items: string[] }) {
  return (
    <span className="help-path">
      {items.map((item, i) => <Fragment key={item}>
        {i > 0 && <><ChevronRight size={12} aria-hidden="true" /><span className="sr-only">, затем</span></>}
        <span className="help-ui">{item}</span>
      </Fragment>)}
    </span>
  );
}

function Keys({ keys }: { keys: string[] }) {
  return <span className="help-keys">{keys.map((name) => <kbd key={name}>{name}</kbd>)}</span>;
}

/** The step number on a sketch, pinned to the control the step is about. */
function Pin({ n }: { n: number }) {
  return <span className="hf-pin">{n}</span>;
}
