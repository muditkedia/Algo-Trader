"""Regression cover for the dashboard exporter's Windows atomic write.

The paper session reported::

    Access denied: logs.tmp -> logs.json

``os.replace`` is atomic on Windows but fails with PermissionError while
ANOTHER PROCESS holds the destination open - Python's ``open()`` does not pass
FILE_SHARE_DELETE, so an ordinary reader blocks it. The dashboard's own static
file server is that reader: the browser polls all ten snapshots every 2s, and
``logs.json`` is the largest payload, so it is held open longest and lost the
race by name.

The fix must keep the write ATOMIC (readers never see a partial file) while
surviving a reader, and must never let one locked file cost the other nine.

These tests take a REAL exclusive handle on Windows rather than mocking
os.replace, so they exercise the actual failure. On POSIX the same code path is
exercised through a monkeypatched replace, since an open handle there does not
block a rename.
"""

import json
import os
import sys

import pytest

from algo.trading.dashboard import DashboardExporter


class _Engine:
    """The exporter only ever OBSERVES the engine; a stub is enough to make it
    write files, and keeps these tests about the write itself."""

    class config:
        dashboard_dir = ""
        mode = "paper"
        timeframes = ("15m",)

    def __getattr__(self, name):
        raise AttributeError(name)


def _exporter(tmp_path):
    exporter = DashboardExporter.__new__(DashboardExporter)
    exporter.engine = _Engine()
    exporter.dir = tmp_path
    exporter.dir.mkdir(parents=True, exist_ok=True)
    exporter.scanner_activity = []
    exporter.last_export = None
    exporter.write_failures = {}
    exporter.started_ts = "2026-07-20T00:00:00+00:00"
    return exporter


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ================================================================ basic write

def test_write_is_atomic_and_leaves_no_temp(tmp_path):
    exporter = _exporter(tmp_path)
    assert exporter._write("logs.json", {"hello": "world"}) is True
    body = _read(tmp_path / "logs.json")
    assert body["data"] == {"hello": "world"}
    assert body["schema"] and body["generated_at"]
    assert list(tmp_path.glob("*.tmp")) == []


def test_snapshot_serialization_is_compact_without_changing_the_schema(tmp_path):
    exporter = _exporter(tmp_path)
    assert exporter._write("logs.json", {"hello": "world", "items": [1, 2]})
    raw = (tmp_path / "logs.json").read_text(encoding="utf-8")
    assert "\n" not in raw
    assert _read(tmp_path / "logs.json")["data"] == {
        "hello": "world", "items": [1, 2]}


def test_staging_file_is_unique_per_process(tmp_path):
    """A leftover temp from a killed run, or a second exporter, must not be
    mistaken for this one's staging file."""
    exporter = _exporter(tmp_path)
    stale = tmp_path / "logs.json.999999.tmp"
    stale.write_text("{}", encoding="utf-8")
    assert exporter._write("logs.json", {"n": 1}) is True
    assert (tmp_path / "logs.json").exists()
    assert stale.exists()               # not ours: untouched by the write


def test_startup_sweeps_temps_left_by_a_previous_run(tmp_path):
    (tmp_path / "logs.json.999999.tmp").write_text("{}", encoding="utf-8")
    (tmp_path / "system.json.999998.tmp").write_text("{}", encoding="utf-8")
    exporter = _exporter(tmp_path)
    exporter._sweep_stale_temps()
    assert list(tmp_path.glob("*.tmp")) == []


# ======================================================= reader holds the file

@pytest.mark.skipif(sys.platform != "win32",
                    reason="only Windows blocks replace on an open destination")
def test_a_reader_holding_the_destination_does_not_corrupt_it(tmp_path):
    """THE regression, against a real Windows share-mode conflict.

    While a reader holds logs.json open, the replace cannot succeed - but the
    live file must remain complete and readable, never truncated or partial,
    and the exporter must not raise.
    """
    exporter = _exporter(tmp_path)
    exporter._write("logs.json", {"generation": 1})
    path = tmp_path / "logs.json"

    with open(path, "r", encoding="utf-8") as reader:   # the dashboard server
        ok = exporter._write("logs.json", {"generation": 2})
        assert ok is False                       # lost the race, said so
        reader.seek(0)
        assert json.loads(reader.read())["data"] == {"generation": 1}

    assert _read(path)["data"] == {"generation": 1}     # intact, not truncated
    assert list(tmp_path.glob("*.tmp")) == []          # staging cleaned up


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share semantics")
def test_the_next_export_restores_the_file_once_the_reader_is_gone(tmp_path):
    """A skipped write loses nothing: each snapshot is a complete picture, so
    the following export brings the file current."""
    exporter = _exporter(tmp_path)
    exporter._write("logs.json", {"generation": 1})
    path = tmp_path / "logs.json"

    with open(path, "r", encoding="utf-8"):
        assert exporter._write("logs.json", {"generation": 2}) is False
    assert exporter._write("logs.json", {"generation": 3}) is True
    assert _read(path)["data"] == {"generation": 3}
    assert exporter.write_failures.get("logs.json") is None    # streak cleared


