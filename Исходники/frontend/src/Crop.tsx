import { useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Crop as CropIcon } from "lucide-react";
import { Dialog } from "./components";
import type { Box } from "./editor";
import "./cutouts.css";
import "./crop.css";

type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
/** The server takes no frame smaller than this many pixels a side. */
export const CROP_MIN = 16;

/** Move a frame or one of its handles by (dx, dy) pixels of the picture, inside the picture. */
export function movedBox(box: Box, handle: Handle | null, dx: number, dy: number, size: [number, number]): Box {
  let [left, top, right, bottom] = box;
  const [width, height] = size;
  if (handle === null) {
    const x = Math.round(Math.min(Math.max(dx, -left), width - right));
    const y = Math.round(Math.min(Math.max(dy, -top), height - bottom));
    return [left + x, top + y, right + x, bottom + y];
  }
  const least = (side: number) => Math.min(CROP_MIN, side);
  if (handle.includes("w")) left = Math.round(Math.min(Math.max(left + dx, 0), right - least(width)));
  if (handle.includes("e")) right = Math.round(Math.max(Math.min(right + dx, width), left + least(width)));
  if (handle.includes("n")) top = Math.round(Math.min(Math.max(top + dy, 0), bottom - least(height)));
  if (handle.includes("s")) bottom = Math.round(Math.max(Math.min(bottom + dy, height), top + least(height)));
  return [left, top, right, bottom];
}

/** One frame over one picture: what is inside stays in the variant in place of the picture. */
export function CropDialog({ title, src, size, box, busy, onApply, onClose }: {
  title: string;
  /** The whole picture, also when it is cropped already. */
  src: string;
  size: [number, number];
  /** The crop the card has now; null for a whole picture. */
  box: Box | null;
  busy: boolean;
  onApply(box: Box): void;
  onClose(): void;
}) {
  const whole: Box = [0, 0, size[0], size[1]];
  const [frame, setFrame] = useState<Box>(box ?? whole);
  const stage = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointer: number; handle: Handle | null; x: number; y: number; from: Box } | null>(null);
  const same = (first: Box, second: Box) => first.every((value, index) => value === second[index]);
  const unchanged = same(frame, box ?? whole);

  function start(event: ReactPointerEvent, handle: Handle | null) {
    if (event.button !== 0 || busy) return;
    event.preventDefault();
    event.stopPropagation();
    stage.current?.setPointerCapture(event.pointerId);
    drag.current = { pointer: event.pointerId, handle, x: event.clientX, y: event.clientY, from: frame };
  }
  function move(event: ReactPointerEvent) {
    const current = drag.current;
    const bounds = stage.current?.getBoundingClientRect();
    if (!current || current.pointer !== event.pointerId || !bounds?.width) return;
    setFrame(movedBox(current.from, current.handle, (event.clientX - current.x) / bounds.width * size[0],
      (event.clientY - current.y) / bounds.height * size[1], size));
  }
  function end(event: ReactPointerEvent) {
    if (drag.current?.pointer === event.pointerId) drag.current = null;
  }
  const share = (value: number, side: number) => `${value / side * 100}%`;
  return (
    <Dialog title="Вырезать часть изображения" subtitle={title} icon={<CropIcon size={18} />} wide busy={busy}
      className="crop-dialog" onClose={onClose}>
      <p className="crop-note">Потяните рамку за края: в варианте останется то, что внутри неё. Обрезанное изображение
        встанет на место этого и сразу запишется в PDF варианта.</p>
      <div className="crop-area">
        <div className="crop-stage" ref={stage} style={{ aspectRatio: `${size[0]} / ${size[1]}`,
          width: `min(100%, calc(62vh * ${size[0] / size[1]}))` }}
          onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
          <img src={src} alt="" draggable={false} />
          <div className="cutout-frame is-selected crop-frame" style={{ left: share(frame[0], size[0]), top: share(frame[1], size[1]),
            width: share(frame[2] - frame[0], size[0]), height: share(frame[3] - frame[1], size[1]) }}
            onPointerDown={(event) => start(event, null)}>
            {HANDLES.map((handle) => (
              <span key={handle} className={`cutout-handle ${handle}`} aria-hidden="true"
                onPointerDown={(event) => start(event, handle)} />
            ))}
          </div>
        </div>
      </div>
      <div className="crop-footer">
        <span className="crop-size">Останется {frame[2] - frame[0]} × {frame[3] - frame[1]} px из {size[0]} × {size[1]}</span>
        <button type="button" className="btn compact secondary" disabled={busy || same(frame, whole)}
          onClick={() => setFrame(whole)}>Вся картинка</button>
        <span className="crop-spacer" />
        <button type="button" className="btn secondary" disabled={busy} onClick={onClose}>Отмена</button>
        <button type="button" className="btn primary" disabled={busy || unchanged} onClick={() => onApply(frame)}>
          <CropIcon size={16} /><span>Вырезать и сохранить</span>
        </button>
      </div>
    </Dialog>
  );
}
