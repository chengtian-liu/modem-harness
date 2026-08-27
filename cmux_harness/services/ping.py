"""Ping service — delegates to system ping, routed through PPP TUN adapter."""

import socket
import subprocess
from typing import Optional, TYPE_CHECKING

from .base import ServiceInterface
from ..events import Event

if TYPE_CHECKING:
    from ..harness import CmuxHarness


class PingService(ServiceInterface):
    """Ping service — uses system ping, which routes through the PPP TUN adapter."""

    name = 'ping'
    commands = ['ping']

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._harness: Optional['CmuxHarness'] = None
        self._ppp_ready = False

    def on_register(self, harness: 'CmuxHarness') -> None:
        self._harness = harness
        harness.events.subscribe(Event.PPP_IPCP_UP, self._on_ppp_up)
        harness.events.subscribe(Event.PPP_DISCONNECTING, self._on_ppp_down)

    def on_command(self, args: list[str]) -> Optional[str]:
        if not self._ppp_ready:
            print("  PPP not connected")
            return None

        # Build system ping args, passing through user flags
        ping_args = ['ping']
        target = None
        i = 1
        while i < len(args):
            if args[i] == '-t':
                # Continuous ping — pass through
                ping_args.append('-t')
                i += 1
            elif args[i] == '-n' and i + 1 < len(args):
                ping_args.extend(['-n', args[i + 1]])
                i += 2
            elif args[i] == '-l' and i + 1 < len(args):
                ping_args.extend(['-l', args[i + 1]])
                i += 2
            elif args[i] == '-w' and i + 1 < len(args):
                ping_args.extend(['-w', args[i + 1]])
                i += 2
            elif not args[i].startswith('-'):
                target = args[i]
                ping_args.append(args[i])
                i += 1
            else:
                ping_args.append(args[i])
                i += 1

        # Default target if none given
        if target is None:
            target = '8.8.8.8'
            ping_args.append(target)

        # Resolve hostname to IP for route injection
        target_ip = self._resolve(target)
        if target_ip is None:
            print(f"  [ping] cannot resolve: {target}")
            return None

        # Add route through TUN adapter
        ppp_service = self._harness.get_service('ppp')
        if ppp_service:
            ppp_service.add_route(target_ip)

        try:
            print()
            subprocess.run(ping_args)
        except KeyboardInterrupt:
            print()
        except Exception as e:
            print(f"  [ping] error: {e}")
        finally:
            if ppp_service:
                ppp_service.del_route(target_ip)
        return None

    def on_shutdown(self) -> None:
        self._harness.events.unsubscribe(Event.PPP_IPCP_UP, self._on_ppp_up)
        self._harness.events.unsubscribe(Event.PPP_DISCONNECTING, self._on_ppp_down)

    def _on_ppp_up(self, **kwargs):
        self._ppp_ready = True

    def _on_ppp_down(self, **kwargs):
        self._ppp_ready = False

    @staticmethod
    def _resolve(host: str) -> Optional[str]:
        """Resolve hostname to IPv4 address. Returns None on failure."""
        try:
            # If already an IP, this returns it as-is
            socket.inet_aton(host)
            return host
        except OSError:
            pass
        try:
            return socket.gethostbyname(host)
        except socket.gaierror:
            return None