# Algo Trader - Claude Operating Instructions

## Project Goal

Build a modular, adaptive algorithmic trading system using Freqtrade.

The objective is long-term profitability through disciplined engineering, extensive validation, and continuous improvement.

This is a production-quality software project, not a prototype.

---

## Technology Stack

- Python
- Freqtrade
- Docker
- Git
- GitHub
- VS Code

---

## Development Principles

Always understand the existing code before making changes.

Never assume.

Never invent APIs.

Never create placeholder implementations unless explicitly requested.

Never remove existing functionality without approval.

Never introduce unnecessary complexity.

Keep every implementation modular.

---

## Required Workflow

For every development task:

1. Read the relevant project documentation.
2. Explain the implementation plan.
3. Wait for approval before making changes.
4. Implement.
5. Run appropriate validation.
6. Summarize every modified file.
7. Update PROJECT_STATE.md when required.

---

## Code Quality

Prioritize:

- readability
- maintainability
- modularity
- correctness

Avoid duplicated logic.

Prefer small reusable functions.

Document non-obvious decisions.

---

## Trading Principles

Never introduce look-ahead bias.

Never overfit strategies.

Always validate using realistic assumptions.

Always preserve reproducibility.

---

## Project Documentation

Before coding, always consult:

- architecture/
- docs/PROJECT_STATE.md

When appropriate, also consult:

- prompts/
- docs/DECISIONS.md
- docs/LEARNINGS.md

---

## Session Rules

If project context is unclear:

Stop.

Ask questions.

Never guess.
