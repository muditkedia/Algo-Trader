"""Evidence database schema (SQLite DDL) - the approved Phase-1 design.

One file holds the whole schema so it is auditable in one place. Design notes:

* SQLite: one machine, one writer, analytics not OLTP, millions of rows at most,
  transactional and pandas-friendly. No speculative server database.
* Market data (candles) is NEVER stored here - it lives in parquet. This table
  set holds only signals, outcomes, trades, aggregates, and reference data,
  which is what keeps the file small enough to keep forever.
* Timestamps are ISO-8601 UTC strings (sortable; SQLite has no datetime type).
* JSON-shaped columns (confidence components, MC gates, calibration bins) are
  TEXT holding a JSON document.
* Append-only in spirit: signals/outcomes/trades are never mutated except the
  labeler filling initially-absent outcome rows. Corrections are new rows.

Bump ``SCHEMA_VERSION`` and add a migration in database.py when the schema
changes; ``PRAGMA user_version`` records the installed version.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

#: Ordered DDL statements. IF NOT EXISTS so initialize() is idempotent.
SCHEMA_STATEMENTS = [
    # ---------------------------------------------------------- reference data
    """
    CREATE TABLE IF NOT EXISTS instruments (
        symbol        TEXT PRIMARY KEY,
        isin          TEXT,
        name          TEXT,
        sector        TEXT,
        industry      TEXT,
        fo_eligible   INTEGER NOT NULL DEFAULT 0,
        listed_from   TEXT,
        delisted_on   TEXT
    )
    """,
    # ----------------------------------------------------------- strategies
    """
    CREATE TABLE IF NOT EXISTS strategies (
        strategy_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        version       TEXT NOT NULL,
        params_hash   TEXT,
        status        TEXT NOT NULL DEFAULT 'draft',
        status_reason TEXT,
        status_at     TEXT,
        created_at    TEXT NOT NULL,
        UNIQUE(name, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_events (
        event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id   INTEGER NOT NULL REFERENCES strategies(strategy_id),
        ts            TEXT NOT NULL,
        from_status   TEXT,
        to_status     TEXT NOT NULL,
        reason        TEXT
    )
    """,
    # ------------------------------------------------------------------- runs
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        kind          TEXT NOT NULL,
        started_at    TEXT NOT NULL,
        finished_at   TEXT,
        git_commit    TEXT,
        config_hash   TEXT,
        data_window   TEXT,
        notes         TEXT
    )
    """,
    # --------------------------------------------------- signals (the heart)
    """
    CREATE TABLE IF NOT EXISTS signals (
        signal_id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ts                    TEXT NOT NULL,
        symbol                TEXT NOT NULL REFERENCES instruments(symbol),
        strategy_id           INTEGER NOT NULL REFERENCES strategies(strategy_id),
        direction             TEXT NOT NULL,
        mode                  TEXT NOT NULL,
        run_id                INTEGER REFERENCES runs(run_id),
        -- market snapshot
        entry_price           REAL,
        spread_pct            REAL,
        atr_pct               REAL,
        relative_volume       REAL,
        traded_value          REAL,
        dist_from_ema         REAL,
        day_return_so_far     REAL,
        gap_open_pct          REAL,
        -- context labels
        trend_regime          TEXT,
        vol_regime            TEXT,
        market_regime         TEXT,
        sector                TEXT,
        sector_return_5d      REAL,
        breadth_pct_above_ema50 REAL,
        -- decision
        confidence_score      REAL,
        confidence_components TEXT,
        expected_reward_pct   REAL,
        expected_risk_pct     REAL,
        expected_holding_min  REAL,
        expected_value_net    REAL,
        -- disposition
        disposition           TEXT NOT NULL,
        disposition_reason    TEXT,
        rank_in_scan          INTEGER
    )
    """,
    # ------------------------------------------------- signal outcomes (1:1)
    """
    CREATE TABLE IF NOT EXISTS signal_outcomes (
        signal_id         INTEGER PRIMARY KEY REFERENCES signals(signal_id),
        labeled_at        TEXT,
        ret_15m           REAL,
        ret_30m           REAL,
        ret_60m           REAL,
        ret_120m          REAL,
        ret_eod           REAL,
        ret_1d            REAL,
        ret_3d            REAL,
        ret_5d            REAL,
        mfe_pct           REAL,
        mae_pct           REAL,
        mfe_time_min      REAL,
        mae_time_min      REAL,
        overnight_gap_pct REAL,
        cost_model_version TEXT,
        est_cost_pct      REAL,
        sim_exit_price    REAL,
        sim_exit_reason   TEXT,
        sim_holding_min   REAL,
        sim_pnl_net       REAL,
        exit_quality      REAL
    )
    """,
    # ------------------------------------------- trades (canonical schema)
    """
    CREATE TABLE IF NOT EXISTS trades (
        trade_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id         INTEGER REFERENCES signals(signal_id),
        strategy_id       INTEGER REFERENCES strategies(strategy_id),
        mode              TEXT NOT NULL,
        symbol            TEXT NOT NULL,
        open_date         TEXT NOT NULL,
        close_date        TEXT NOT NULL,
        profit_ratio      REAL,
        profit_abs        REAL,
        stake_amount      REAL,
        trade_duration    REAL,
        exit_reason       TEXT,
        enter_tag         TEXT,
        stop_distance_pct REAL
    )
    """,
    # --------------------------------------- evaluations (confidence reads)
    """
    CREATE TABLE IF NOT EXISTS evaluations (
        eval_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id       INTEGER NOT NULL REFERENCES strategies(strategy_id),
        as_of             TEXT NOT NULL,
        run_id            INTEGER REFERENCES runs(run_id),
        scope_type        TEXT NOT NULL,
        scope_value       TEXT NOT NULL,
        n_signals         INTEGER,
        n_trades          INTEGER,
        gross_expectancy  REAL,
        net_expectancy    REAL,
        win_rate          REAL,
        profit_factor     REAL,
        avg_mfe_mae       REAL,
        ci_lo             REAL,
        ci_hi             REAL,
        wfe               REAL,
        mc_gates          TEXT,
        verdict           TEXT
    )
    """,
    # ------------------------------------------------- daily regime labels
    """
    CREATE TABLE IF NOT EXISTS regime_daily (
        date          TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        trend_label   TEXT,
        vol_label     TEXT,
        PRIMARY KEY (date, symbol)
    )
    """,
    # ------------------------------------------------------- calibration
    """
    CREATE TABLE IF NOT EXISTS calibration (
        calib_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        scope         TEXT NOT NULL,
        as_of         TEXT NOT NULL,
        method        TEXT,
        bins          TEXT,
        brier_score   REAL,
        valid_from    TEXT
    )
    """,
    # ------------------------------------------------------------- indexes
    "CREATE INDEX IF NOT EXISTS idx_signals_strat_symbol_ts "
    "ON signals(strategy_id, symbol, ts)",
    "CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts)",
    "CREATE INDEX IF NOT EXISTS idx_signals_disposition ON signals(disposition)",
    "CREATE INDEX IF NOT EXISTS idx_signals_mode ON signals(mode)",
    "CREATE INDEX IF NOT EXISTS idx_eval_strat_scope "
    "ON evaluations(strategy_id, scope_type, as_of)",
    "CREATE INDEX IF NOT EXISTS idx_trades_signal ON trades(signal_id)",
    "CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_id)",
    "CREATE INDEX IF NOT EXISTS idx_strategy_events_strat "
    "ON strategy_events(strategy_id)",
]

#: Table names, for introspection/validation.
TABLES = (
    "instruments", "strategies", "strategy_events", "runs", "signals",
    "signal_outcomes", "trades", "evaluations", "regime_daily", "calibration",
)
