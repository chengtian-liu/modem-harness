"""Shared state between all services — thread-safe via lock."""

from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class SharedState:
    """Thread-safe shared state accessible by all services."""

    # Transport
    mode: str = 'cmux'             # 'cmux' or 'serial'
    transport_ready: bool = False

    # PPP
    ppp_dlci: int = 2
    ppp_running: bool = False
    ppp_lcp_up: bool = False
    ppp_ipcp_up: bool = False
    ppp_local_ip: Optional[str] = None
    ppp_remote_ip: Optional[str] = None
    ppp_dns: list[str] = field(default_factory=list)
    ppp_mru: int = 1500

    # FTP config (must be configured before use)
    ftp_host: str = ''
    ftp_port: int = 21
    ftp_user: str = ''
    ftp_password: str = ''

    _lock: Lock = field(default_factory=Lock, repr=False)

    def update(self, **kwargs):
        """Thread-safe batch update."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def get(self, key: str, default=None):
        """Thread-safe read."""
        with self._lock:
            return getattr(self, key, default)