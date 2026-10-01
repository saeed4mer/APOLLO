import type { WorldRecord } from "../models/world";
import type { ProfileState } from "../state/store";
import { buildCallouts, provenanceLines, type Callout } from "./callouts";
import { el } from "./dom";
import { formatNumber, LOADING } from "./format";

const SVG_NS = "http://www.w3.org/2000/svg";
/** Horizontal clearance between the asteroid's edge and its callouts (px), and stacking gaps. */
const GAP_X = 70;
const STACK_GAP = 12;
const TOP_CLEARANCE = 84;
const BOTTOM_CLEARANCE = 96;

/**
 * Asteroid intelligence focus: callouts with leader lines arranged around the asteroid, which stays
 * the visual subject at the centre. Opacity follows the camera's focus animation.
 */
export class FocusView {
  readonly element = el("section", { className: "focus-view", attrs: { "aria-label": "Asteroid intelligence" } });
  private readonly lines = document.createElementNS(SVG_NS, "svg");
  private readonly body = el("div", { className: "focus-body" });
  private readonly backButton = el("button", { className: "button focus-back", text: "← Back to world", attrs: { type: "button" } });
  private readonly retryButton = el("button", { className: "button", text: "Retry", attrs: { type: "button" } });
  private readonly handleBack = (): void => this.onBack();
  private readonly handleRetry = (): void => this.onRetry();
  private center: { x: number; y: number } | null = null;
  private radius = 48;
  private visible = false;

  constructor(private readonly onBack: () => void, private readonly onRetry: () => void) {
    this.lines.classList.add("focus-lines");
    this.lines.setAttribute("aria-hidden", "true");
    this.element.append(this.lines, this.backButton, this.body);
    this.element.hidden = true;
    this.backButton.addEventListener("click", this.handleBack);
    this.retryButton.addEventListener("click", this.handleRetry);
  }

  update(profile: ProfileState, record: WorldRecord | null): void {
    if (profile.status === "idle") {
      this.visible = false;
      this.element.hidden = true;
      this.body.replaceChildren();
      this.lines.replaceChildren();
      return;
    }
    this.visible = true;
    this.element.hidden = false;
    this.element.dataset.state = profile.status;
    const name = record?.name ?? `NeoWs ${profile.neowsId}`;
    const title = el("h2", { className: "focus-title", text: name });

    if (profile.status === "loading") {
      const loading = ["Identity", "Encounter", "Orbit", "Sentry"].map((t, i): Callout => ({
        key: `loading-${i}`, title: t, source: "", rows: [{ label: "", value: LOADING }], note: null,
      }));
      this.body.replaceChildren(title, ...loading.map((c, i) => this.callout(c, i)));
    } else if (profile.status === "error") {
      const notFound = profile.error.kind === "not_found";
      const card = el("div", { className: "focus-error" }, [
        el("p", { className: "status-title", text: notFound ? "Asteroid not found" : "Profile unavailable" }),
        el("p", { text: notFound ? `No asteroid with NeoWs ID ${profile.neowsId} exists in the platform's data.` : "The intelligence service could not provide this profile." }),
      ]);
      if (!notFound) card.append(this.retryButton);
      this.body.replaceChildren(title, card);
    } else {
      const model = buildCallouts(profile.profile);
      const extras = el("div", { className: "focus-extras" });
      if (model.unavailable.length > 0) {
        extras.append(el("p", { className: "focus-unavailable-title", text: "Not available" }));
        for (const u of model.unavailable) {
          extras.append(el("p", { className: "focus-unavailable", text: `${u.title} · ${u.source} — ${u.reason}` }));
        }
      }
      const provenance = el("details", { className: "focus-provenance" }, [el("summary", { text: "Provenance" })]);
      for (const line of provenanceLines(profile.profile)) provenance.append(el("p", { text: line }));
      extras.append(provenance);
      this.body.replaceChildren(title, ...model.callouts.map((c, i) => this.callout(c, i)), extras);
    }
    if (record) title.append(el("span", { className: "focus-subtitle", text: `Miss distance ${formatNumber(record.encounter.miss_distance_km, "km", 0)} · NASA NeoWs` }));
    this.place();
  }

  /** Keep callouts and leader lines anchored to the asteroid's current screen position. */
  setAnchor(center: { x: number; y: number } | null, focus: number, radius = 48): void {
    this.center = center;
    this.radius = radius;
    this.element.style.opacity = String(Math.min(1, Math.max(0, (focus - 0.55) / 0.45)));
    this.place();
  }

  private callout(c: Callout, slot: number): HTMLElement {
    const node = el("article", { className: "callout", attrs: { "data-callout": c.key, "data-slot": String(slot) } }, [
      el("header", {}, [el("h3", { text: c.title }), c.source ? el("p", { className: "source", text: c.source }) : null]),
      ...c.rows.map((r) => el("div", { className: "fact" }, [el("span", { className: "fact-label", text: r.label }), el("span", { className: "fact-value", text: r.value })])),
      c.note ? el("p", { className: "note", text: c.note }) : null,
    ]);
    return node;
  }

  private place(): void {
    if (!this.visible || !this.center) return;
    const { x, y } = this.center;
    const rect = this.element.getBoundingClientRect();
    const radius = this.radius;
    this.lines.setAttribute("width", String(rect.width));
    this.lines.setAttribute("height", String(rect.height));
    const nodes = [...this.body.querySelectorAll<HTMLElement>(".callout")];
    const columns: HTMLElement[][] = [[], []]; // left, right: callouts alternate sides in slot order
    nodes.forEach((node, i) => columns[i % 2]!.push(node));
    const paths: SVGPathElement[] = [];
    columns.forEach((column, sideIndex) => {
      const side = sideIndex === 0 ? -1 : 1;
      const total = column.reduce((sum, n) => sum + n.offsetHeight, 0) + STACK_GAP * Math.max(0, column.length - 1);
      let top = Math.max(TOP_CLEARANCE, Math.min(rect.height - BOTTOM_CLEARANCE - total, y - rect.top - total / 2));
      for (const node of column) {
        const left = side < 0 ? x - rect.left - radius - GAP_X - node.offsetWidth : x - rect.left + radius + GAP_X;
        node.style.left = `${Math.max(8, Math.min(rect.width - node.offsetWidth - 8, left))}px`;
        node.style.top = `${top}px`;
        const anchorX = side < 0 ? Math.max(8, left) + node.offsetWidth : Math.min(rect.width - node.offsetWidth - 8, left);
        const anchorY = top + 18;
        const angle = Math.atan2(anchorY - (y - rect.top), anchorX - (x - rect.left));
        const startX = x - rect.left + Math.cos(angle) * radius * 0.92;
        const startY = y - rect.top + Math.sin(angle) * radius * 0.92;
        const elbowX = anchorX - side * 28;
        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute("d", `M ${startX} ${startY} L ${elbowX} ${anchorY} L ${anchorX} ${anchorY}`);
        paths.push(path);
        top += node.offsetHeight + STACK_GAP;
      }
    });
    this.lines.replaceChildren(...paths);
  }

  dispose(): void {
    this.backButton.removeEventListener("click", this.handleBack);
    this.retryButton.removeEventListener("click", this.handleRetry);
    this.element.remove();
  }
}
