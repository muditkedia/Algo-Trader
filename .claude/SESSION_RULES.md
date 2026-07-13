# Session Rules

Every development session must follow this sequence.

---

## Phase 1 - Understand

Read:

- .claude/CLAUDE.md
- docs/PROJECT_STATE.md

If relevant, also read:

- architecture/
- docs/DECISIONS.md
- docs/LEARNINGS.md

Do not begin coding before understanding the current project state.

---

## Phase 2 - Plan

Produce a concise implementation plan.

The plan should include:

- objective
- files to modify
- expected outcome
- possible risks

Wait for approval before making changes.

---

## Phase 3 - Implement

Implement only the approved plan.

Avoid unrelated refactoring.

Keep changes modular.

---

## Phase 4 - Validate

Run appropriate validation.

Examples include:

- linting
- syntax checks
- unit tests
- Freqtrade validation
- backtests

Use the smallest validation necessary for the task.

---

## Phase 5 - Summarize

At the end of every session provide:

- Files created
- Files modified
- Files deleted
- Summary of changes
- Validation performed
- Remaining work

---

## Phase 6 - Documentation

When appropriate, update:

- PROJECT_STATE.md
- DECISIONS.md
- LEARNINGS.md

Do not modify architecture documents without approval.

---

## General Rules

Never guess.

Never fabricate results.

Never bypass validation.

Prefer maintainability over cleverness.

Keep implementations simple unless complexity is required.