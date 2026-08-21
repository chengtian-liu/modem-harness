"""CmuxHarness — central coordinator that owns transport, services, state, and event bus."""

import time
import threading
from typing import Optional, Callable

from .state import SharedState
from .events import Event, EventBus
from .transport.base import TransportInterface
from .transport.serial import SerialTransport
from .transport.cmux import CmuxTransport
from .services.base import ServiceInterface
from .services.at import AtService, AtChannel
from .services.ppp import PppService
from .protocol.cmux import CtrlType


class CmuxHarness:
    """Central coordinator — owns transport, services, shared state, and event bus.

    Lifecycle:
        1. harness = CmuxHarness(mode='cmux', ...)
        2. harness.register(AtService())
        3. harness.register(PppService())
        4. harness.start(port, baudrate)
        5. harness.dispatch(cmd) in a loop
        6. harness.shutdown()
    """

    def __init__(self, mode: str = 'cmux', verbose: bool = False, frame_size: int = 0):
        self._mode = mode
        self._verbose = verbose
        self._frame_size = frame_size
        self._running = False
        self._services: dict[str, ServiceInterface] = {}
        self._service_order: list[str] = []
        self._command_map: dict[str, str] = {}  # command → service_name

        # Core infrastructure
        self.state = SharedState(mode=mode)
        self.events = EventBus()

        # Transport
        if mode == 'serial':
            self.transport: TransportInterface = SerialTransport(verbose=verbose)
        else:
            self.transport: TransportInterface = CmuxTransport(
                frame_size=frame_size, verbose=verbose
            )

        # AT service (special — always present, needed for frame routing)
        self._at_service: Optional[AtService] = None

    # ---- Service registration ----

    def register(self, service: ServiceInterface) -> None:
        """Register a service with the harness."""
        name = service.name
        if name in self._services:
            print(f"  [Harness] service '{name}' already registered, overwriting")
        self._services[name] = service
        self._service_order.append(name)
        for cmd in service.commands:
            if cmd in self._command_map:
                print(f"  [Harness] command '{cmd}' already registered by '{self._command_map[cmd]}', "
                      f"overwriting with '{name}'")
            self._command_map[cmd] = name

    def get_service(self, name: str) -> Optional[ServiceInterface]:
        """Get a registered service by name."""
        return self._services.get(name)

    @property
    def at_service(self) -> Optional[AtService]:
        return self._at_service

    @property
    def services(self) -> dict[str, ServiceInterface]:
        return self._services

    # ---- Lifecycle ----

    def start(self, port: str, baudrate: int) -> bool:
        """Open transport, initialize CMUX if needed, register services, start reader."""
        self.state.update(transport_ready=False)

        mode_label = 'Direct Serial' if self._mode == 'serial' else 'CMUX Multiplex'
        print(f"\n{'='*60}")
        print(f"  {mode_label} Terminal")
        print(f"  Copyright (c) 2026 chengtian.liu")
        print(f"  Author: chengtian.liu")
        print(f"  Serial: {port} @ {baudrate}")
        print(f"{'='*60}")

        try:
            self.transport.open(port, baudrate)
        except Exception as e:
            print(f"  Transport open failed: {e}")
            return False

        # Note: CMUX init is handled inside CmuxTransport.open() via init_cmux()

        self.state.update(transport_ready=True)

        # Start reader
        self.transport.start_reader(self._handle_frame)

        # Register all services
        self._running = True
        for name in self._service_order:
            service = self._services[name]
            try:
                service.on_register(self)
            except Exception as e:
                print(f"  [Harness] error registering service '{name}': {e}")

        # Create AT channels for CMUX mode
        if self._mode == 'cmux' and self._at_service:
            cmux_transport = self.transport
            if hasattr(cmux_transport, 'dlc_available'):
                for dlci in [1, 2]:
                    if cmux_transport.dlc_available.get(dlci):
                        self._at_service.create_channel(dlci)

        # Serial mode: create virtual channel for AT responses
        if self._mode == 'serial' and self._at_service:
            self._at_service.create_channel(0)

        self.events.fire(Event.TRANSPORT_OPENED, mode=self._mode)

        self._print_help()
        return True

    def shutdown(self) -> None:
        """Shutdown all services and close transport."""
        self._running = False

        self.events.fire(Event.TRANSPORT_CLOSED)

        for name in reversed(self._service_order):
            service = self._services[name]
            try:
                service.on_shutdown()
            except Exception as e:
                print(f"  [Harness] error shutting down service '{name}': {e}")

        self.transport.stop_reader()
        self.transport.close()

    @property
    def running(self) -> bool:
        return self._running

    # ---- Command dispatch ----

    def dispatch(self, cmd: str) -> Optional[str]:
        """Dispatch a user command to the appropriate service."""
        if not cmd or not cmd.strip():
            return None

        parts = cmd.strip().split()
        first = parts[0].lower()

        # Special commands
        if first == 'quit':
            return 'quit'

        # PPP mode: route all non-quit commands to services
        if first == 'ppp':
            # ppp, ppp stop, ppp <dlci>, ppp --apn <apn>
            ppp = self._services.get('ppp')
            if ppp:
                ppp.on_command(parts)
            return None

        # CMUX: DLCI prefix routing (e.g., "1>ATI")
        if '>' in cmd and self._mode == 'cmux':
            self._handle_dlci_command(cmd)
            return None

        # AT commands (auto-detect)
        if cmd.upper().startswith('AT'):
            self._handle_at_command(cmd)
            return None

        # Route to registered service
        if first in self._command_map:
            service_name = self._command_map[first]
            service = self._services.get(service_name)
            if service:
                try:
                    return service.on_command(parts)
                except Exception as e:
                    print(f"  [{service_name}] error: {e}")
                    return None
            else:
                print(f"  Service '{service_name}' not found")
                return None

        # Unknown command
        self._print_help()
        return None

    def _handle_dlci_command(self, cmd: str):
        """Handle DLCI-prefixed AT commands like '1>ATI'."""
        try:
            dlci_str, at_cmd = cmd.split('>', 1)
            dlci = int(dlci_str)
            self._send_at(dlci, at_cmd)
            if self._at_service:
                resp = self._at_service.read_response(dlci)
                if resp:
                    print(f"  {resp.strip()}")
        except (ValueError, KeyError):
            print(f"  format error, use: DLCI>AT_COMMAND, e.g. 1>ATI")

    def _handle_at_command(self, cmd: str):
        """Handle AT commands in both serial and CMUX modes."""
        if cmd.lower().startswith('at ') and len(cmd) > 3:
            at_cmd = cmd[3:].strip()
        else:
            at_cmd = cmd.strip()

        if self._mode == 'serial':
            self._send_at(0, at_cmd)
        else:
            self._send_at(1, at_cmd)
            if self._at_service:
                resp = self._at_service.read_response(1)
                if resp:
                    print(f"  {resp.strip()}")

    def _send_at(self, dlci: int, cmd: str):
        if self._mode == 'serial':
            if self._at_service:
                self._at_service.clear_buffer(0)
                self._at_service.send(0, cmd)
                # wait for response via event (no fixed sleep)
                resp = self._at_service.read_response(0, timeout=5.0)
                if resp:
                    print(f"  {resp.strip()}")
        elif self._at_service:
            self._at_service.send(dlci, cmd)

    # ---- Frame routing ----

    def _handle_frame(self, dlci: int, data: bytes):
        """Route incoming frame data to the correct service."""
        if not self._running:
            return

        ppp_dlci = self.state.ppp_dlci
        ppp_running = self.state.ppp_running

        # Serial mode: all data comes as dlci=0
        if self._mode == 'serial':
            if ppp_running:
                ppp = self._services.get('ppp')
                if ppp and data:
                    ppp.feed(data)
            elif self._at_service and data:
                self._at_service.feed_response(0, data)
            return

        # CMUX mode: route by DLCI
        if dlci == 0:
            # Control channel — log and ignore
            if self._verbose and data:
                ctrl_type = data[0] & 0xEF if data else 0
                type_names = {
                    CtrlType.CLD: 'CLD', CtrlType.TEST: 'TEST',
                    CtrlType.FCON: 'FCON', CtrlType.FCOFF: 'FCOFF',
                    CtrlType.MSC: 'MSC', CtrlType.NSC: 'NSC',
                }
                name = type_names.get(ctrl_type, f'0x{ctrl_type:02X}')
                print(f"  [DLCI 0] {name} {data.hex(' ')}")
        elif dlci == ppp_dlci and ppp_running:
            # PPP data
            ppp = self._services.get('ppp')
            if ppp and data:
                ppp.feed(data)
        elif self._at_service and dlci in self._at_service.channels:
            # AT response
            if data:
                self._at_service.feed_response(dlci, data)

    # ---- Help ----

    def _print_help(self):
        sep = "─" * 60
        if self._mode == 'serial':
            print(f"  [Serial Mode] — Direct Serial")
            print(f"  {sep}")
            print(f"    ppp                             Start PPP dialup")
            print(f"    ppp stop                        Stop PPP")
            print(f"    ping [args]                     System ping (e.g. ping -l 1024 8.8.8.8)")
            print(f"    ftp upload <local> <remote>     Upload file via FTP")
            print(f"    ftp download <remote> <loc>     Download file via FTP")
            print(f"    ftp config                      View/modify FTP settings")
            print(f"    iperf3 <ip> [port] [duration]   Run iperf3 speed test")
            print(f"    AT+<cmd>                        Send AT command (e.g. AT+CSQ)")
            print(f"    quit                            Quit")
        else:
            print(f"  [CMUX Mode]")
            print(f"  {sep}")
            print(f"    1>ATI                           Send AT command on DLCI 1")
            print(f"    2>AT+CSQ                        Send AT command on DLCI 2")
            print(f"    ppp [dlci]                      Start PPP dialup (default: DLCI 2)")
            print(f"    ppp stop                        Stop PPP")
            print(f"    ping [args]                     System ping (e.g. ping -l 1024 8.8.8.8)")
            print(f"    ftp upload <local> <remote>     Upload file via FTP")
            print(f"    ftp download <remote> <loc>     Download file via FTP")
            print(f"    ftp config                      View/modify FTP settings")
            print(f"    iperf3 <ip> [port] [duration]   Run iperf3 speed test")
            print(f"    quit                            Quit")
        print(f"  {sep}")