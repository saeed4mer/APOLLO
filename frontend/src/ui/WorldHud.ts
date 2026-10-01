import type { WorldSnapshotInfo } from "../models/world";
import { introOpacity } from "../scene/atmosphere";
import { DISTANCE_MIN_KM, SKY_PROJECTION } from "../scene/skyLayout";
import type { WorldState } from "../state/store";
import { el } from "./dom";
import { formatKmCompact } from "./format";

/**
 * Minimal world chrome: an opening title + scroll hint (fades as the user goes deeper), loading
 * and error states, and an "About this view" panel holding every disclaimer about the visual model.
 */
export class WorldHud {
  readonly element = el("div", { className: "world-hud" });
  private readonly intro = el("div", { className: "intro" }, [
    el("h1", { text: "ASTEROID INTELLIGENCE" }),
    el("p", { text: "Scroll to explore" }),
  ]);
  private readonly status = el("section", { className: "status-overlay", attrs: { role: "status", "aria-live": "polite" } });
  private readonly retryButton = el("button", { className: "button", text: "Retry", attrs: { type: "button" } });
  private readonly aboutButton = el("button", { className: "button about-button", text: "About this view", attrs: { type: "button", "aria-expanded": "false" } });
  private readonly about = el("aside", { className: "about-panel", attrs: { "aria-label": "About this view" } });
  private readonly aboutBody = el("div");
  private readonly closeAbout = el("button", { className: "button", text: "Close", attrs: { type: "button" } });
  private readonly handleRetry = (): void => this.onRetry();
  private readonly toggleAbout = (): void => this.setAboutOpen(this.about.hidden);
  private readonly hideAbout = (): void => this.setAboutOpen(false);

  constructor(private readonly onRetry: () => void) {
    this.about.hidden = true;
    this.about.append(el("h2", { text: "About this view" }), this.aboutBody, this.closeAbout);
    this.element.append(this.intro, this.status, this.aboutButton, this.about);
    this.retryButton.addEventListener("click", this.handleRetry);
    this.aboutButton.addEventListener("click", this.toggleAbout);
    this.closeAbout.addEventListener("click", this.hideAbout);
    this.renderAbout(null, 0, 0);
  }

  update(world: WorldState): void {
    this.status.dataset.state = world.status;
    if (world.status === "loading") {
      this.status.hidden = false;
      this.status.replaceChildren(el("p", { className: "status-title", text: "INITIALIZING ASTEROID INTELLIGENCE FIELD" }));
    } else if (world.status === "error") {
      this.status.hidden = false;
      const detail = world.error.kind === "timeout"
        ? "The intelligence service did not respond in time."
        : world.error.kind === "network"
          ? "The intelligence service could not be reached."
          : world.error.kind === "malformed"
            ? "The intelligence service returned data this renderer cannot trust."
            : "The intelligence service returned an error.";
      this.status.replaceChildren(
        el("p", { className: "status-title", text: "ASTEROID INTELLIGENCE UNAVAILABLE" }),
        el("p", { text: detail }),
        this.retryButton,
      );
    } else {
      const { rejected } = world.data;
      this.status.hidden = rejected.length === 0;
      this.status.replaceChildren(el("p", { className: "status-warning", text: `${rejected.length} record(s) failed validation and are not shown.` }));
      this.renderAbout(world.data.snapshot, world.data.records.length, rejected.length);
    }
  }

  setProgress(progress: number, focus: number): void {
    this.intro.style.opacity = String(introOpacity(progress) * (1 - focus));
  }

  private setAboutOpen(open: boolean): void {
    this.about.hidden = !open;
    this.aboutButton.setAttribute("aria-expanded", String(open));
  }

  private renderAbout(snapshot: WorldSnapshotInfo | null, shown: number, rejected: number): void {
    const lines = [
      "This is an illustrative visualization, not a physics simulation or an orbit propagator.",
      "Scrolling travels outward from Earth: it reveals real distance, from 0 km to beyond the farthest asteroid. " +
        "An asteroid appears only once the revealed distance reaches its exact NeoWs miss distance (closest first), " +
        "and retreats again if you scroll back below it.",
      "Height above the Earth arc follows each asteroid's exact miss distance on a logarithmic scale starting at " +
        `Earth's radius (${formatKmCompact(DISTANCE_MIN_KM)}): nearer objects always rest lower. The ruler on the right marks every 1,000,000 km; labels are rounded, positions never are.`,
      "The Moon marks the Earth-Moon distance (384,400 km) for scale. It is not part of the asteroid data.",
      "The warning badge marks asteroids whose NASA NeoWs 'potentially hazardous' flag is true. It is not an impact prediction, a Sentry result or a risk score.",
      `Horizontal placement is the longitude of the backend's deterministic illustrative direction (${snapshot?.spatial_model.direction_algorithm ?? "..."}; renderer projection ${SKY_PROJECTION}). It is not an observed approach direction.`,
      "The falling motion and fiery trail are a visual metaphor for approach, identical for every asteroid. They are not trajectories.",
      "Every asteroid is drawn the same size and colour: neither encodes diameter, hazard or Sentry status.",
      "Stars, terrain and the Moon are context, not data.",
      snapshot ? `${shown} NeoWs object(s) shown from GET /asteroids/world${rejected ? `; ${rejected} rejected by validation` : ""}.` : "Loading the asteroid population...",
    ];
    this.aboutBody.replaceChildren(...lines.map((text) => el("p", { text })));
  }

  dispose(): void {
    this.retryButton.removeEventListener("click", this.handleRetry);
    this.aboutButton.removeEventListener("click", this.toggleAbout);
    this.closeAbout.removeEventListener("click", this.hideAbout);
    this.element.remove();
  }
}
