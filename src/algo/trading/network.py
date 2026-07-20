"""LAN address discovery for the read-only dashboard server.

One job: work out which address a phone on the same Wi-Fi should open, on a
laptop that may simultaneously have Wi-Fi, Ethernet, a VPN, WSL/Hyper-V/Docker
virtual switches, and loopback.

Ranking (highest first), all standard library:

1. **The route-preferred address.** Opening a UDP socket toward a public IP and
   reading back the local endpoint reveals the interface holding the default
   route. No packet is sent and no internet connection is required - it is a
   routing-table lookup - so this works on an isolated Wi-Fi network.
2. Other private IPv4 addresses (RFC1918) reported for this host.

Then two demotions, both learned from real machines:

* Addresses whose last octet is ``.1`` and which are NOT the route-preferred
  address are almost always the host side of a virtual switch (Hyper-V, WSL,
  VirtualBox, Docker). A phone cannot reach those.
* Loopback (127/8) and link-local/APIPA (169.254/16) are never offered as LAN
  addresses - the first is this machine only, the second means "no DHCP".

Nothing here touches trading; it only produces strings to print.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import List, Optional

from algo.core.logging import get_logger

logger = get_logger("trading.network")

#: probed to find the default route; no traffic is actually sent
_ROUTE_PROBE = ("8.8.8.8", 80)


@dataclass(frozen=True)
class LanAddress:
    ip: str
    primary: bool = False
    note: str = ""

    def url(self, port: int) -> str:
        return f"http://{self.ip}:{port}"


def _is_usable(ip: str) -> bool:
    """Private IPv4, not loopback, not link-local."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return (addr.is_private and not addr.is_loopback
            and not addr.is_link_local and not addr.is_multicast
            and not addr.is_unspecified)


def route_preferred_ip() -> Optional[str]:
    """The local address on the interface holding the default route, or None.

    Sends nothing: ``connect`` on a UDP socket only fixes the local endpoint.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.3)
        sock.connect(_ROUTE_PROBE)
        ip = sock.getsockname()[0]
        return ip if _is_usable(ip) else None
    except OSError:
        return None                      # no network at all
    finally:
        sock.close()


def _host_addresses() -> List[str]:
    """Every IPv4 address this host reports for itself."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def lan_addresses() -> List[LanAddress]:
    """Usable LAN addresses, best candidate first (may be empty)."""
    route_ip = route_preferred_ip()
    candidates = []
    if route_ip:
        candidates.append(route_ip)
    for ip in _host_addresses():
        if ip not in candidates:
            candidates.append(ip)

    out: List[LanAddress] = []
    for ip in candidates:
        if not _is_usable(ip):
            continue
        if ip == route_ip:
            out.append(LanAddress(ip, primary=True,
                                  note="most likely - this machine's active "
                                       "network connection"))
        elif ip.endswith(".1"):
            # host side of a virtual switch (WSL / Hyper-V / Docker / VBox)
            out.append(LanAddress(ip, note="probably a virtual adapter "
                                           "(WSL/Hyper-V/Docker) - phones "
                                           "cannot reach this"))
        else:
            out.append(LanAddress(ip, note="alternative adapter"))

    # if the route probe failed, promote the first plain address instead
    if out and not any(a.primary for a in out):
        plain = next((i for i, a in enumerate(out)
                      if "virtual" not in a.note), 0)
        best = out[plain]
        out[plain] = LanAddress(best.ip, primary=True,
                                note="best guess - no default route detected")
    return out


def describe(port: int, host: str = "0.0.0.0") -> List[str]:
    """Console lines describing where the dashboard can be opened."""
    lines = [f"http://localhost:{port}".ljust(34) + "(this laptop)"]
    if host in ("127.0.0.1", "localhost"):
        lines.append("LAN access disabled (bound to localhost only)")
        return lines
    addresses = lan_addresses()
    if not addresses:
        lines.append("no LAN address found - is Wi-Fi/Ethernet connected?")
        return lines
    for addr in addresses:
        marker = "  <-- open this on your phone" if addr.primary else ""
        lines.append(addr.url(port).ljust(34) + f"({addr.note}){marker}")
    return lines


#: ANSI background colours. The QR is drawn as coloured SPACES rather than
#: block glyphs: Windows consoles default to cp1252 and cannot encode block
#: characters (printing them raises UnicodeEncodeError), while spaces encode
#: everywhere. Colouring the background also guarantees a true dark-on-light
#: code regardless of the terminal's theme, which is what scanners expect.
_QR_DARK = "\033[40m  \033[0m"      # black cell
_QR_LIGHT = "\033[107m  \033[0m"    # bright white cell


def qr_matrix(url: str) -> Optional[List[List[bool]]]:
    """QR module matrix for ``url``, or None if no QR library is installed.

    Optional by design: ``qrcode`` / ``segno`` are used when present and
    skipped otherwise, so nothing new is required just to run the system.
    """
    try:
        import qrcode                                   # type: ignore
        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.make(fit=True)
        return [[bool(c) for c in row] for row in qr.get_matrix()]
    except ImportError:
        pass
    except Exception as exc:                            # pragma: no cover
        logger.debug("qrcode render failed: %s", exc)
        return None
    try:
        import segno                                    # type: ignore
        code = segno.make(url)
        return [[bool(c) for c in row]
                for row in code.matrix_iter(border=2)]
    except ImportError:
        return None
    except Exception as exc:                            # pragma: no cover
        logger.debug("segno render failed: %s", exc)
        return None


def qr_lines(url: str) -> List[str]:
    """Printable terminal QR for ``url`` ([] when no QR library is present).

    ASCII-safe: every cell is two spaces with an ANSI background colour, so
    this prints correctly on a cp1252 Windows console.
    """
    matrix = qr_matrix(url)
    if not matrix:
        return []
    return ["".join(_QR_DARK if cell else _QR_LIGHT for cell in row)
            for row in matrix]
