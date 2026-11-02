import { useEffect, useId, useLayoutEffect, useState } from "react";
import type { ReactNode, RefObject } from "react";

/** A rounded panel of liquid glass: its corner radius, how far in from the rim the glass bends and how strongly. */
export interface GlassShape {
  radius: number;
  bezel: number;
  /** The largest shift of the backdrop, in pixels (feDisplacementMap scale). */
  scale: number;
}

/**
 * The displacement map of a rounded rectangle as RGBA: red and green shift the backdrop along x and y, 128 is no shift.
 * Inside the bezel the backdrop is pulled in from the rim, most at the very edge, like the rim of a thick convex lens;
 * the middle stays flat.
 */
export function refractionMap(width: number, height: number, radius: number, bezel: number) {
  const data = new Uint8ClampedArray(width * height * 4);
  const halfWidth = width / 2, halfHeight = height / 2;
  const r = Math.min(radius, halfWidth, halfHeight);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const px = x + 0.5 - halfWidth, py = y + 0.5 - halfHeight;
      const ax = Math.abs(px) - (halfWidth - r), ay = Math.abs(py) - (halfHeight - r);
      // Distance in from the outline and the outward normal there, in the quarter of this pixel.
      let inside: number, nx: number, ny: number;
      if (ax > 0 && ay > 0) {
        const d = Math.hypot(ax, ay);
        inside = r - d; nx = ax / d; ny = ay / d;
      } else if (ax > ay) {
        inside = r - ax; nx = 1; ny = 0;
      } else {
        inside = r - ay; nx = 0; ny = 1;
      }
      let strength = 0;
      if (inside >= 0 && inside < bezel) {
        const t = 1 - inside / bezel;
        strength = 1 - Math.sqrt(1 - t * t);
      }
      const i = (y * width + x) * 4;
      data[i] = 128 - Math.sign(px) * nx * strength * 127;
      data[i + 1] = 128 - Math.sign(py) * ny * strength * 127;
      data[i + 2] = 128;
      data[i + 3] = 255;
    }
  }
  return data;
}

const supported = () => typeof CSS !== "undefined" && CSS.supports("backdrop-filter", "url(#glass)");

/**
 * Liquid glass on an element while `active`: an SVG filter bends the backdrop at the rim. backdrop-filter reads SVG
 * filters in Chromium, and WebView2 is Chromium. The map is drawn again when the element changes size. While it is
 * not ready, without support or with transparency off in Windows (CSS), the element keeps its plain look; with it the
 * element gets `data-glass="liquid"` and `--liquid-glass: url(#…)`. Render the returned filter anywhere on the page.
 */
export function useLiquidGlass(target: RefObject<HTMLElement | null>, active: boolean, shape: GlassShape): ReactNode {
  const id = `liquid-glass-${useId().replace(/[^\w-]/g, "")}`;
  const [map, setMap] = useState<{ href: string; width: number; height: number } | null>(null);
  const { radius, bezel } = shape;
  useEffect(() => {
    const element = target.current;
    if (!active || !element || !supported()) return;
    let frame = 0, drawn = "";
    const draw = () => {
      frame = 0;
      const width = element.offsetWidth, height = element.offsetHeight;
      if (!width || !height || `${width}×${height}` === drawn) return;
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.putImageData(new ImageData(refractionMap(width, height, radius, bezel), width, height), 0, 0);
      drawn = `${width}×${height}`;
      setMap({ href: canvas.toDataURL(), width, height });
    };
    // A resize of the window or the menu moves the dock every frame: one map per frame at most.
    const observer = new ResizeObserver(() => { frame ||= requestAnimationFrame(draw); });
    observer.observe(element);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      setMap(null);
    };
  }, [target, active, radius, bezel]);
  // The element points at the filter only once the filter is on the page.
  useLayoutEffect(() => {
    const element = target.current;
    if (!element || !map) return;
    element.dataset.glass = "liquid";
    element.style.setProperty("--liquid-glass", `url("#${id}")`);
    return () => {
      delete element.dataset.glass;
      element.style.removeProperty("--liquid-glass");
    };
  }, [target, map, id]);
  if (!map) return null;
  return (
    <svg className="liquid-glass-filter" width="0" height="0" aria-hidden="true" focusable="false">
      <filter id={id} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
        {/* A light frost: captions passing under the glass must not compete with the panel's own text. */}
        <feGaussianBlur in="SourceGraphic" stdDeviation="2.5" result="soft" />
        <feImage href={map.href} x="0" y="0" width={map.width} height={map.height} preserveAspectRatio="none"
          result="map" />
        <feDisplacementMap in="soft" in2="map" scale={shape.scale} xChannelSelector="R" yChannelSelector="G"
          result="bent" />
        <feColorMatrix in="bent" type="saturate" values="1.5" />
      </filter>
    </svg>
  );
}
