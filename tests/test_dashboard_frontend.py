"""Static contracts for the read-only dashboard surface."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"


def _html():
    return (DASHBOARD / "index.html").read_text(encoding="utf-8")


def test_dashboard_has_unique_operational_ids_and_required_panels():
    html = _html()
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "duplicate DOM ids break live patching"
    for panel in (
        "m-watchlist", "m-registered", "m-strategies", "positions",
        "t-signals", "t-rejected", "timeline", "t-closed", "t-orders",
        "t-logs", "h-components", "md-detail", "notif-toggle", "logsearch",
    ):
        assert f'id="{panel}"' in html


def test_dashboard_keeps_the_lightweight_static_contract():
    html = _html()
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    css = (DASHBOARD / "styles.css").read_text(encoding="utf-8")
    assert 'src="app.js?v=' in html
    assert 'href="styles.css?v=' in html
    assert "dashboard_data" in app
    assert "document.addEventListener(\"visibilitychange\"" in app
    assert "@media(max-width:760px)" in css
    assert not re.search(r"(?:react|vue|angular|bootstrap|tailwind)",
                         html + app + css, re.IGNORECASE)


def test_dashboard_declares_all_exported_snapshot_names():
    app = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    for name in (
        "live", "system", "scanner", "marketdata", "positions", "orders",
        "portfolio", "signals", "timeline", "performance", "health", "logs",
    ):
        assert f'"{name}"' in app
