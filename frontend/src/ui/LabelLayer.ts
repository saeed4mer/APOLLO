import type { WorldRecord } from "../models/world";
import type { ViewSnapshot, WorldRenderer } from "../renderer/WorldRenderer";
import { labelDetailOpacity, labelOpacity, MAX_LABELS, referenceOpacity } from "../scene/atmosphere";
import { el } from "./dom";
import { formatKmCompact } from "./format";

interface Box { left: number; top: number; right: number; bottom: number }

/** Approximate label footprint (11px name line, optional 10px monospace detail line). */
export function labelBox(x: number, y: number, name: string, detail: string | null): Box {
  const width = Math.max(name.length * 6.3, detail ? detail.length * 6.1 : 0) + 6;
  return { left: x, top: y, right: x + width, bottom: y + (detail ? 28 : 15) };
}

export function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

/**
 * Progressive information density. Names appear around 60% depth and miss distances around 80%,
 * for at most MAX_LABELS settled asteroids, nearest first (deterministic: by real miss distance,
 * then neows_id); a label that would overlap a nearer asteroid's label is skipped. Reference-
 * distance arcs get their labels from the renderer. A fixed pool of DOM nodes is reused, so large
 * populations never create unbounded DOM.
 */
export class LabelLayer {
  readonly element = el("div", { className: "label-layer", attrs: { "aria-hidden": "true" } });
  private readonly pool: { root: HTMLElement; name: HTMLElement; detail: HTMLElement }[] = [];
  private readonly references: HTMLElement[] = [];
  private nearest: WorldRecord[] = [];

  constructor() {
    for (let i = 0; i < MAX_LABELS; i++) {
      const name = el("span", { className: "label-name" });
      const detail = el("span", { className: "label-detail" });
      const root = el("div", { className: "asteroid-label" }, [name, detail]);
      root.hidden = true;
      this.pool.push({ root, name, detail });
      this.element.append(root);
    }
  }

  setRecords(records: readonly WorldRecord[]): void {
    this.nearest = [...records]
      .sort((a, b) => a.encounter.miss_distance_km - b.encounter.miss_distance_km || (a.neows_id < b.neows_id ? -1 : 1));
  }

  update(view: ViewSnapshot, renderer: WorldRenderer, hoveredId: string | null): void {
    const fade = 1 - view.focus;
    const nameOpacity = labelOpacity(view.progress) * fade;
    const detailOpacity = labelDetailOpacity(view.progress);
    let used = 0;
    const placed: Box[] = [];
    if (nameOpacity > 0.01) {
      for (const record of this.nearest) {
        if (used >= MAX_LABELS) break;
        if (record.neows_id === hoveredId || renderer.phaseOf(record.neows_id) !== "SETTLED") continue;
        const at = renderer.screenPositionOf(record.neows_id);
        if (!at) continue;
        const detail = formatKmCompact(record.encounter.miss_distance_km);
        const box = labelBox(at.x + 12, at.y - 8, record.name, detailOpacity > 0.01 ? detail : null);
        if (placed.some((other) => overlaps(box, other))) continue; // nearer asteroids keep their label
        placed.push(box);
        const slot = this.pool[used++]!;
        slot.name.textContent = record.name;
        slot.detail.textContent = detail;
        slot.detail.style.opacity = String(detailOpacity);
        slot.root.style.transform = `translate(${Math.round(at.x + 12)}px, ${Math.round(at.y - 8)}px)`;
        slot.root.style.opacity = String(nameOpacity);
        slot.root.hidden = false;
      }
    }
    for (let i = used; i < this.pool.length; i++) this.pool[i]!.root.hidden = true;

    const refOpacity = referenceOpacity(view.progress) * fade;
    const anchors = refOpacity > 0.01 ? renderer.referenceAnchors() : [];
    anchors.forEach((anchor, i) => {
      let node = this.references[i];
      if (!node) {
        node = el("div", { className: "reference-label" });
        this.references.push(node);
        this.element.append(node);
      }
      node.textContent = anchor.label;
      node.style.transform = `translate(calc(${Math.round(anchor.x)}px - 100%), ${Math.round(anchor.y - 16)}px)`;
      node.style.opacity = String(Math.min(1, refOpacity * 1.6));
      node.hidden = false;
    });
    for (let i = anchors.length; i < this.references.length; i++) this.references[i]!.hidden = true;
  }

  dispose(): void {
    this.element.remove();
  }
}
