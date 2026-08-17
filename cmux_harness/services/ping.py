"""Ping service — delegates to system ping, routed through PPP TUN adapter."""

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
                ping_args.append(args[i])
                i += 1
            else:
                ping_args.append(args[i])
                i += 1

        # Default target if none given
        if len(ping_args) == 1:
            ping_args.append('8.8.8.8')

        try:
            print()
            subprocess.run(ping_args)
        except KeyboardInterrupt:
            print()
        except Exception as e:
            print(f"  [ping] error: {e}")
        return None

    def on_shutdown(self) -> None:
        self._harness.events.unsubscribe(Event.PPP_IPCP_UP, self._on_ppp_up)
        self._harness.events.unsubscribe(Event.PPP_DISCONNECTING, self._on_ppp_down)

    def _on_ppp_up(self, **kwargs):
        self._ppp_ready = True

    def _on_ppp_down(self, **kwargs):
        self._ppp_ready = False