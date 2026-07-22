# Project Memory

This file contains the persistent working memory for the project.

## Current Objective

Build a modular adaptive algorithmic trading platform for Indian equities on Angel One SmartAPI.

Primary goals:

- Correctness
- Reproducibility
- Maintainability
- Risk control

Profitability is evaluated only after engineering validation.

---

## Technology

- Python
- Angel One SmartAPI
- Git
- GitHub
- VS Code

---

## Development Model

The project follows a vertical-slice architecture.

Every major feature must be completed end-to-end before expanding.

Example:

EMA Strategy

↓

Backtest

↓

Validation

↓

Bias Detection

↓

Documentation

↓

Only then begin the next strategy.

---

## Absolute Rules

Never introduce lookahead bias.

Never overfit.

Never skip validation.

Never modify architecture without approval.

Never delete working functionality without approval.

Never invent APIs.

Never fabricate results.

---

## Session Ending Checklist

Every completed session should leave the repository in a better state than it started.

Required:

- PROJECT_STATE updated
- LEARNINGS updated (if applicable)
- Git status clean
- Tests passing
- Changes summarized
