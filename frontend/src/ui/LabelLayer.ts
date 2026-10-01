import type { WorldRecord } from "../models/world";
import type { ViewSnapshot, WorldRenderer } from "../renderer/WorldRenderer";
import type { GuideState } from "../renderer/WorldRenderer";
import { frontierOpacity, labelDetailOpacity, labelOpacity, MAX_LABELS } from "../scene/atmosphere";
import { frontierLabelKm, SCALE_STEP_KM } from "../scene/skyLayout";
import { el } from "./dom";
import { formatKmCompact } from "./format";

/** Most distance-guide labels shown at once, and the minimum vertical gap between them (px). */
export const MAX_GUIDE_LABELS = 16;
const GUIDE_LABEL_GAP_PX = 15;
/** A guide needs at least this opacity to carry a label (very faint look-ahead guides stay unlabelled). */
export const GUIDE_LABEL_MIN_ALPHA = 0.04;
/** Space reserved left of the guide labels' column, and for the frontier label (px). */
const SCALE_COLUMN_PX = 64;
const FRONTIER_LABEL_PX = 230;
/** The frontier label sits this far above its arc, so it never covers an asteroid resting just inside it. */
const FRONTIER_LIFT_PX = 12;

/**
 * Distance-guide labels. Only guides inside the viewport window are drawn, and in the distance
 * world they are ~120 px apart, so every drawn guide can carry its own label ("17M km", "18M km",
 * ...). Candidates are ordered nearest the frontier first; greedy placement keeps
 * GUIDE_LABEL_GAP_PX between labels and away from the frontier label.
 */
export function guideLabelCandidates(guides: readonly GuideState[], frontierKm: number): GuideState[] {
  return guides
    .filter((g) => g.alpha >= GUIDE_LABEL_MIN_ALPHA)
    .sort((a, b) => Math.abs(a.km - frontierKm) - Math.abs(b.km - frontierKm) || a.km - b.km);
}

export function chooseGuideLabels<T extends { km: number; y: number }>(ordered: readonly T[], frontierY: number | null): T[] {
  const taken: number[] = frontierY === null ? [] : [frontierY];
  const chosen: T[] = [];
  for (const label of ordered) {
    if (chosen.length >= MAX_GUIDE_LABELS) break;
    if (taken.some((y) => Math.abs(y - label.y) < GUIDE_LABEL_GAP_PX)) continue;
    taken.push(label.y);
    chosen.push(label);
  }
  return chosen.sort((a, b) => a.km - b.km);
}

export function formatScaleKm(km: number): string {
  return km >= SCALE_STEP_KM ? `${km / SCALE_STEP_KM}M km` : formatKmCompact(km);
}

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
 * Progressive information density. Names appear around 50% depth and miss distances around 66%,
 * for at most MAX_LABELS settled asteroids, nearest first (deterministic: by real miss distance,
 * then neows_id); a label that would overlap a nearer asteroid's label is skipped. Also labels the
 * Moon landmark, the revealed-distance frontier and the million-km distance guides. Fixed pools of DOM nodes are
 * reused, so large populations never create unbounded DOM.
 */
export class LabelLayer {
  readonly element = el("div", { className: "label-layer", attrs: { "aria-hidden": "true" } });
  private readonly pool: { root: HTMLElement; name: HTMLElement; detail: HTMLElement }[] = [];
  private readonly guideLabels: HTMLElement[] = [];
  private readonly moonLabel = el("div", { className: "moon-label" }, [
    el("span", { className: "moon-title", text: "MOON DISTANCE" }),
    el("span", { className: "moon-km", text: "384,400 km" }),
  ]);
  private readonly frontierLabel = el("div", { className: "frontier-label" });
  private nearest: WorldRecord[] = [];
  /** Formatted miss distance per asteroid, computed once per population (not per frame). */
  private readonly details = new Map<string, string>();

  constructor() {
    this.element.append(this.moonLabel, this.frontierLabel);
    this.moonLabel.hidden = true;
    for (let i = 0; i < MAX_GUIDE_LABELS; i++) {
      const node = el("div", { className: "guide-label" });
      node.hidden = true;
      this.guideLabels.push(node);
      this.element.append(node);
    }
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
    this.details.clear();
    for (const r of records) this.details.set(r.neows_id, formatKmCompact(r.encounter.miss_distance_km));
  }

