"""What is behind the network panel's throughput numbers.

Throughput says *how much*; this module answers the questions that follow it on
a shared machine: which services are listening and to whom (only this host, or
every interface), which interfaces exist and which one carries the traffic, and
whether TCP itself is healthy (retransmissions are the classic sign of a bad
link or an overloaded peer, and are invisible in a MB/s plot).

The pieces here are pure -- parsers and classifiers fed with plain data -- so
they are unit-tested without a network. ``SystemMetrics.get_network_details``
does the collecting and keeps the counters the rates are computed from.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Dict, Iterable, List, Optional

# Socket enumeration walks every process's fd table, so it is refreshed at most
# this often; interface and TCP counters are cheap and follow every tick.
SOCKET_REFRESH_SECONDS = 5.0

# Ports whose owner a reader would want named even when the process is not
# visible (another user's, or one psutil cannot attribute without root). These
# are *hints* -- any program can bind any port -- so they are reported as
# ``service_hint`` and the process name, when known, wins in the display.
KNOWN_PORTS: Dict[int, str] = {
    22: "ssh",
    53: "dns",
    80: "http",
    111: "rpcbind",
    443: "https",
    631: "ipp",
    2049: "nfs",
    3000: "grafana/dev server",
    3306: "mysql",
    4040: "spark ui",
    5000: "flask/mlflow",
    5432: "postgres",
    5900: "vnc",
    6006: "tensorboard",
    6379: "redis/ray",
    6817: "slurmctld",
    6818: "slurmd",
    6819: "slurmdbd",
    7860: "gradio",
    8000: "http (fastapi/vllm)",
    8080: "http-alt",
    8265: "ray dashboard",
    8443: "https-alt",
    8501: "streamlit",
    8786: "dask scheduler",
    8787: "dask dashboard/rstudio",
    9090: "prometheus",
    9100: "node exporter",
    11434: "ollama",
    27017: "mongodb",
    29500: "torch.distributed",
}
# Jupyter walks upward from 8888 when the port is taken.
JUPYTER_PORTS = range(8888, 8900)

# Interface name prefixes that are virtual plumbing rather than a link to the
# network. Used only when sysfs cannot answer (no /sys/class/net/<if>/device).
VIRTUAL_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "tailscale", "tun",
                    "tap", "wg", "vxlan", "cni", "flannel", "cali", "zt")

EXPOSURE_ALL = "all interfaces"
EXPOSURE_LOOPBACK = "localhost only"


def service_hint(port: int, proto: str = "tcp") -> Optional[str]:
    """A likely service name for ``port``, or None."""
    if port in KNOWN_PORTS:
        return KNOWN_PORTS[port]
    if port in JUPYTER_PORTS:
        return "jupyter"
    try:
        return socket.getservbyport(int(port), "udp" if proto.startswith("udp") else "tcp")
    except (OSError, ValueError, OverflowError):
        return None


def exposure_of(address: str, iface_by_ip: Optional[Dict[str, str]] = None) -> str:
    """Who can reach a socket bound to ``address``.

    The wildcard addresses listen on every interface -- reachable from the
    network unless a firewall says otherwise, which is the case worth
    flagging. Loopback is this host only. Anything else is one interface,
    named when known.
    """
    text = (address or "").split("%", 1)[0]
    if text in ("", "0.0.0.0", "::", "*"):
        return EXPOSURE_ALL
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return text
    if ip.is_loopback:
        return EXPOSURE_LOOPBACK
    # An IPv4-mapped IPv6 wildcard/loopback behaves like its IPv4 form.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return exposure_of(str(mapped), iface_by_ip)
    name = (iface_by_ip or {}).get(text)
    return f"{name} only" if name else f"{text} only"


def is_exposed(exposure: str) -> bool:
    """True unless the socket is reachable from this host alone."""
    return exposure != EXPOSURE_LOOPBACK


def interface_kind(name: str, sysfs_root: str = "/sys/class/net") -> str:
    """``"infiniband"``, ``"physical"`` or ``"virtual"`` for an interface.

    sysfs is authoritative where present: a ``device`` link means real
    hardware, and ARP type 32 is InfiniBand (the HPC fabric, which a plain
    MB/s total lumps together with ethernet).
    """
    base = os.path.join(sysfs_root, name)
    try:
        with open(os.path.join(base, "type")) as handle:
            if handle.read().strip() == "32":
                return "infiniband"
    except OSError:
        pass
    if os.path.exists(os.path.join(base, "device")):
        return "physical"
    if os.path.isdir(base) or name.startswith(VIRTUAL_PREFIXES):
        return "virtual"
    return "physical"


_KIND_ORDER = {"infiniband": 0, "physical": 1, "virtual": 2}


def sort_interfaces(interfaces: Iterable[Dict]) -> List[Dict]:
    """Links that carry traffic first: up before down, fabric before plumbing."""
    return sorted(
        interfaces,
        key=lambda i: (not i.get("is_up"), _KIND_ORDER.get(i.get("kind"), 3),
                       -(i.get("rx_mbps") or 0) - (i.get("tx_mbps") or 0),
                       i.get("name") or ""),
    )


def parse_proc_net_snmp(text: str) -> Dict[str, Dict[str, int]]:
    """``/proc/net/snmp`` as ``{"Tcp": {"RetransSegs": n, ...}, ...}``.

    The file pairs a header line with a value line per protocol; a pair that
    does not line up is skipped rather than guessed at.
    """
    result: Dict[str, Dict[str, int]] = {}
    lines = [line.split() for line in (text or "").splitlines() if line.strip()]
    for header, values in zip(lines, lines[1:]):
        if not header or not values or header[0] != values[0]:
            continue
        if not header[0].endswith(":") or header[0] in result:
            continue
        fields = {}
        for key, value in zip(header[1:], values[1:]):
            try:
                fields[key] = int(value)
            except ValueError:
                continue
        result[header[0].rstrip(":")] = fields
    return result


def tcp_health(previous: Optional[Dict[str, int]], current: Optional[Dict[str, int]],
               seconds: float) -> Dict[str, Optional[float]]:
    """Rates from two ``Tcp`` readings of ``/proc/net/snmp``.

    ``retransmit_percent`` -- retransmitted over sent segments in the interval
    -- is the figure to watch: above a percent or two, transfers stall in a
    way no throughput plot explains. None where there is no earlier reading.
    """
    health: Dict[str, Optional[float]] = {
        "established": None,
        "retransmit_percent": None,
        "retransmits_per_s": None,
        "segments_out_per_s": None,
        "in_errors_per_s": None,
        "resets_out_per_s": None,
    }
    if current:
        health["established"] = current.get("CurrEstab")
    if not previous or not current or seconds <= 0:
        return health

    def rate(key):
        if key not in current or key not in previous:
            return None
        return max(current[key] - previous[key], 0) / seconds

    out, retrans = rate("OutSegs"), rate("RetransSegs")
    health["segments_out_per_s"] = None if out is None else round(out, 1)
    health["retransmits_per_s"] = None if retrans is None else round(retrans, 2)
    if out and retrans is not None:
        health["retransmit_percent"] = round(min(retrans / out * 100.0, 100.0), 2)
    elif out == 0 and retrans is not None:
        health["retransmit_percent"] = 0.0
    errors = rate("InErrs")
    health["in_errors_per_s"] = None if errors is None else round(errors, 2)
    resets = rate("OutRsts")
    health["resets_out_per_s"] = None if resets is None else round(resets, 2)
    return health


def merge_listeners(rows: Iterable[Dict]) -> List[Dict]:
    """Collapse sockets bound to the same (proto, address, port).

    A forking server (gunicorn, a dataloader, sshd) holds one listening socket
    per worker; listing each would bury every other service. Pids are kept,
    in order, so nothing is hidden -- just grouped.
    """
    merged: Dict[tuple, Dict] = {}
    for row in rows:
        key = (row.get("proto"), row.get("address"), row.get("port"))
        existing = merged.get(key)
        if existing is None:
            entry = dict(row)
            entry["pids"] = [row["pid"]] if row.get("pid") is not None else []
            merged[key] = entry
            continue
        pid = row.get("pid")
        if pid is not None and pid not in existing["pids"]:
            existing["pids"].append(pid)
        for field in ("process", "username"):
            if not existing.get(field) and row.get(field):
                existing[field] = row[field]
        existing["mine"] = bool(existing.get("mine") or row.get("mine"))
    for entry in merged.values():
        entry["pid"] = entry["pids"][0] if entry["pids"] else None
    return sorted(merged.values(),
                  key=lambda r: (not r.get("mine"), not is_exposed(r.get("exposure", "")),
                                 r.get("port") or 0, r.get("proto") or ""))


def summarize_listeners(listening: Iterable[Dict]) -> Dict[str, int]:
    """Counts for a one-line summary: total, exposed, and the user's own."""
    listening = list(listening)
    return {
        "listening": len(listening),
        "exposed": sum(1 for r in listening if is_exposed(r.get("exposure", ""))),
        "mine": sum(1 for r in listening if r.get("mine")),
    }
