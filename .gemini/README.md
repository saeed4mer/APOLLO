# APOLLO — Gemini & AI Assistant Workspace Guide

This directory houses operational directives, safety invariants, and context runbooks for AI assistants and pair programmers interacting with the NASA Intelligence Platform codebase.

## Directory Structure
- `rules/`: Authoritative standards and scientific safety guardrails that must be upheld across all code modifications.
  - `architectural_invariants.md`: Locked rules on Sentry linkage, PHA classifications, and missingness semantics.
  - `coding_standards.md`: Python and TypeScript style, typing, and testing conventions.
- `prompts/`: Reusable task workflows and diagnostic runbooks (e.g. data quality failure triage, crosswalk inspection).
- `config.json`: Agent environment configuration.
