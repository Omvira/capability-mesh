"""Outbound URL safety helpers for relay and push delivery."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from capability_mesh.core import CapabilityMeshValidationError


def _hostname_is_private(hostname: str) -> bool:
    if hostname in {"localhost", "localhost.localdomain"}:
        return True
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except OSError:
            return False
        addresses = []
        for info in infos:
            address = info[4][0]
            try:
                addresses.append(ipaddress.ip_address(address))
            except ValueError:
                continue
    return any(address.is_loopback or address.is_private or address.is_link_local or address.is_multicast or address.is_unspecified for address in addresses)


def validate_outbound_http_url(url: str, *, allow_private_networks: bool = False) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise CapabilityMeshValidationError("outbound URL must use http or https")
    if not parsed.hostname:
        raise CapabilityMeshValidationError("outbound URL must include a hostname")
    if not allow_private_networks and _hostname_is_private(parsed.hostname):
        raise CapabilityMeshValidationError("outbound URL targets a private network address")
    return url


def private_networks_allowed_for_server(host: str) -> bool:
    if os.environ.get("CAPABILITY_MESH_ALLOW_PRIVATE_OUTBOUND") == "1":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "localhost.localdomain"}
    return address.is_loopback


__all__ = ["private_networks_allowed_for_server", "validate_outbound_http_url"]
