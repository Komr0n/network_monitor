"""
Backward-compatible exports for legacy ping service imports.
"""
from app.services.probe_service import ProbeResult as PingResult
from app.services.probe_service import check_tcp_port, ping_host, ping_multiple_hosts

__all__ = ["PingResult", "check_tcp_port", "ping_host", "ping_multiple_hosts"]
