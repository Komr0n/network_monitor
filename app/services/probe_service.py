"""
Probe service supporting ICMP, TCP, HTTP, and DNS checks.
"""
from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from time import perf_counter
from typing import Optional

import httpx
from icmplib import async_ping

from app.config import TIMEOUT


@dataclass
class ProbeResult:
    """Normalized result of a provider probe."""

    is_online: bool
    response_time_ms: Optional[float] = None
    method: str = ""
    error: str = ""
    details: str = ""


def _to_error_message(error: Exception) -> str:
    message = str(error).strip()
    return message or error.__class__.__name__


async def check_tcp_port(host: str, port: int, timeout: int = TIMEOUT) -> ProbeResult:
    """Check whether a TCP port accepts a connection."""
    start_time = perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        elapsed = (perf_counter() - start_time) * 1000
        return ProbeResult(
            is_online=True,
            response_time_ms=elapsed,
            method=f"TCP:{port}",
            details=f"TCP port {port} is open",
        )
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as error:
        return ProbeResult(
            is_online=False,
            method=f"TCP:{port}",
            error=_to_error_message(error),
            details=f"TCP port {port} did not respond",
        )


async def check_icmp_ping(host: str, timeout: int = TIMEOUT) -> ProbeResult:
    """Check host reachability with ICMP ping."""
    try:
        result = await async_ping(
            host,
            timeout=timeout,
            count=1,
            privileged=False,
        )
        if result.is_alive:
            return ProbeResult(
                is_online=True,
                response_time_ms=result.max_rtt,
                method="ICMP",
                details="ICMP echo reply received",
            )
        return ProbeResult(
            is_online=False,
            method="ICMP",
            details="ICMP echo request timed out",
        )
    except Exception as error:
        return ProbeResult(
            is_online=False,
            method="ICMP",
            error=_to_error_message(error),
            details="ICMP check failed",
        )


def _build_http_url(host: str, path: Optional[str], port: Optional[int]) -> str:
    normalized_path = (path or "").strip()
    if normalized_path.startswith(("http://", "https://")):
        return normalized_path

    normalized_path = normalized_path or "/"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    scheme = "https" if port == 443 else "http"
    port_suffix = ""
    if port and ((scheme == "http" and port != 80) or (scheme == "https" and port != 443)):
        port_suffix = f":{port}"

    return f"{scheme}://{host}{port_suffix}{normalized_path}"


async def check_http_target(host: str, path: Optional[str], port: Optional[int], timeout: int = TIMEOUT) -> ProbeResult:
    """Check HTTP availability with a GET request."""
    url = _build_http_url(host, path, port)
    start_time = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=False) as client:
            response = await client.get(url)
        elapsed = (perf_counter() - start_time) * 1000
        is_success = 200 <= response.status_code < 400
        return ProbeResult(
            is_online=is_success,
            response_time_ms=elapsed if is_success else None,
            method="HTTP",
            error="" if is_success else f"HTTP {response.status_code}",
            details=f"{response.request.method} {url} -> HTTP {response.status_code}",
        )
    except (asyncio.TimeoutError, httpx.HTTPError) as error:
        return ProbeResult(
            is_online=False,
            method="HTTP",
            error=_to_error_message(error),
            details=f"HTTP request failed for {url}",
        )


async def check_dns_resolution(host: str, expected_value: Optional[str] = None, timeout: int = TIMEOUT) -> ProbeResult:
    """Check DNS resolution of a hostname."""
    normalized_expected = (expected_value or "").strip()
    start_time = perf_counter()
    try:
        loop = asyncio.get_running_loop()
        address_info = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM),
            timeout=timeout,
        )
        resolved_values = sorted({sockaddr[0] for _, _, _, _, sockaddr in address_info if sockaddr and sockaddr[0]})
        elapsed = (perf_counter() - start_time) * 1000

        if normalized_expected and normalized_expected not in resolved_values:
            return ProbeResult(
                is_online=False,
                method="DNS",
                error=f"Expected {normalized_expected}, got {', '.join(resolved_values) or 'nothing'}",
                details=f"Resolved values: {', '.join(resolved_values) or 'none'}",
            )

        return ProbeResult(
            is_online=True,
            response_time_ms=elapsed,
            method="DNS",
            details=f"Resolved values: {', '.join(resolved_values) or 'none'}",
        )
    except (asyncio.TimeoutError, OSError) as error:
        return ProbeResult(
            is_online=False,
            method="DNS",
            error=_to_error_message(error),
            details=f"DNS lookup failed for {host}",
        )


async def ping_host(host: str, timeout: int = TIMEOUT, use_fallback: bool = True) -> ProbeResult:
    """Keep compatibility with the legacy ping-first behavior."""
    icmp_result = await check_icmp_ping(host, timeout)
    if icmp_result.is_online or not use_fallback:
        return icmp_result

    http_port_result = await check_tcp_port(host, 80, timeout)
    if http_port_result.is_online:
        return http_port_result

    https_port_result = await check_tcp_port(host, 443, timeout)
    if https_port_result.is_online:
        return https_port_result

    if https_port_result.error:
        return https_port_result
    if http_port_result.error:
        return http_port_result
    return icmp_result


async def check_target(
    host: str,
    check_type: str = "auto",
    port: Optional[int] = None,
    path: Optional[str] = None,
    dns_expected_value: Optional[str] = None,
    timeout: int = TIMEOUT,
) -> ProbeResult:
    """Dispatch a probe based on the provider check type."""
    normalized_check_type = (check_type or "auto").strip().lower()

    if normalized_check_type == "ping":
        return await ping_host(host, timeout=timeout, use_fallback=False)
    if normalized_check_type == "tcp":
        return await check_tcp_port(host, port or 80, timeout=timeout)
    if normalized_check_type == "http":
        return await check_http_target(host, path=path, port=port, timeout=timeout)
    if normalized_check_type == "dns":
        return await check_dns_resolution(host, expected_value=dns_expected_value, timeout=timeout)

    return await ping_host(host, timeout=timeout, use_fallback=True)


async def ping_multiple_hosts(hosts: list[str], timeout: int = TIMEOUT) -> dict[str, ProbeResult]:
    """Ping multiple hosts concurrently using the legacy auto strategy."""
    tasks = [ping_host(host, timeout=timeout) for host in hosts]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    return {
        host: result if isinstance(result, ProbeResult) else ProbeResult(is_online=False, method="ICMP", error=_to_error_message(result))
        for host, result in zip(hosts, results)
    }
