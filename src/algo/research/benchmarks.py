"""Benchmark battery (D-031) - every strategy measured against market exposure.

L-010 showed the absolute-return gate can be cleared by market participation
alone: 2 of 3 random-entry controls PASSed at multi-week horizons on a bullish,
survivorship-biased corpus. This module makes the comparison explicit and
permanent. A strategy is measured against baselines that share its market
exposure, so what survives is selection skill, not drift.

Baselines (methodology / assumptions / reproducibility / limitations each
documented on the function that builds it):

  1. Buy & Hold, matched per trade  - same symbol, same entry bar, held the
                                       strategy's full max horizon, net one
                                       round trip. Isolates entry+exit skill
                                       from "being long this exact name".
  2. Buy & Hold, portfolio          - equal-weight the traded universe over the
                                       measured window. The "just be long the
                                       market" number.
  3. Random entry, K seeds          - random (symbol, bar) entries, IDENTICAL
                                       risk engine / costs / management, matched
                                       to the strategy's trade count. Isolates
                                       entry selection from management.
  4. Random entry, matched holding  - as (3) but every trade is force-closed at
                                       the strategy's mean realized holding, so
                                       the comparison removes the exit engine.

The random baselines are drawn with seeded generators, so the whole battery is
deterministic and replayable. The managed simulation is the engine's own
``simulate_entries`` - a benchmark is priced through the same path as the
strategy, or the comparison would not be like-for-like.

Comparative metrics (Part B), with block-bootstrap CIs where valid:
  Selection Edge (entry level)  - lives on the EdgeReport (edge_lab); the gate.
  Excess vs Buy & Hold          - per-trade strategy return minus matched B&H.
  Excess vs Random              - strategy expectancy minus random expectancy.
  Information Ratio             - mean/std of the per-trade excess vs B&H.
  Relative Profit Factor        - strategy PF / random PF.
  Relative Drawdown             - strategy maxDD minus random maxDD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd

from algo.core.costs import Product
from algo.core.logging import get_logger
from algo.research import edge_lab
from algo.research.validation import metrics

logger = get_logger("research.benchmarks")

#: Deterministic seeds for the random-entry baselines. More than one so the
#: baseline is a distribution, not a single lucky/unlucky draw.
DEFAULT_SEEDS = (101, 102, 103)

#: The random baseline is drawn matched to the strategy's trade count, but
#: capped here for tractability: a high-frequency strategy (tens of thousands of
#: signals) needs no more than a few thousand random draws to estimate the
#: baseline mean/PF/DD stably, and the strategy's OWN trades are always used in
#: full. The cap only widens the baseline's own CI slightly for such strategies;
#: it never changes the strategy's numbers. Documented per VALIDATION_RULES 26.
MAX_RANDOM_TRADES = 3000


def _median_hold_bars(trades: pd.DataFrame, tf_minutes: float) -> int:
    if trades.empty or "trade_duration" not in trades.columns:
        return 1
    med_min = float(trades["trade_duration"].median())
    return max(1, int(round(med_min / tf_minutes)))


def _eligible_indices(prepared, min_index: int, max_lead: int) -> Dict[str, np.ndarray]:
    """Bars that could host a random entry: past warm-up, with forward room.

    ``min_index`` skips the indicator warm-up region (so a random entry sees the
    same matured-history precondition the strategy's own signals do); ``max_lead``
    reserves forward bars so the managed trade can run its full horizon.
    """
    out = {}
    for symbol, frame in prepared.frames.items():
        n = len(frame)
        hi = n - max_lead
        if hi > min_index:
            out[symbol] = np.arange(min_index, hi)
    return out


def random_entries(prepared, n: int, seed: int, min_index: int,
                   max_lead: int) -> Dict[str, np.ndarray]:
    """Draw ``n`` random (symbol, bar) entries across the eligible universe.

    Deterministic under ``seed``. Entries are sampled without replacement from
    the flattened pool of eligible bars, so the same bar is never entered twice
    and the draw is matched to the strategy's trade count.
    """
    eligible = _eligible_indices(prepared, min_index, max_lead)
    pool = [(sym, int(i)) for sym, arr in eligible.items() for i in arr]
    if not pool or n <= 0:
        return {}
    rng = np.random.default_rng(seed)
    take = min(n, len(pool))
    chosen = rng.choice(len(pool), size=take, replace=False)
    by_symbol: Dict[str, list] = {}
    for c in chosen:
        sym, i = pool[c]
        by_symbol.setdefault(sym, []).append(i)
    return {s: np.array(sorted(v)) for s, v in by_symbol.items()}


def buy_and_hold_matched(prepared, actual_trades: pd.DataFrame,
                         max_hold_bars: int, cost_pct: float) -> pd.Series:
    """Per actual trade, the net return of holding the SAME symbol from the
    same entry bar for the full ``max_hold_bars`` horizon (one round trip).

    Assumptions: entry and exit at the bar close; a single round-trip cost
    applied (delivery, same as the strategy). Limitation: uses the strategy's
    realized entry bars, so it answers "did management beat naive hold of the
    names you picked", NOT "did you pick the right names" (that is the
    selection edge). Indexed like ``actual_trades``.
    """
    if actual_trades.empty:
        return pd.Series(dtype=float)
    # date -> row position, per symbol, built once
    pos: Dict[str, dict] = {}
    closes: Dict[str, np.ndarray] = {}
    for symbol, frame in prepared.frames.items():
        pos[symbol] = {d: i for i, d in enumerate(frame["date"])}
        closes[symbol] = frame["close"].to_numpy(float)
    out = []
    for t in actual_trades.itertuples(index=False):
        sym = t.pair
        i = pos.get(sym, {}).get(pd.Timestamp(t.open_date))
        c = closes.get(sym)
        if i is None or c is None or i + max_hold_bars >= len(c):
            out.append(np.nan)
            continue
        gross = c[i + max_hold_bars] / c[i] - 1.0
        out.append(gross - cost_pct)
    return pd.Series(out, index=actual_trades.index, dtype=float)


def portfolio_buy_and_hold(prepared, cost_pct: float) -> float:
    """Equal-weight buy&hold of every traded symbol over the measured window,
    net one round trip. The 'just be long the market' return the strategy must
    beat to justify its activity. Limitation: uses the CURRENT universe over the
    whole window (survivorship) - it is a generous benchmark by construction,
    which is the point (beating a generous drift benchmark means more)."""
    rets = []
    for frame in prepared.frames.values():
        c = frame["close"].to_numpy(float)
        if len(c) > 1 and c[0] > 0:
            rets.append(c[-1] / c[0] - 1.0 - cost_pct)
    return float(np.mean(rets)) if rets else float("nan")


@dataclass
class BenchmarkReport:
    n_trades: int
    strategy: dict = field(default_factory=dict)
    random_seeds: dict = field(default_factory=dict)
    random_matched_hold: dict = field(default_factory=dict)
    buy_hold_portfolio: float = float("nan")
    #: Part-B comparative metrics (the summary the league table reads).
    excess_vs_bh: float = float("nan")
    excess_vs_bh_ci: tuple = (float("nan"), float("nan"))
    excess_vs_random: float = float("nan")
    excess_vs_random_ci: tuple = (float("nan"), float("nan"))
    information_ratio: Optional[float] = None
    relative_profit_factor: Optional[float] = None
    relative_drawdown: Optional[float] = None

    def summary(self) -> dict:
        return {
            "n_trades": self.n_trades,
            "buy_hold_portfolio": _r(self.buy_hold_portfolio),
            "excess_vs_bh": _r(self.excess_vs_bh),
            "excess_vs_bh_ci": [_r(x) for x in self.excess_vs_bh_ci],
            "excess_vs_random": _r(self.excess_vs_random),
            "excess_vs_random_ci": [_r(x) for x in self.excess_vs_random_ci],
            "information_ratio": _r(self.information_ratio, 3),
            "relative_profit_factor": _r(self.relative_profit_factor, 3),
            "relative_drawdown": _r(self.relative_drawdown),
            "random_expectancy": _r(self.random_seeds.get("expectancy")),
            "random_profit_factor": _r(self.random_seeds.get("profit_factor"), 3),
        }


def _r(x, dp: int = 5):
    return None if x is None or (isinstance(x, float) and x != x) \
        else round(float(x), dp)


def _two_sample_diff_ci(a_vals: pd.Series, a_days: pd.Series,
                        b_vals: pd.Series, b_days: pd.Series,
                        block_days: int, seed: int = 7) -> tuple:
    """CI of mean(a) - mean(b), each resampled by independent day-blocks."""
    return edge_lab.selection_diff_ci(a_vals, a_days, b_vals, b_days,
                                      block_days, seed=seed)


def run_benchmarks(engine, strategy, prepared, actual_trades: pd.DataFrame, *,
                   product: Product, edge: edge_lab.EdgeReport,
                   start_capital: float = 100_000.0,
                   seeds: Sequence[int] = DEFAULT_SEEDS) -> BenchmarkReport:
    """Compute the full benchmark battery for one strategy.

    Deterministic given ``seeds``. ``edge`` supplies the cost and the entry-level
    selection edge (the gate); this adds the managed-trade comparisons.
    """
    from algo.data.ohlcv import timeframe_minutes

    n = len(actual_trades)
    report = BenchmarkReport(n_trades=n)
    if n < 1:
        return report

    tf_min = timeframe_minutes(strategy.meta.timeframe)
    max_hold = int(strategy.meta.max_hold_bars)
    cost = edge.cost_pct
    min_index = strategy.min_history()
    n_random = min(n, MAX_RANDOM_TRADES)               # capped for tractability
    block = max(1, int(round(float(actual_trades["trade_duration"].median())
                             / tf_min))) if "trade_duration" in actual_trades \
        else 1

    strat_summary = metrics.summarize(actual_trades, start_capital)
    report.strategy = strat_summary

    # --- baseline 3: random entry, K seeds, natural managed exits -----------
    random_frames = []
    for s in seeds:
        entries = random_entries(prepared, n_random, s, min_index, max_hold)
        rf = engine.simulate_entries(entries, prepared, product=product,
                                     max_bars=max_hold, enter_tag="random")
        if not rf.empty:
            random_frames.append(rf)
    random_all = pd.concat(random_frames, ignore_index=True) \
        if random_frames else pd.DataFrame(columns=actual_trades.columns)
    report.random_seeds = metrics.summarize(random_all, start_capital) \
        if not random_all.empty else {}

    # --- baseline 4: random entry, exits forced to the strategy's hold ------
    hold_bars = _median_hold_bars(actual_trades, tf_min)
    matched_frames = []
    for s in seeds:
        entries = random_entries(prepared, n_random, s + 1000, min_index, max_hold)
        mf = engine.simulate_entries(entries, prepared, product=product,
                                     max_bars=hold_bars, enter_tag="random_mh")
        if not mf.empty:
            matched_frames.append(mf)
    matched_all = pd.concat(matched_frames, ignore_index=True) \
        if matched_frames else pd.DataFrame()
    report.random_matched_hold = metrics.summarize(matched_all, start_capital) \
        if not matched_all.empty else {}

    # --- baselines 1 & 2: buy & hold ----------------------------------------
    bh_matched = buy_and_hold_matched(prepared, actual_trades, max_hold, cost)
    report.buy_hold_portfolio = portfolio_buy_and_hold(prepared, cost)

    # --- Part-B comparative metrics -----------------------------------------
    strat_ret = actual_trades["profit_ratio"]
    strat_days = pd.to_datetime(actual_trades["open_date"]).dt.normalize()

    excess_bh = (strat_ret - bh_matched).dropna()
    if not excess_bh.empty:
        report.excess_vs_bh = float(excess_bh.mean())
        eb_days = strat_days.loc[excess_bh.index]
        report.excess_vs_bh_ci = edge_lab.block_bootstrap_ci(
            excess_bh, eb_days, block)
        std = float(excess_bh.std(ddof=1))
        if std > 0 and len(excess_bh) >= 30:
            # per-trade IR annualized by trades/year (session calendar ~252d)
            years = max((strat_days.max() - strat_days.min()).days / 365.25,
                        1e-9)
            trades_per_year = len(excess_bh) / years
            report.information_ratio = (float(excess_bh.mean()) / std
                                        * np.sqrt(trades_per_year))

    if not random_all.empty:
        rand_ret = random_all["profit_ratio"]
        rand_days = pd.to_datetime(random_all["open_date"]).dt.normalize()
        report.excess_vs_random = float(strat_ret.mean() - rand_ret.mean())
        report.excess_vs_random_ci = _two_sample_diff_ci(
            strat_ret, strat_days, rand_ret, rand_days, block)
        spf = strat_summary.get("profit_factor")
        rpf = report.random_seeds.get("profit_factor")
        if spf and rpf:
            report.relative_profit_factor = float(spf) / float(rpf)
        sdd = strat_summary.get("max_drawdown_pct")
        rdd = report.random_seeds.get("max_drawdown_pct")
        if sdd is not None and rdd is not None:
            report.relative_drawdown = float(sdd) - float(rdd)

    logger.info("benchmarks %s: excess_vs_random=%s excess_vs_bh=%s",
                strategy.name, _r(report.excess_vs_random),
                _r(report.excess_vs_bh))
    return report
