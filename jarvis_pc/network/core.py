"""Network reachability for the homelab: can this PC reach the machines JARVIS depends on? Read-only (TCP connects).

Tailscale and WSL commands are T2 under the gate (`tailscale` is unrecognised, `wsl` is a nested shell), so they are
deliberately not probed here; a person approves those.
"""
from __future__ import annotations

from typing import Callable, Iterable, List, Tuple

from ..diagnostics.findings import Finding
from ..verify.checks import CheckResult, port_check


def check_targets(targets: Iterable[Tuple[str, str, int]], check: Callable[..., CheckResult] = port_check,
                  timeout: float = 3.0) -> List[Finding]:
    """targets: (friendly name, host, port). One finding each; a target that does not answer is a warning."""
    out: List[Finding] = []
    for name, host, port in targets:
        r = check(host, int(port), timeout=timeout)
        out.append(Finding("ok", "network", f"{name} reachable", f"{host}:{port}") if r.passed else
                   Finding("warning", "network", f"{name} not reachable", f"{host}:{port}: {r.detail}",
                           hint="check that the machine is on, the service is running, and Tailscale/the LAN is connected"))
    return out
