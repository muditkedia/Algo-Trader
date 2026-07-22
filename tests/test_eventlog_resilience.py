"""Crash-boundary checks for the append-only event journal."""

import json

import pytest

from algo.trading.eventlog import EventLog


def test_read_day_ignores_only_an_incomplete_final_record(tmp_path):
    log = EventLog(tmp_path)
    path = log._path("20260723")
    path.write_text(
        json.dumps({"type": "health", "n": 1}) + "\n"
        + '{"type":"position","n":2',
        encoding="utf-8")

    assert log.read_day("20260723") == [{"type": "health", "n": 1}]


def test_read_day_still_raises_for_interior_corruption(tmp_path):
    log = EventLog(tmp_path)
    path = log._path("20260723")
    path.write_text(
        '{"type":"health","n":1}\n'
        + '{not-json}\n'
        + json.dumps({"type": "health", "n": 3}) + "\n",
        encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        log.read_day("20260723")
