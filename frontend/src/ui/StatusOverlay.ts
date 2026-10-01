import type { WorldState } from "../state/store";
import { el } from "./dom";

/** World loading / error / loaded banner. Never shows placeholder asteroids or fake counts. */
export class StatusOverlay {
  readonly element = el("section", { className: "status-overlay", attrs: { role: "status", "aria-live": "polite" } });
  private readonly retryButton = el("button", { className: "button", text: "Retry", attrs: { type: "button" } });

  private readonly handleRetry = (): void => this.onRetry();

  constructor(private readonly onRetry: () => void) {
    this.retryButton.addEventListener("click", this.handleRetry);
  }

  update(world: WorldState): void {
    this.element.dataset.state = world.status;
    switch (world.status) {
      case "loading":
        this.element.replaceChildren(el("p", { className: "status-title", text: "INITIALIZING ASTEROID INTELLIGENCE FIELD" }));
        return;
      case "error": {
        const detail = world.error.kind === "timeout"
          ? "The intelligence service did not respond in time."
          : world.error.kind === "network"
            ? "The intelligence service could not be reached."
            : world.error.kind === "malformed"
              ? "The intelligence service returned data this renderer cannot trust."
              : "The intelligence service returned an error.";
        this.element.replaceChildren(
          el("p", { className: "status-title", text: "ASTEROID INTELLIGENCE UNAVAILABLE" }),
          el("p", { text: detail }),
          this.retryButton,
        );
        return;
      }
      case "ready": {
        const { records, rejected } = world.data;
        const nodes: Node[] = [el("p", { className: "status-title", text: `LOADED · ${records.length} OBJECTS` })];
        if (rejected.length > 0) {
          nodes.push(el("p", { className: "status-warning", text: `${rejected.length} record(s) failed validation and are not shown.` }));
        }
        this.element.replaceChildren(...nodes);
      }
    }
  }

  dispose(): void {
    this.retryButton.removeEventListener("click", this.handleRetry);
    this.element.remove();
  }
}
