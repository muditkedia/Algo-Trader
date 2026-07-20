"""LAN address discovery for the dashboard server.

Covers the messy real-world cases: VPN/virtual adapters, Ethernet vs Wi-Fi,
several adapters at once, no network, loopback-only and APIPA. Pure functions
over injected address lists - no sockets are opened in these tests.
"""

import pytest

from algo.trading import network
from algo.trading.network import LanAddress, describe, lan_addresses


def _patch(monkeypatch, route, hosts):
    monkeypatch.setattr(network, "route_preferred_ip", lambda: route)
    monkeypatch.setattr(network, "_host_addresses", lambda: list(hosts))


def test_route_preferred_address_wins(monkeypatch):
    """The interface holding the default route is the one a phone can reach."""
    _patch(monkeypatch, "192.168.1.14", ["192.168.1.14", "10.5.0.2"])
    addrs = lan_addresses()
    assert addrs[0].ip == "192.168.1.14" and addrs[0].primary
    assert not addrs[1].primary


def test_virtual_switch_address_is_demoted_not_chosen(monkeypatch):
    """This machine's real layout: a 10.x Wi-Fi address plus a 192.168.240.1
    Hyper-V/WSL switch. A naive 'prefer 192.168.x' rule picks the unreachable
    one - the route probe must win and the .1 address must be flagged."""
    _patch(monkeypatch, "10.1.132.215", ["10.1.132.215", "192.168.240.1"])
    addrs = lan_addresses()
    assert addrs[0].ip == "10.1.132.215" and addrs[0].primary
    virtual = next(a for a in addrs if a.ip == "192.168.240.1")
    assert not virtual.primary and "virtual adapter" in virtual.note


def test_loopback_and_link_local_are_never_offered(monkeypatch):
    _patch(monkeypatch, None, ["127.0.0.1", "169.254.10.3"])
    assert lan_addresses() == []


def test_no_network_returns_nothing(monkeypatch):
    _patch(monkeypatch, None, [])
    assert lan_addresses() == []


def test_public_address_is_not_treated_as_lan(monkeypatch):
    _patch(monkeypatch, None, ["49.36.12.7"])
    assert lan_addresses() == []


def test_falls_back_when_route_probe_fails(monkeypatch):
    """VPN down / odd routing: still offer the best guess, clearly labelled."""
    _patch(monkeypatch, None, ["192.168.0.50", "10.20.30.40"])
    addrs = lan_addresses()
    assert addrs[0].primary and "best guess" in addrs[0].note


def test_fallback_skips_virtual_adapters(monkeypatch):
    _patch(monkeypatch, None, ["172.20.0.1", "192.168.1.77"])
    primary = next(a for a in lan_addresses() if a.primary)
    assert primary.ip == "192.168.1.77"      # not the .1 virtual switch


def test_multiple_adapters_all_listed_once(monkeypatch):
    _patch(monkeypatch, "192.168.1.14",
           ["192.168.1.14", "192.168.1.14", "10.0.0.9", "172.16.4.5"])
    ips = [a.ip for a in lan_addresses()]
    assert ips == ["192.168.1.14", "10.0.0.9", "172.16.4.5"]
    assert sum(a.primary for a in lan_addresses()) == 1


def test_ethernet_only_machine_works(monkeypatch):
    _patch(monkeypatch, "10.0.0.23", ["10.0.0.23"])
    addrs = lan_addresses()
    assert len(addrs) == 1 and addrs[0].primary


# ------------------------------------------------------------- console text

def test_describe_marks_the_phone_address(monkeypatch):
    _patch(monkeypatch, "192.168.1.14", ["192.168.1.14", "192.168.240.1"])
    lines = describe(8787)
    assert any("localhost:8787" in ln for ln in lines)
    phone = [ln for ln in lines if "192.168.1.14" in ln][0]
    assert "open this on your phone" in phone
    assert any("virtual adapter" in ln for ln in lines)


def test_describe_localhost_only_mode(monkeypatch):
    _patch(monkeypatch, "192.168.1.14", ["192.168.1.14"])
    lines = describe(8787, host="127.0.0.1")
    assert any("LAN access disabled" in ln for ln in lines)
    assert not any("192.168.1.14" in ln for ln in lines)


def test_describe_reports_missing_network(monkeypatch):
    _patch(monkeypatch, None, [])
    assert any("no LAN address found" in ln for ln in describe(8787))


def test_url_helper():
    assert LanAddress("192.168.1.14").url(8787) == "http://192.168.1.14:8787"


def test_qr_lines_absent_library_is_not_an_error(monkeypatch):
    """QR is optional: without the library we must degrade silently."""
    monkeypatch.setattr(network, "qr_matrix", lambda url: None)
    assert network.qr_lines("http://192.168.1.14:8787") == []


def test_qr_output_is_ascii_safe():
    """Windows consoles default to cp1252: block glyphs raise
    UnicodeEncodeError at print time, so the QR must be ASCII + ANSI only."""
    lines = network.qr_lines("http://192.168.1.14:8787")
    if not lines:
        pytest.skip("no QR library installed")
    assert all(ch.isascii() for line in lines for ch in line)
    for line in lines:                       # must not raise
        line.encode("cp1252")


def test_qr_matrix_is_square_and_plausible():
    matrix = network.qr_matrix("http://192.168.1.14:8787")
    if matrix is None:
        pytest.skip("no QR library installed")
    assert len(matrix) == len(matrix[0]) >= 21      # smallest QR is 21x21
    assert any(any(row) for row in matrix)          # has dark modules