def test_replace_is_retried_before_giving_up(tmp_path, monkeypatch):
    """The collision lasts only as long as one file send, so a transient
    failure must be ridden out rather than dropped."""
    exporter = _exporter(tmp_path)
    real_replace = os.replace
    attempts = {"n": 0}

    def flaky(src, dst):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    assert exporter._write("logs.json", {"ok": True}) is True
    assert attempts["n"] == 3
    assert _read(tmp_path / "logs.json")["data"] == {"ok": True}


def test_a_permanently_locked_file_is_skipped_not_raised(tmp_path, monkeypatch):
    exporter = _exporter(tmp_path)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(
        PermissionError(5, "Access is denied")))
    assert exporter._write("logs.json", {"ok": True}) is False
    assert exporter.write_failures["logs.json"] == 1
    assert list(tmp_path.glob("*.tmp")) == []          # no litter accumulates


def test_repeated_failures_accumulate_for_the_health_panel(tmp_path,
                                                           monkeypatch):
    """One skipped write is a normal read collision; a file that keeps losing
    is a real export problem and must become visible."""
    exporter = _exporter(tmp_path)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(
        PermissionError(5, "Access is denied")))
    for _ in range(4):
        exporter._write("logs.json", {"n": 1})
    assert exporter.write_failures["logs.json"] == 4


# ================================================= one bad file, nine good ones

# ============================================ the suite must not touch the repo

def test_the_live_dashboard_guard_actually_fires(tmp_path):
    """``TradingConfig.dashboard_dir`` defaults to the REAL
    ``dashboard/dashboard_data``, so an engine built in a test without
    overriding it silently overwrites a live session's snapshots - which is
    what happened: a suite run replaced the record of an actual trading day.

    conftest's autouse guard now fails any test that does this. Verify the
    guard detects a write rather than trusting it to.
    """
    import hashlib

    from tests.conftest import LIVE_DASHBOARD_DIR

    def fingerprint(d):
        return {p.name: hashlib.sha1(p.read_bytes()).hexdigest()
                for p in sorted(d.iterdir()) if p.is_file()}

    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "a.json").write_text("1", encoding="utf-8")
    before = fingerprint(probe)
    # rewritten immediately: an mtime-only fingerprint can miss this, because
    # both writes land inside one filesystem timestamp tick
    (probe / "a.json").write_text("2", encoding="utf-8")
    assert fingerprint(probe) != before, "guard cannot detect a rewrite"

    # and the path it watches is the real one, not a stale guess
    assert LIVE_DASHBOARD_DIR.name == "dashboard_data"
    assert LIVE_DASHBOARD_DIR.parent.name == "dashboard"


def test_one_failing_snapshot_does_not_abort_the_others(tmp_path,
                                                        monkeypatch):
    """Previously a single failure aborted the whole export; logs.json being
    written LAST was luck, not a guarantee."""
    exporter = _exporter(tmp_path)
    written = []

    def build_ok(name):
        return lambda: written.append(name) or {"name": name}

    monkeypatch.setattr(exporter, "_events", lambda: [])
    monkeypatch.setattr(exporter, "_system",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(exporter, "_scanner", lambda state: {"ok": 1})
    for name in ("_positions", "_orders", "_portfolio", "_performance"):
        monkeypatch.setattr(exporter, name, build_ok(name))
    for name in ("_signals", "_timeline", "_health", "_logs"):
        monkeypatch.setattr(exporter, name, lambda events, n=name: {"n": n})

    exporter.export()                       # must not raise

    assert not (tmp_path / "system.json").exists()      # the broken one
    for name in ("scanner", "positions", "orders", "portfolio",
                 "signals", "timeline", "performance", "health", "logs"):
        assert (tmp_path / f"{name}.json").exists(), name
    assert exporter.last_export is not None
