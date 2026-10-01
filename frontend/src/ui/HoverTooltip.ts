import { isSentryLinked, type WorldRecord } from "../models/world";
import { el } from "./dom";
import { formatFlag, formatNumber, MATCH_STATE_TEXT, SENTRY_STATUS_TEXT, UNAVAILABLE } from "./format";

/**
 * Lightweight hover facts from the already-loaded world record (no request per hover).
 * NeoWs facts and Sentry linkage are labelled separately; Sentry text is the contract's status.
 */
export function hoverFacts(record: WorldRecord): [label: string, value: string][] {
  const enc = record.encounter;
  return [
    ["NeoWs ID", record.neows_id],
    ["Approach (NeoWs)", enc.close_approach_datetime ?? enc.closest_approach_date],
    ["Miss distance (NeoWs)", formatNumber(enc.miss_distance_km, "km", 0)],
    ["Relative velocity (NeoWs)", formatNumber(enc.relative_velocity_km_s, "km/s", 3)],
    [
      "Est. diameter (NeoWs)",
      enc.estimated_diameter_min_km === null || enc.estimated_diameter_max_km === null
        ? UNAVAILABLE
        : `${formatNumber(enc.estimated_diameter_min_km, undefined, 4)} – ${formatNumber(enc.estimated_diameter_max_km, "km", 4)}`,
    ],
    ["Potentially hazardous (NeoWs)", formatFlag(enc.is_potentially_hazardous)],
    ["Identity", MATCH_STATE_TEXT[record.resolution.match_state]],
    ["JPL Sentry linkage", SENTRY_STATUS_TEXT[record.sentry.status]],
  ];
}

const TOOLTIP_OFFSET = 16;
const TOOLTIP_MARGIN = 8;

/** Place the tooltip beside the pointer, flipping left/up so it never crosses the given bounds. */
export function tooltipPosition(
  clientX: number, clientY: number, width: number, height: number, bounds: { right: number; bottom: number },
): { left: number; top: number } {
  let left = clientX + TOOLTIP_OFFSET;
  if (left + width > bounds.right) left = clientX - TOOLTIP_OFFSET - width;
  let top = clientY + TOOLTIP_OFFSET;
  if (top + height > bounds.bottom) top = clientY - TOOLTIP_OFFSET - height;
  return { left: Math.max(TOOLTIP_MARGIN, left), top: Math.max(TOOLTIP_MARGIN, top) };
}

export class HoverTooltip {
  readonly element = el("div", { className: "hover-tooltip", attrs: { role: "tooltip" } });
  private anchor: { x: number; y: number } | null = null;

  constructor() {
    this.element.hidden = true;
  }

  show(record: WorldRecord, clientX: number, clientY: number): void {
    const rows = hoverFacts(record).map(([label, value]) =>
      el("div", { className: "fact" }, [el("span", { className: "fact-label", text: label }), el("span", { className: "fact-value", text: value })]),
    );
    // The gold designation's meaning, stated where the user is already reading about this object.
    const tag = isSentryLinked(record) ? [el("p", { className: "sentry-tag", text: "SENTRY LINKED" })] : [];
    this.element.replaceChildren(el("p", { className: "tooltip-title", text: record.name }), ...tag, ...rows);
    this.element.hidden = false;
    this.anchor = { x: clientX, y: clientY };
    this.place();
  }

  /** Re-place a visible tooltip after the layout around it changed (e.g. the profile panel opened). */
  place(): void {
    if (this.element.hidden || !this.anchor) return;
    const { left, top } = tooltipPosition(this.anchor.x, this.anchor.y, this.element.offsetWidth, this.element.offsetHeight, this.bounds());
    this.element.style.left = `${left}px`;
    this.element.style.top = `${top}px`;
  }

  /** Area the tooltip may occupy: the viewport minus an open profile panel, which must stay readable. */
  private bounds(): { right: number; bottom: number } {
    const panel = this.element.parentElement?.querySelector<HTMLElement>(".profile-panel");
    const panelLeft = panel && !panel.hidden ? panel.getBoundingClientRect().left : Infinity;
    return { right: Math.min(window.innerWidth, panelLeft) - TOOLTIP_MARGIN, bottom: window.innerHeight - TOOLTIP_MARGIN };
  }

  hide(): void {
    this.element.hidden = true;
    this.anchor = null;
  }

  dispose(): void {
    this.element.remove();
  }
}