  update(view: ViewSnapshot, renderer: WorldRenderer, hoveredId: string | null): void {
    const fade = 1 - view.focus;
    const nameOpacity = labelOpacity(view.progress) * fade;
    const detailOpacity = labelDetailOpacity(view.progress);
    let used = 0;
    // The distance scale owns the right-hand column and the frontier label's row: asteroid labels
    // that would cover them are skipped like any other collision.
    const frontier = renderer.frontierScreenPosition();
    const placed: Box[] = frontier === null ? [] : [
      { left: frontier.x - 14 - SCALE_COLUMN_PX, right: Infinity, top: -Infinity, bottom: Infinity },
      { left: frontier.x - 14 - FRONTIER_LABEL_PX, right: Infinity, top: frontier.y - FRONTIER_LIFT_PX - 9, bottom: frontier.y },
    ];
    if (nameOpacity > 0.01) {
      for (const record of this.nearest) {
        if (used >= MAX_LABELS) break;
        if (record.neows_id === hoveredId || renderer.phaseOf(record.neows_id) !== "SETTLED") continue;
        const at = renderer.screenPositionOf(record.neows_id);
        if (!at) continue;
        const detail = this.details.get(record.neows_id)!;
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

    // Moon landmark label: shown with the Moon (revealed when the frontier reaches 384,400 km).
    const moonAlpha = renderer.moonOpacity * fade;
    const moon = renderer.moonScreenPosition();
    // Hidden before the Moon is reached, and again once the journey has passed it (off-screen).
    this.moonLabel.hidden = moonAlpha < 0.01 || moon.y > view.layout.height + 30 || moon.y < -30;
    if (!this.moonLabel.hidden) {
      // Left of the Moon: the right-hand column belongs to the frontier and guide labels.
      this.moonLabel.style.transform = `translate(calc(${Math.round(moon.x - 18)}px - 100%), ${Math.round(moon.y - 14)}px)`;
      this.moonLabel.style.opacity = String(0.9 * moonAlpha);
    }

    // Revealed-distance frontier: counts in whole millions as the user travels outward.
    const frontierAlpha = frontierOpacity(view.progress) * fade;
    this.frontierLabel.hidden = frontier === null || frontierAlpha < 0.01;
    if (frontier && !this.frontierLabel.hidden) {
      this.frontierLabel.textContent = `REVEALED TO ${formatKmCompact(frontierLabelKm(view.revealedKm))}`;
      this.frontierLabel.style.transform = `translate(calc(${Math.round(frontier.x - 14)}px - 100%), ${Math.round(frontier.y - FRONTIER_LIFT_PX - 9)}px)`;
      this.frontierLabel.style.opacity = String(frontierAlpha);
    }

    // Progressive million-km guide labels, at the right end of their guides.
    const candidates = guideLabelCandidates(renderer.visibleGuides, view.revealedKm);
    const positions = renderer.distanceScreenPositions(candidates.map((g) => g.km));
    const placedGuides = chooseGuideLabels(
      candidates.map((g, i) => ({ ...g, x: positions[i]!.x, y: positions[i]!.y - 7 })),
      frontier === null || this.frontierLabel.hidden ? null : frontier.y - FRONTIER_LIFT_PX,
    );
    placedGuides.forEach((g, i) => {
      const node = this.guideLabels[i]!;
      node.textContent = formatScaleKm(g.km);
      node.dataset.tier = g.tier;
      node.style.transform = `translate(calc(${Math.round(g.x - 14)}px - 100%), ${Math.round(g.y - 7)}px)`;
      node.style.opacity = String(Math.min(1, 0.45 + 1.4 * g.alpha) * fade);
      node.hidden = false;
    });
    for (let i = placedGuides.length; i < this.guideLabels.length; i++) this.guideLabels[i]!.hidden = true;
  }

  dispose(): void {
    this.element.remove();
  }
}
