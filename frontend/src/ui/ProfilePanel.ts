import { PROFILE_SPEC, type AsteroidProfile, type SectionSpec } from "../models/profile";
import type { ProfileState } from "../state/store";
import { el } from "./dom";
import { formatField, LOADING, REASON_TEXT, SENTRY_STATUS_TEXT, sourceLabel } from "./format";
import type { SentryLinkageStatus } from "../models/world";

/**
 * The asteroid intelligence profile. Each section shows its declared source; unavailable values
 * show "Unavailable"/"Unknown" plus the contract's reason. While loading, every value reads
 * "Loading…" (never a placeholder number).
 */
export class ProfilePanel {
  readonly element = el("aside", { className: "profile-panel", attrs: { "aria-label": "Asteroid profile" } });
  private readonly backButton = el("button", { className: "button", text: "← Back to world", attrs: { type: "button" } });
  private readonly retryButton = el("button", { className: "button", text: "Retry", attrs: { type: "button" } });
  private readonly handleBack = (): void => this.onBack();
  private readonly handleRetry = (): void => this.onRetry();

  constructor(private readonly onBack: () => void, private readonly onRetry: () => void) {
    this.element.hidden = true;
    this.backButton.addEventListener("click", this.handleBack);
    this.retryButton.addEventListener("click", this.handleRetry);
  }

  update(profile: ProfileState, displayName: string | null): void {
    if (profile.status === "idle") {
      this.element.hidden = true;
      this.element.replaceChildren();
      return;
    }
    this.element.hidden = false;
    this.element.dataset.state = profile.status;
    const header = el("header", { className: "profile-header" }, [
      this.backButton,
      el("h2", { text: displayName ?? `NeoWs ${profile.neowsId}` }),
    ]);

    if (profile.status === "loading") {
      this.element.replaceChildren(header, ...PROFILE_SPEC.map((spec) => this.loadingSection(spec)));
      return;
    }
    if (profile.status === "error") {
      const notFound = profile.error.kind === "not_found";
      const nodes: Node[] = [
        header,
        el("p", { className: "status-title", text: notFound ? "Asteroid not found" : "Profile unavailable" }),
        el("p", {
          text: notFound
            ? `No asteroid with NeoWs ID ${profile.neowsId} exists in the platform's data.`
            : "The intelligence service could not provide this profile.",
        }),
      ];
      if (!notFound) nodes.push(this.retryButton);
      this.element.replaceChildren(...nodes);
      return;
    }
    this.element.replaceChildren(header, ...this.readySections(profile.profile));
  }

  private loadingSection(spec: SectionSpec): HTMLElement {
    return el("section", { className: "profile-section" }, [
      el("h3", { text: spec.title }),
      ...spec.fields.map((f) => this.row(f.label, LOADING, null)),
    ]);
  }

  private readySections(profile: AsteroidProfile): HTMLElement[] {
    const sections = PROFILE_SPEC.map((spec) => {
      const section = profile.sections[spec.key];
      const children: (Node | null)[] = [
        el("h3", { text: spec.title }),
        el("p", { className: "source", text: `Source: ${sourceLabel(section.source)}` }),
      ];
      if (spec.key === "sentry_assessment") {
        const status = profile.sentryStatus as SentryLinkageStatus;
        children.push(
          el("p", { className: "sentry-status", text: `Linkage: ${SENTRY_STATUS_TEXT[status] ?? profile.sentryStatus}` }),
          el("p", { className: "note", text: `Published values only (${profile.sentrySourceContract}); no score is derived.` }),
        );
      }
      for (const field of spec.fields) {
        const value = section.values[field.key] ?? null;
        const reason = value === null ? section.availability?.unavailable[field.key] : undefined;
        children.push(this.row(field.label, formatField(field, value), reason ? REASON_TEXT[reason] : null));
      }
      return el("section", { className: "profile-section", attrs: { "data-section": spec.key } }, children);
    });

    const provenance = Object.entries(profile.provenance).map(([source, block]) =>
      this.row(sourceLabel(source), Object.entries(block)
        .filter(([key, v]) => key !== "source" && v !== null)
        .map(([key, v]) => `${key}: ${String(v)}`)
        .join(" · ") || "No snapshot metadata recorded", null),
    );
    sections.push(el("section", { className: "profile-section", attrs: { "data-section": "provenance" } }, [
      el("h3", { text: "Provenance" }), ...provenance,
    ]));
    return sections;
  }

  private row(label: string, value: string, reason: string | null): HTMLElement {
    return el("div", { className: "fact" }, [
      el("span", { className: "fact-label", text: label }),
      el("span", { className: "fact-value", text: value }),
      reason ? el("span", { className: "fact-reason", text: reason }) : null,
    ]);
  }

  dispose(): void {
    this.backButton.removeEventListener("click", this.handleBack);
    this.retryButton.removeEventListener("click", this.handleRetry);
    this.element.remove();
  }
}
