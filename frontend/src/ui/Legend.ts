import type { WorldSnapshotInfo } from "../models/world";
import { MARKER_COLORS } from "../renderer/WorldRenderer";
import { el } from "./dom";
import { formatKmCompact } from "./format";

const hex = (value: number): string => `#${value.toString(16).padStart(6, "0")}`;

/**
 * Explains every visual encoding and the spatial model. Colour is never the only carrier of
 * meaning: each swatch has a text label, and the tooltip/profile state values in words.
 */
export class Legend {
  readonly element = el("aside", { className: "legend", attrs: { "aria-label": "Legend" } });
  private readonly scale = el("p", { className: "scale-indicator", text: "" });
  private readonly spatialNote = el("p", { className: "note", text: "" });

  constructor() {
    const swatch = (color: number, label: string): HTMLElement =>
      el("li", {}, [el("span", { className: "swatch", attrs: { style: `background:${hex(color)}` } }), el("span", { text: label })]);
    this.element.replaceChildren(
      el("p", { className: "legend-title", text: "Marker colour: NeoWs potentially-hazardous flag" }),
      el("ul", { className: "swatches" }, [
        swatch(MARKER_COLORS.pha_yes, "Yes"),
        swatch(MARKER_COLORS.pha_no, "No"),
        swatch(MARKER_COLORS.pha_unknown, "Unknown (not reported)"),
      ]),
      el("p", { className: "note", text: "Marker size is a fixed visual marker, not physical size." }),
      el("p", {
        className: "note",
        text: "Spatial placement is an illustrative visualization. Distance from Earth follows the real NeoWs miss distance on a logarithmic visualization scale; direction is deterministic but not physically observed.",
      }),
      this.spatialNote,
      this.scale,
    );
  }

  setSpatialModel(snapshot: WorldSnapshotInfo): void {
    this.spatialNote.textContent = `Direction model: ${snapshot.spatial_model.direction_algorithm} (${snapshot.spatial_model.direction_semantics}).`;
  }

  setVisibleRadius(km: number): void {
    this.scale.textContent = `Visualization radius ≈ ${formatKmCompact(km)} (visualization scale, not camera distance)`;
  }

  dispose(): void {
    this.element.remove();
  }
}
