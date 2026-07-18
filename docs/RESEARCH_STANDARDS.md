# Research Standards (PERMANENT POLICY)

_Established Phase 13, 2026-07-18. This is permanent project policy, on the same
footing as `architecture/VALIDATION_RULES.md`. It governs HOW research is done;
VALIDATION_RULES governs how a result is JUDGED. Both are frozen; amendments
require explicit owner approval and a DECISIONS entry._

The project has transitioned from strategy implementation to systematic
quantitative research (D-033). These standards make that permanent. They exist
because the project has already learned, expensively, that undisciplined search
manufactures false discoveries (L-010: random entries passed an un-benchmarked
gate; the multiple-testing risk of high-throughput screening).

---

## 1. Mandatory research standards (Part E)

Every research effort, without exception, must satisfy these. A result that
skipped any of them is not evidence and must not inform a decision.

### 1.1 Pre-registration before implementation
Every idea exists as a **Research Registry entry (`RESEARCH_REGISTRY.md`) BEFORE
any code**, and its measurable prediction, parameters, horizons and falsification
criteria are frozen in a pre-registration note (as batches 1-2 did) **before the
first measurement**. Parameters chosen or changed after seeing a result are
curve-fitting (D-026) and void the result.

### 1.2 Measurable, falsifiable hypotheses
An idea must state a **specific, quantitative prediction** and the **observation
that would falsify it**. "Momentum works" is not a hypothesis; "top-decile 12-1
momentum has a selection edge whose 95% CI-low exceeds cost at a 21-63-day
horizon" is. If no observation could refute it, it is not research.

### 1.3 Objective, pre-existing success criteria
Success is the **frozen D-031 gate** (selection edge CI-low > cost, plus the §7
bars) — never a criterion invented for the idea. No new statistic, threshold,
baseline, or benchmark may be introduced to make a result look better. Do NOT
chase PASS; do NOT weaken standards.

### 1.4 Reproducibility
Every result must be **deterministic and replayable**: fixed seeds, committed
dated data snapshots (as `user_data/universe/ind_nifty500_2026-07.csv`),
evidence persisted to the SQLite store, and the exact command recorded. A number
that cannot be regenerated bit-for-bit does not exist.

### 1.5 Documentation
Each effort leaves: the registry entry, the pre-registration note, the evidence
rows in the DB (a new evaluation generation — never overwrite history), and a
DECISIONS/LEARNINGS entry with the verdict AND the reasoning. Point estimates
are always reported WITH their confidence intervals (L-011).

### 1.6 Promotion workflow
`Idea → Prioritised → Ready → Implemented → {measured | rejected} → …`. A
strategy may advance toward paper/live **only** on a certified PASS under the
frozen gate, confirmed out-of-sample/walk-forward. The paper engine already
enforces this in code (D-021): it refuses anything not `measured`+. No manual
override.

### 1.7 Archival of negative results
**Negative results are kept, never deleted.** A rejected idea becomes a registry
entry with status `Rejected`/`Archived` and its evidence, so it is never
silently re-proposed. The 27 rejected strategies are the project's most valuable
asset — the map of what does not work. Evidence is the product (D-010).

---

## 2. Hypothesis Quality Framework (Part B)

An objective rubric to **prioritise which ideas to research first** — NOT to
predict whether they are profitable (only the gate judges that, on evidence).
Each idea scores 1-5 on seven dimensions; the weighted sum is its research
priority. Weights encode the two hardest-won lessons of the project: statistical
power and independence from what already failed dominate.

| Dimension | 1 (low) | 5 (high) | Weight |
|---|---|---|---|
| **Expected statistical power** | long horizon, few independent periods (e.g. 6-mo hold) | short horizon / many near-independent events (e.g. tom's 3-7d × 53k) | **×3** |
| **Independence from rejected ideas** | a re-parameterised dead idea (per-symbol trend, long-horizon single factor, low-vol) | a genuinely new mechanism or data source | **×2.5** |
| **Theoretical justification** | pattern with no mechanism | peer-reviewed causal story, replicated | ×2 |
| **Data availability** | needs external data we don't have | fully in the current store | ×1.5 |
| **Implementation effort** (inverse) | new platform capability | a few lines via the hypothesis framework | ×1.5 |
| **Novelty** | already tested here | untested family/angle | ×1 |
| **Business value** | uncapacitiable / undeployable even if real | scalable, deployable | ×1 |

**Priority score = Σ(weight × score) / Σ(weights)**, on a 1-5 scale.

Why these weights: the project's binding constraint is statistical POWER (L-012 —
horizon beats sample size), so it is weighted highest; the second-biggest waste
was re-testing variants of rejected ideas, so INDEPENDENCE is next. A
theoretically-grounded, powered, novel, in-data, cheap idea scores ~5; a
long-horizon re-parameterisation of a dead factor scores ~2 regardless of how
famous the anomaly is.

**The framework ranks research ORDER. It never asserts an idea will pass.** A
high score means "worth measuring soon", a low score means "not yet / not again".

---

## 3. Standing controls (permanent, from hard experience)

- **Benchmark-relative always** (D-031): every verdict is selection edge vs a
  random baseline, never absolute return.
- **Multiple-testing discipline**: when screening many hypotheses, report the
  full selection-edge DISTRIBUTION and the COUNT tested; treat any single PASS as
  provisional pending pre-registered out-of-sample confirmation. Testing N ideas
  at 95% yields ~N/20 false PASSes by chance — the registry's count is the
  denominator.
- **Cost is first-order** (D-007): every result is net of the full NSE charge
  stack; a thesis that lives in illiquid names (illiq, reversal) must revisit the
  cost model before any deployment claim.
- **Confidence heuristics are banned as evidence** (L-003, 4×): signal-quality
  scores carry no information and must never gate or rank a promotion; if ever
  modelled, they must be learned from evidence and themselves validated.
