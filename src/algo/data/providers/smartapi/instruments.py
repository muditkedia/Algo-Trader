"""SmartApiInstruments - the official Angel One instrument master.

SmartAPI publishes one JSON scrip master for all exchanges (documented URL,
configurable via SMARTAPI_INSTRUMENTS_URL). Records look like:

    {"token": "3045", "symbol": "SBIN-EQ", "name": "SBIN",
     "expiry": "", "strike": "-1.0", "lotsize": "1",
     "instrumenttype": "", "exch_seg": "NSE", "tick_size": "5.0"}

This class downloads it (stdlib urllib; downloader injectable for tests),
filters to NSE cash equities (``exch_seg == "NSE"`` and the ``-EQ`` series),
normalizes to a small canonical frame, caches to parquet, and provides the
symbol -> (token, tradingsymbol) lookups the candle/quote APIs need. Symbols
are exposed WITHOUT the ``-EQ`` suffix (RELIANCE, not RELIANCE-EQ) so the rest
of the platform stays broker-agnostic; the suffixed tradingsymbol is kept for
API calls. No auth is required for the master - it works credential-free.

THIS CLASS IS THE ONE AUTHORITATIVE symbol -> token map (D-038). Every
component that needs a token - the data provider, the live broker adapter, the
universe builder, the downloader - resolves through here and nothing keeps a
private copy.

SELF-LOADING (D-038). Lookups load the master on first use: cached parquet
first, network only if there is no cache. Callers therefore CANNOT forget to
prime it, which is what defect D-038 was: ``ensure()`` was the caller's job and
five entry points each remembered it separately. ``scripts/run_trading.py`` -
the production paper/live runner - did not, and in paper mode the one in-engine
``ensure()`` (AngelOneBroker.connect) never runs because paper uses PaperBroker.
Every ``token_for`` then returned None from an unloaded master, indistinguishable
from "symbol not listed", so all 99 watchlist symbols logged "no instrument
token - skipping" while the cache sat on disk unread. The index path already
resolved lazily; equities now do the same.

A lookup MISS and an UNAVAILABLE MASTER are different failures and are reported
differently: ``resolve``/``resolve_many`` say which, so an operator never has to
guess whether a symbol is delisted or the download failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from algo.core.logging import get_logger

logger = get_logger("data.smartapi.instruments")

COLUMNS = ("symbol", "token", "tradingsymbol", "name", "exchange", "lotsize")
CACHE_FILE = "smartapi_nse_eq.parquet"
INDEX_CACHE_FILE = "smartapi_nse_idx.parquet"

#: The NSE cash series this provider trades. It is also the ONLY symbol alias
#: in the system: ``tradingsymbol_for`` hands back "RELIANCE-EQ", so that form
#: must resolve back to "RELIANCE" when it is fed in again.
SERIES_SUFFIX = "-EQ"

#: Canonical platform symbol -> the scrip-master ``symbol`` field of the NSE
#: index record (``instrumenttype == "AMXIDX"``). These are the reserved
#: market-context symbols (DATA_INFRASTRUCTURE_PLAN.md section 3): they are
#: resolvable through ``token_for`` like any equity but are NEVER returned by
#: ``symbols()``, so universe building and scanning cannot pick them up.
#: Tokens are always discovered from the master, never hardcoded.
INDEX_SYMBOLS = {
    "NIFTY50": "Nifty 50",
    "BANKNIFTY": "Nifty Bank",
    "INDIAVIX": "India VIX",
}


def normalize_symbol(symbol) -> str:
    """The canonical platform form of a symbol.

    Case and surrounding whitespace are normalized, and the ``-EQ`` series
    suffix is stripped so the broker's tradingsymbol round-trips. Other NSE
    series (``-BE``, ``-SM``) are deliberately NOT stripped: they are different
    scrips with different tokens, and folding them onto the EQ symbol would
    resolve a symbol to an instrument the operator did not ask for.
    """
    text = str(symbol or "").strip().upper()
    if text.endswith(SERIES_SUFFIX):
        text = text[:-len(SERIES_SUFFIX)].strip()
    return text


@dataclass
class MappingReport:
    """The outcome of resolving a set of symbols against the master.

    Exists so the operator gets ONE statement per cycle ("97/99 resolved")
    instead of one log line per symbol, and so "the master never loaded" is
    never silently reported as "these symbols do not exist".
    """

    requested: List[str] = field(default_factory=list)
    resolved: Dict[str, str] = field(default_factory=dict)   # symbol -> token
    unresolved: List[str] = field(default_factory=list)
    master_loaded: bool = True
    master_rows: int = 0
    error: str = ""

    @property
    def total(self) -> int:
        return len(self.requested)

    @property
    def resolved_count(self) -> int:
        return len(self.resolved)

    @property
    def unresolved_count(self) -> int:
        return len(self.unresolved)

    @property
    def ok(self) -> bool:
        return self.master_loaded and not self.unresolved

    def examples(self, limit: int = 5) -> List[str]:
        return self.unresolved[:limit]

    def summary_line(self, limit: int = 5) -> str:
        """One operator-readable line - the whole point of this class."""
        if not self.master_loaded:
            return (f"instrument master UNAVAILABLE - 0/{self.total} symbols "
                    f"resolvable ({self.error or 'reason unknown'})")
        if not self.unresolved:
            return f"instrument mapping OK - {self.resolved_count}/{self.total} resolved"
        shown = ", ".join(self.examples(limit))
        more = (f", +{self.unresolved_count - limit} more"
                if self.unresolved_count > limit else "")
        return (f"instrument mapping failures - {self.unresolved_count}/"
                f"{self.total} symbols unresolved (examples: {shown}{more})")

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "resolved": self.resolved_count,
            "unresolved": self.unresolved_count,
            "unresolved_symbols": list(self.unresolved),
            "examples": self.examples(),
            "master_loaded": self.master_loaded,
            "master_rows": self.master_rows,
            "error": self.error,
            "summary": self.summary_line(),
        }


def _default_downloader(url: str) -> str:  # pragma: no cover - network
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8")


class SmartApiInstruments:
    def __init__(self, instruments_url: str, cache_dir=None,
                 downloader: Optional[Callable] = None,
                 evidence_logger=None) -> None:
        self.url = instruments_url
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.downloader = downloader or _default_downloader
        self.evidence_logger = evidence_logger
        self._master: Optional[pd.DataFrame] = None
        self._index_master: Optional[pd.DataFrame] = None
        self._records_cache: Optional[list] = None
        #: normalized symbol -> {token, tradingsymbol, ...}. Rebuilt whenever
        #: the master changes; lookups hit this dict, never a frame scan (the
        #: watchlist is resolved on every cycle).
        self._by_symbol: Dict[str, dict] = {}
        #: why the last load attempt failed, and whether to stop retrying. A
        #: failed download must not be re-attempted once per symbol per cycle.
        self._load_error: str = ""
        self._load_attempted: bool = False

    # ------------------------------------------------------------------ fetch

    def _records(self) -> list:
        """The parsed scrip master, downloaded at most once per process."""
        if self._records_cache is None:
            self._records_cache = json.loads(self.downloader(self.url))
        return self._records_cache

    def _reindex(self, frame: pd.DataFrame) -> None:
        """Rebuild the O(1) lookup index from a master frame."""
        index: Dict[str, dict] = {}
        for row in frame.to_dict("records"):
            index[normalize_symbol(row.get("symbol"))] = row
        self._by_symbol = index

    def fetch(self, exchange: str = "NSE", series_suffix: str = SERIES_SUFFIX
              ) -> pd.DataFrame:
        """Download + normalize the master, filtered to cash equities."""
        records = self._records()
        frame = pd.DataFrame(records)
        frame = frame[(frame["exch_seg"] == exchange)
                      & frame["symbol"].astype(str).str.endswith(series_suffix)]
        out = pd.DataFrame({
            "symbol": frame["symbol"].astype(str)
            .str.removesuffix(series_suffix).str.strip().str.upper(),
            "token": frame["token"].astype(str).str.strip(),
            "tradingsymbol": frame["symbol"].astype(str).str.strip(),
            "name": frame.get("name", frame["symbol"]).astype(str),
            "exchange": exchange,
            "lotsize": pd.to_numeric(frame.get("lotsize", 1),
                                     errors="coerce").fillna(1).astype(int),
        }).drop_duplicates(subset=["symbol"]).reset_index(drop=True)
        if out.empty:
            raise ValueError(
                f"instrument master yielded no {exchange}{series_suffix} rows "
                "- source format may have changed")
        self._master = out
        self._reindex(out)
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            out.to_parquet(self.cache_dir / CACHE_FILE, index=False)
        logger.info("SmartAPI instruments loaded: %d %s equities",
                    len(out), exchange)
        return out

    def load_cached(self) -> pd.DataFrame:
        """Use a previously cached master (offline / restart path)."""
        if self.cache_dir is None:
            raise RuntimeError("no cache_dir configured")
        frame = pd.read_parquet(self.cache_dir / CACHE_FILE)
        # normalize defensively: a cache written by an older build may hold
        # un-uppercased symbols, and a lookup miss caused by that would be
        # indistinguishable from a delisting.
        if "symbol" in frame.columns:
            frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
        self._master = frame
        self._reindex(frame)
        logger.info("SmartAPI instruments loaded from cache: %d equities",
                    len(frame))
        return self._master

    def ensure(self) -> pd.DataFrame:
        """Cached master if present, else fetch."""
        if self._master is not None:
            return self._master
        if self.cache_dir is not None \
                and (self.cache_dir / CACHE_FILE).exists():
            return self.load_cached()
        return self.fetch()

    def _ensure_loaded(self) -> bool:
        """Load the master for a LOOKUP: cache-first, network only if needed.

        Never raises - a lookup asks a question and gets an answer. The failure
        is remembered so 99 symbols x every cycle cannot become 99 downloads,
        and it is logged once at ERROR (an unavailable master means no market
        data at all, which is not a warning).
        """
        if self._master is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True
        try:
            self.ensure()
            self._load_error = ""
            return True
        except Exception as exc:
            self._load_error = str(exc)
            logger.error("instrument master could not be loaded (%s) - NO "
                         "symbol can be resolved to a token until this is "
                         "fixed; market data will not update", exc)
            return False

    def reset(self) -> None:
        """Forget the loaded master so the next lookup reloads it (a new
        trading day, or an operator retrying after fixing connectivity)."""
        self._master = None
        self._index_master = None
        self._records_cache = None
        self._by_symbol = {}
        self._load_error = ""
        self._load_attempted = False

    # ------------------------------------------------------------- indices

    def fetch_indices(self, exchange: str = "NSE") -> pd.DataFrame:
        """The canonical market-context indices (NIFTY50/BANKNIFTY/INDIAVIX)
        from the master's AMXIDX records. An index the provider does not list
        is skipped WITH A WARNING, never an error - availability is detected,
        not assumed."""
        frame = pd.DataFrame(self._records())
        idx = frame[(frame["exch_seg"] == exchange)
                    & (frame.get("instrumenttype", "") == "AMXIDX")]
        names = idx["symbol"].astype(str).str.strip()
        rows = []
        for canonical, master_symbol in INDEX_SYMBOLS.items():
            match = idx[names.str.lower() == master_symbol.lower()]
            if match.empty:
                logger.warning("index %r (%s) not present in the instrument "
                               "master - unavailable from this provider",
                               canonical, master_symbol)
                continue
            row = match.iloc[0]
            rows.append({
                "symbol": canonical, "token": str(row["token"]).strip(),
                "tradingsymbol": str(row["symbol"]).strip(),
                "name": str(row.get("name", master_symbol)),
                "exchange": exchange, "lotsize": 1,
            })
        out = pd.DataFrame(rows, columns=list(COLUMNS))
        self._index_master = out
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            out.to_parquet(self.cache_dir / INDEX_CACHE_FILE, index=False)
        logger.info("SmartAPI index instruments resolved: %s",
                    sorted(out["symbol"]) if len(out) else "NONE")
        return out

    def ensure_indices(self) -> pd.DataFrame:
        """Cached index master if present, else fetch (network only then)."""
        if self._index_master is not None:
            return self._index_master
        if self.cache_dir is not None \
                and (self.cache_dir / INDEX_CACHE_FILE).exists():
            self._index_master = pd.read_parquet(
                self.cache_dir / INDEX_CACHE_FILE)
            return self._index_master
        return self.fetch_indices()

    # ------------------------------------------------------------- accessors

    def _lookup(self, symbol: str) -> Optional[dict]:
        """Resolve one symbol, loading the master on first use.

        Equities and the reserved index symbols both resolve here - there is no
        other lookup path in the system.
        """
        key = normalize_symbol(symbol)
        if not key:
            return None
        if self._ensure_loaded():
            row = self._by_symbol.get(key)
            if row is not None:
                return row
        # market-context indices: reserved canonical names resolve through the
        # index master (loaded lazily; cache-first, one warning on failure)
        if key in INDEX_SYMBOLS:
            if self._index_master is None:
                try:
                    self.ensure_indices()
                except Exception as exc:   # unavailable != broken pipeline
                    logger.warning("index master unavailable: %s", exc)
                    return None
            match = self._index_master[self._index_master["symbol"] == key]
            return None if match.empty else match.iloc[0].to_dict()
        return None

    def token_for(self, symbol: str) -> Optional[str]:
        row = self._lookup(symbol)
        return None if row is None else str(row["token"])

    def tradingsymbol_for(self, symbol: str) -> Optional[str]:
        row = self._lookup(symbol)
        return None if row is None else str(row["tradingsymbol"])

    def symbols(self) -> List[str]:
        """Every tradeable NSE cash symbol in the master (loads it if needed)."""
        if not self._ensure_loaded():
            return []
        return sorted(self._master["symbol"])

    # ------------------------------------------------------------- reporting

    def resolve_many(self, symbols) -> MappingReport:
        """Resolve a whole watchlist in one pass and report it as ONE result.

        This is what the operator sees ("97/99 resolved"), what preflight
        checks at startup, and what the dashboard displays - all from the same
        call, so those three can never disagree.
        """
        requested = [normalize_symbol(s) for s in symbols]
        requested = [s for s in requested if s]
        loaded = self._ensure_loaded()
        report = MappingReport(
            requested=requested, master_loaded=loaded,
            master_rows=0 if self._master is None else len(self._master),
            error=self._load_error)
        if not loaded:
            report.unresolved = list(requested)
            return report
        for symbol in requested:
            token = self.token_for(symbol)
            if token is None:
                report.unresolved.append(symbol)
            else:
                report.resolved[symbol] = token
        return report

    def sync_to_evidence(self) -> int:
        """Upsert reference rows into the evidence instruments table (reuse)."""
        if self.evidence_logger is None or self._master is None:
            return 0
        for row in self._master.itertuples(index=False):
            self.evidence_logger.upsert_instrument(row.symbol, name=row.name)
        return len(self._master)
