"""Owner-only Wake-on-LAN via the ALWAYS-ON laptop on the PC's local LAN.

Does not contact the sleeping/offline PC over Tailscale and cannot guarantee
startup: firmware/NIC/power/network settings determine whether packets wake it.
No paid APIs, code execution, discovery scan or arbitrary address arguments.
"""
from __future__ import annotations
import ipaddress
import os
import re
import socket
import threading
import time

_MAC=re.compile(r"^(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$|^[0-9a-fA-F]{12}$")
_SEND_GUARD=threading.Lock()
_last_sent=0.0

class WakeError(ValueError):
    pass

def mac_bytes(text:str)->bytes:
    if not isinstance(text,str) or not _MAC.fullmatch(text.strip()):
        raise WakeError("Set JARVIS_PC_WAKE_MAC to the PC Ethernet adapter MAC (12 hex digits).")
    raw=bytes.fromhex(text.replace(":","").replace("-",""))
    if raw==b"\x00"*6 or raw==b"\xff"*6 or raw[0]&1:
        raise WakeError("Use the PC's physical unicast Ethernet MAC, not a multicast or broadcast MAC.")
    return raw

def destination(text:str)->str:
    """Only allow an explicit LAN broadcast; never target arbitrary hosts."""
    try:
        addr=ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError:
        raise WakeError("Set JARVIS_PC_WAKE_BROADCAST to your LAN broadcast address.") from None
    # 255.255.255.255 and RFC1918-directed *.*.*.255 broadcast.
    if str(addr)=="255.255.255.255":
        return str(addr)
    ranges=(ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"))
    if not (any(addr in network for network in ranges) and addr.packed[-1]==255):
        raise WakeError("Use a private LAN broadcast (such as 192.168.1.255), not a host address.")
    return str(addr)

def configured()->bool:
    """Capability check only; no packets sent and no secret data returned."""
    try:
        mac_bytes(os.getenv("JARVIS_PC_WAKE_MAC",""))
        destination(os.getenv("JARVIS_PC_WAKE_BROADCAST",""))
        return True
    except WakeError:
        return False

def wake(*,send=None)->str:
    """Send only to the owner's preconfigured NIC. No PC contact required."""
    from core import pcbridge
    pcbridge.require_owner()
    if pcbridge.local_pc():
        return "This Windows PC is already on; a wake packet isn't necessary."
    if not configured():
        return ("Wake-on-LAN is not configured on the always-on laptop. Set "
                "JARVIS_PC_WAKE_MAC (PC's wired Ethernet MAC) and "
                "JARVIS_PC_WAKE_BROADCAST (local LAN broadcast). Enable Wake-on-LAN "
                "in the PC BIOS and adapter, then test with the PC sleeping first.")
    global _last_sent
    with _SEND_GUARD:
        if time.monotonic()-_last_sent<5.0:
            return "A wake packet was just sent. Wait a few seconds before trying again."
        packet=b"\xff"*6+mac_bytes(os.environ["JARVIS_PC_WAKE_MAC"])*16
        target=destination(os.environ["JARVIS_PC_WAKE_BROADCAST"])
        if send is None:
            def send(data,address):
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
                    sock.settimeout(2)
                    return sock.sendto(data,address)
        try:
            count=send(packet,(target,9))
        except OSError as exc:
            return ("Could not send the LAN wake packet ("+type(exc).__name__+
                    "). Confirm that the always-on laptop is connected to the same LAN.")
        if count!=len(packet):
            return "The Wake-on-LAN packet did not finish sending; PC startup is unconfirmed."
        _last_sent=time.monotonic()
    return ("Wake-on-LAN packet sent from the always-on laptop. This does NOT confirm "
            "that the PC powered on. Give it time to boot, then ask 'PC status'.")
