"""PPP dialup service — LCP/IPCP/IPv6CP negotiation, TUN forwarding, disconnect."""

import os
import time
import struct
import ctypes
from ctypes import c_void_p
import threading
import subprocess
from typing import Optional, TYPE_CHECKING

from .base import ServiceInterface
from ..events import Event
from ..protocol.ppp import (
    PPP_FLAG, PPP_ADDR, PPP_CTRL, PPP_IP, PPP_LCP, PPP_IPCP, PPP_PAP, PPP_IPV6CP,
    LCP_MRU, LCP_ACCM, LCP_MAGIC, LCP_PROTOCOMP, LCP_ACFC,
    LCP_CONF_REQ, LCP_CONF_ACK, LCP_CONF_NAK, LCP_CONF_REJ,
    LCP_TERM_REQ, LCP_TERM_ACK, LCP_CODE_REJ, LCP_ECHO_REQ, LCP_ECHO_REP,
    IPCP_CONF_REQ, IPCP_CONF_ACK, IPCP_CONF_NAK, IPCP_CONF_REJ,
    IPCP_IPADDR, IPCP_PRIDNS, IPCP_SECDNS,
    PAP_REQ, PAP_ACK, PAP_NAK,
    IPV6CP_IFACE_ID,
    make_ppp_frame, PppParser,
)
from ..protocol.cmux import FrameType
from ..tun import TunAdapter, _kernel32, WAIT_OBJECT_0, WAIT_TIMEOUT

if TYPE_CHECKING:
    from ..harness import CmuxHarness


class PppService(ServiceInterface):
    """PPP dialup service — manages full PPP lifecycle."""

    name = 'ppp'
    commands = ['ppp', 'ppp stop']

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._harness: Optional['CmuxHarness'] = None
        self._running = False
        self._parser = PppParser(verbose=verbose)
        self._lcp_up = False
        self._ipcp_up = False
        self._auth_up = True
        self._local_ip = None
        self._remote_ip = None
        self._dns = []
        self._id_counter = 1
        self._magic = 0x12345678
        self._mru = 1500
        self._acfc_enabled = False
        self._pfc_enabled = False
        self._peer_wants_pfc = False
        self._peer_wants_acfc = False
        self._lcp_rejected_options: set[int] = set()
        self._ipcp_rejected_options: set[int] = set()

        # TUN
        self._tun: Optional[TunAdapter] = None
        self._tun_thread: Optional[threading.Thread] = None
        self._current_ftp_route = ''

        # Threads
        self._ppp_thread: Optional[threading.Thread] = None
        self._engine_thread: Optional[threading.Thread] = None

    # ---- ServiceInterface ----

    def on_register(self, harness: 'CmuxHarness') -> None:
        self._harness = harness

    def on_command(self, args: list[str]) -> Optional[str]:
        subcmd = args[1] if len(args) > 1 else 'start'
        if subcmd == 'stop':
            self.stop()
            self._harness.state.update(ppp_running=False, ppp_ipcp_up=False,
                                       ppp_lcp_up=False, ppp_local_ip=None)
            return None

        # Start PPP
        dlci = 2
        apn = ''
        # Parse arguments
        i = 1
        while i < len(args):
            if args[i] == '--apn' and i + 1 < len(args):
                apn = args[i + 1]
                i += 2
            elif args[i].isdigit():
                dlci = int(args[i])
                i += 1
            else:
                i += 1

        # Prompt for hardware flow control
        self._hw_flow = False
        try:
            ans = input('  Enable hardware flow control (RTS/CTS)? (requires RTS/CTS wired, USB modems OK) [y/N]: ').strip().lower()
            if ans in ('y', 'yes'):
                sp = self._harness.transport.serial_port
                if sp:
                    sp.rtscts = True
                    self._hw_flow = True
                    print('  RTS/CTS flow control enabled')
        except (EOFError, KeyboardInterrupt):
            pass

        self.start(dlci, apn)
        return None

    def on_shutdown(self) -> None:
        self.stop(fast=True)

    # ---- Public API ----

    def start(self, dlci: int = 2, apn: str = '', user: str = '', password: str = '') -> None:
        if self._running:
            print(f"  PPP already running, please execute ppp stop first")
            return

        state = self._harness.state
        transport = self._harness.transport

        if state.mode == 'cmux' and dlci not in self._harness.at_service.channels:
            print(f"  DLCI {dlci} unavailable")
            return

        state.update(ppp_dlci=dlci)
        mode_label = 'Serial' if state.mode == 'serial' else f'DLCI {dlci}'
        print(f"\n  [PPP] on {mode_label} starting dialup...")

        # Mark as running before dialing (wait_serial_connect checks this flag)
        self._running = True
        self._harness.state.update(ppp_running=True)

        # Set dialing lock
        transport.dialing = True

        # Send dial command
        dial_cmd = 'ATD*99#'
        if apn:
            dial_cmd = 'ATD*99***1#'

        if state.mode == 'serial':
            print(f"  [PPP] → {dial_cmd}")
            transport.send((dial_cmd + '\r').encode())
            connected = self._wait_serial_connect(timeout=15)
        else:
            at_ch = self._harness.at_service
            at_ch.clear_buffer(dlci)
            # Drain parser
            while transport.cmux.parser.get_frame():
                pass
            time.sleep(0.5)
            print(f"  [PPP] → {dial_cmd}")
            transport.send((dial_cmd + '\r').encode(), dlci)
            connected = self._wait_cmux_connect(dlci, timeout=15)

        transport.dialing = False

        if not connected:
            print(f"  [PPP] ✗ CONNECT not received")
            self._running = False
            self._harness.state.update(ppp_running=False)
            return

        print(f"  [PPP] ✓ CONNECT received")
        time.sleep(0.3)

        self._harness.events.fire(Event.PPP_STARTING)

        self._ppp_thread = threading.Thread(
            target=self._run, args=(apn, user, password),
            daemon=True
        )
        self._ppp_thread.start()

    def stop(self, fast=False) -> None:
        if self._running:
            print(f"\n  [PPP] stopping{' (fast)' if fast else ''}...")
            self._harness.transport.dialing = True
            self._disconnect(fast=fast)
            self._running = False
            if self._ppp_thread:
                self._ppp_thread.join(timeout=5)
            self._harness.transport.dialing = False

            # Restore hardware flow control
            if self._hw_flow:
                try:
                    sp = self._harness.transport.serial_port
                    if sp:
                        sp.rtscts = False
                        self._hw_flow = False
                except Exception:
                    pass

            print(f"  [PPP] stopped")
        else:
            print(f"  PPP not running")

    def feed(self, data: bytes) -> None:
        """Feed PPP data from the transport reader."""
        self._parser.feed(data)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def local_ip(self) -> Optional[str]:
        return self._local_ip

    @property
    def tun(self) -> Optional[TunAdapter]:
        return self._tun

    # ---- PPP Engine ----

    def _run(self, apn: str = '', user: str = '', password: str = ''):
        self._running = True
        self._harness.state.update(ppp_running=True)

        self._engine_thread = threading.Thread(target=self._process_ppp, daemon=True)
        self._engine_thread.start()

        print(f"\n  [PPP] --- LCP negotiation ---")
        self._send_lcp_conf_req()
        lcp_timeout = time.time() + 10
        while not self._lcp_up and self._running and time.time() < lcp_timeout:
            time.sleep(0.1)

        if not self._lcp_up:
            print(f"  [PPP] ✗ LCP negotiation timeout")
            self._running = False
            self._harness.state.update(ppp_running=False)
            return

        self._harness.state.update(ppp_lcp_up=True)
        self._harness.events.fire(Event.PPP_LCP_UP)

        if not self._auth_up:
            print(f"\n  [PPP] --- PAP authentication ---")
            user_bytes = user.encode() if user else b''
            pwd_bytes = password.encode() if password else b''
            info = bytes([PAP_REQ, self._next_id(), len(user_bytes)]) + user_bytes + \
                   bytes([len(pwd_bytes)]) + pwd_bytes
            self._send_ppp(PPP_PAP, info)
            auth_timeout = time.time() + 5
            while not self._auth_up and self._running and time.time() < auth_timeout:
                time.sleep(0.1)
            if not self._auth_up:
                print(f"  [PPP] auth timeout (continuing...)")
                self._auth_up = True

        print(f"\n  [PPP] --- IPv6CP + IPCP negotiation ---")
        self._send_ipv6cp_conf_req()
        time.sleep(0.05)
        self._send_ipcp_conf_req()
        ipcp_timeout = time.time() + 15
        while not self._ipcp_up and self._running and time.time() < ipcp_timeout:
            time.sleep(0.1)

        if not self._ipcp_up:
            print(f"  [PPP] ✗ IPCP negotiation timeout")
            self._running = False
            self._harness.state.update(ppp_running=False)
            return

        self._harness.state.update(
            ppp_ipcp_up=True,
            ppp_local_ip=self._local_ip,
            ppp_remote_ip=self._remote_ip,
            ppp_dns=list(self._dns),
        )

        print(f"\n  [PPP] ✓ connected!")
        print(f"  [PPP] Local IP:  {self._local_ip or 'unknown'}")
        print(f"  [PPP] Remote IP: {self._remote_ip or 'unknown'}")
        if self._dns:
            print(f"  [PPP] DNS:       {', '.join(self._dns)}")

        self._harness.events.fire(Event.PPP_IPCP_UP,
                                   local_ip=self._local_ip,
                                   remote_ip=self._remote_ip,
                                   dns=list(self._dns))

        # Create TUN adapter
        if self._local_ip:
            self._start_tun()

        while self._running:
            time.sleep(0.5)

    def _send_ppp(self, protocol: int, info: bytes):
        frame = make_ppp_frame(protocol, info,
                               acfc=self._acfc_enabled,
                               pfc=self._pfc_enabled)
        if self._verbose and protocol != PPP_IP:
            print(f"  [PPP TX] {frame.hex(' ')} ({len(frame)}B)")

        transport = self._harness.transport
        state = self._harness.state

        if state.mode == 'serial':
            transport.serial_port.write(frame)
            return

        frame_max = transport.frame_size if transport.frame_size > 0 else 127
        total = len(frame)
        offset = 0
        while offset < total:
            chunk = frame[offset:offset + frame_max]
            transport.send(chunk, state.ppp_dlci)
            offset += len(chunk)
            if self._verbose and offset < total:
                print(f"  [PPP]   fragment sent: {offset}/{total}")

    def _next_id(self) -> int:
        i = self._id_counter
        self._id_counter = (self._id_counter + 1) % 256
        if self._id_counter == 0:
            self._id_counter = 1
        return i

    # ---- LCP ----

    def _send_lcp_conf_req(self):
        options = bytearray()
        options.extend([LCP_MRU, 4, (self._mru >> 8) & 0xFF, self._mru & 0xFF])
        options.extend([LCP_ACCM, 6, 0, 0, 0, 0])
        magic_bytes = struct.pack('>I', self._magic)
        options.extend([LCP_MAGIC, 6])
        options.extend(magic_bytes)
        if LCP_PROTOCOMP not in self._lcp_rejected_options:
            options.extend([LCP_PROTOCOMP, 2])
        if LCP_ACFC not in self._lcp_rejected_options:
            options.extend([LCP_ACFC, 2])
        info = bytes([LCP_CONF_REQ, self._next_id(), 0, 0]) + options
        info = info[:2] + struct.pack('>H', len(info)) + info[4:]
        self._send_ppp(PPP_LCP, info)
        opts_str = f"MRU={self._mru}, ACCM=0x00000000, Magic=0x{self._magic:08X}"
        if LCP_PROTOCOMP not in self._lcp_rejected_options:
            opts_str += ", PFC"
        if LCP_ACFC not in self._lcp_rejected_options:
            opts_str += ", ACFC"
        print(f"  [PPP] LCP → Configure-Request ({opts_str})")

    def _send_lcp_conf_ack(self, req_id: int, options: bytes):
        info = bytes([LCP_CONF_ACK, req_id, 0, 0]) + options
        info = info[:2] + struct.pack('>H', len(info)) + info[4:]
        self._send_ppp(PPP_LCP, info)

    def _send_lcp_term_req(self):
        info = bytes([LCP_TERM_REQ, self._next_id(), 0, 4])
        self._send_ppp(PPP_LCP, info)
        print(f"  [PPP] LCP → Terminate-Request")

    # ---- IPCP ----

    def _send_ipcp_conf_req(self):
        if self._local_ip:
            ip_bytes = bytes(int(b) for b in self._local_ip.split('.'))
        else:
            ip_bytes = bytes([0, 0, 0, 0])
        options = bytearray()
        if 2 not in self._ipcp_rejected_options:
            options.extend([0x02, 6, 0x00, 0x2D, 0x0F, 0x01])
        options.extend([IPCP_IPADDR, 6])
        options.extend(ip_bytes)
        dns1 = bytes(int(b) for b in self._dns[0].split('.')) if len(self._dns) > 0 else bytes([0, 0, 0, 0])
        dns2 = bytes(int(b) for b in self._dns[1].split('.')) if len(self._dns) > 1 else bytes([0, 0, 0, 0])
        if 0x81 not in self._ipcp_rejected_options:
            options.extend([0x81, 6])
            options.extend(dns1)
        if 0x82 not in self._ipcp_rejected_options:
            options.extend([0x82, 6, 0, 0, 0, 0])
        if 0x83 not in self._ipcp_rejected_options:
            options.extend([0x83, 6])
            options.extend(dns2)
        if 0x84 not in self._ipcp_rejected_options:
            options.extend([0x84, 6, 0, 0, 0, 0])
        info = bytes([IPCP_CONF_REQ, self._next_id(), 0, 0]) + options
        info = info[:2] + struct.pack('>H', len(info)) + info[4:]
        self._send_ppp(PPP_IPCP, info)
        ip_str = self._local_ip or '0.0.0.0'
        dns1_str = '.'.join(str(b) for b in dns1)
        dns2_str = '.'.join(str(b) for b in dns2)
        print(f"  [PPP] IPCP → Configure-Request (IP={ip_str}, DNS={dns1_str},{dns2_str})")

    def _send_ipcp_conf_ack(self, req_id: int, options: bytes):
        info = bytes([IPCP_CONF_ACK, req_id, 0, 0]) + options
        info = info[:2] + struct.pack('>H', len(info)) + info[4:]
        self._send_ppp(PPP_IPCP, info)

    # ---- IPv6CP ----

    def _send_ipv6cp_conf_req(self):
        iface_id = os.urandom(8)
        options = bytes([IPV6CP_IFACE_ID, 10]) + iface_id
        info = bytes([IPCP_CONF_REQ, self._next_id(), 0, 0]) + options
        info = info[:2] + struct.pack('>H', len(info)) + info[4:]
        self._send_ppp(PPP_IPV6CP, info)
        print(f"  [PPP] IPv6CP → Configure-Request")

    def _send_ipv6cp_conf_ack(self, req_id: int, options: bytes):
        info = bytes([IPCP_CONF_ACK, req_id, 0, 0]) + options
        info = info[:2] + struct.pack('>H', len(info)) + info[4:]
        self._send_ppp(PPP_IPV6CP, info)
        print(f"  [PPP] IPv6CP → Configure-ACK (id={req_id})")

    # ---- Protocol Handlers ----

    def _process_ppp(self):
        while self._running:
            frame = self._parser.get_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            proto = frame['protocol']
            info = frame['info']

            if proto == PPP_LCP:
                self._handle_lcp(info)
            elif proto == PPP_IPCP:
                self._handle_ipcp(info)
            elif proto == PPP_IPV6CP:
                self._handle_ipv6cp(info)
            elif proto == PPP_PAP:
                if len(info) >= 2 and info[0] == PAP_ACK:
                    print(f"  [PPP] PAP ← Authenticate-ACK ✓")
                    self._auth_up = True
                elif len(info) >= 2 and info[0] == PAP_NAK:
                    print(f"  [PPP] PAP ← Authenticate-NAK (continue)")
                    self._auth_up = True
            elif proto == PPP_IP:
                if len(info) >= 20:
                    # Forward to TUN
                    if self._tun and self._tun._running:
                        try:
                            self._tun.write(info)
                            if self._verbose:
                                src = '.'.join(str(b) for b in info[12:16])
                                dst = '.'.join(str(b) for b in info[16:20])
                                print(f"  [PPP→TUN] {src} → {dst} ({len(info)}B)")
                        except Exception as e:
                            print(f"  [PPP→TUN] error: {e}")

    def _handle_lcp(self, info: bytes):
        if len(info) < 4:
            return
        code = info[0]
        ident = info[1]
        length = (info[2] << 8) | info[3]
        data = info[4:length]

        if code == LCP_CONF_REQ:
            print(f"  [PPP] LCP ← Configure-Request (id={ident})")
            pos = 0
            while pos < len(data):
                if pos + 1 >= len(data):
                    break
                opt_type = data[pos]
                opt_len = data[pos + 1] if pos + 1 < len(data) else 0
                if opt_len < 2 or pos + opt_len > len(data):
                    break
                if opt_type == LCP_ACFC:
                    self._peer_wants_acfc = True
                elif opt_type == LCP_PROTOCOMP:
                    self._peer_wants_pfc = True
                pos += opt_len
            self._send_lcp_conf_ack(ident, data)
        elif code == LCP_CONF_ACK:
            print(f"  [PPP] LCP ← Configure-ACK (id={ident})")
            self._pfc_enabled = self._peer_wants_pfc
            self._acfc_enabled = self._peer_wants_acfc
            self._parser.set_peer_compression(
                acfc=self._peer_wants_acfc, pfc=self._peer_wants_pfc)
            self._lcp_up = True
            print(f"  [PPP] LCP established ✓")
        elif code == LCP_CONF_NAK:
            print(f"  [PPP] LCP ← Configure-NAK (id={ident})")
            pos = 0
            while pos < len(data):
                if pos + 1 >= len(data):
                    break
                opt_type = data[pos]
                opt_len = data[pos + 1]
                if opt_len < 2 or pos + opt_len > len(data):
                    break
                if opt_type == LCP_MRU and opt_len >= 4:
                    self._mru = (data[pos + 2] << 8) | data[pos + 3]
                pos += opt_len
            self._send_lcp_conf_req()
        elif code == LCP_CONF_REJ:
            print(f"  [PPP] LCP ← Configure-Reject (id={ident})")
            pos = 0
            while pos < len(data):
                if pos + 1 >= len(data):
                    break
                opt_type = data[pos]
                opt_len = data[pos + 1]
                if opt_len < 2 or pos + opt_len > len(data):
                    break
                self._lcp_rejected_options.add(opt_type)
                pos += opt_len
            self._send_lcp_conf_req()
        elif code == LCP_ECHO_REQ:
            if len(data) >= 4:
                magic_bytes = data[:4]
                echo_data = data[4:]
                info = bytes([LCP_ECHO_REP, ident, 0, 0]) + magic_bytes + echo_data
                info = info[:2] + struct.pack('>H', len(info)) + info[4:]
                self._send_ppp(PPP_LCP, info)
        elif code == LCP_TERM_REQ:
            print(f"  [PPP] LCP ← Terminate-Request → Terminate-ACK")
            info = bytes([LCP_TERM_ACK, ident, 0, 4])
            self._send_ppp(PPP_LCP, info)
            self._lcp_up = False
        elif code == LCP_TERM_ACK:
            print(f"  [PPP] LCP ← Terminate-ACK")
            self._lcp_up = False
        elif code == 8:  # Protocol-Reject
            pass

    def _handle_ipcp(self, info: bytes):
        if len(info) < 4:
            return
        code = info[0]
        ident = info[1]
        length = (info[2] << 8) | info[3]
        data = info[4:length]

        if code == IPCP_CONF_REQ:
            print(f"  [PPP] IPCP ← Configure-Request (id={ident})")
            pos = 0
            while pos < len(data):
                if pos + 1 >= len(data):
                    break
                opt_type = data[pos]
                opt_len = data[pos + 1]
                if opt_len < 2 or pos + opt_len > len(data):
                    break
                if opt_type == IPCP_IPADDR and opt_len >= 6:
                    ip = '.'.join(str(b) for b in data[pos + 2:pos + 6])
                    if ip != '0.0.0.0':
                        self._remote_ip = ip
                pos += opt_len
            self._send_ipcp_conf_ack(ident, data)
        elif code == IPCP_CONF_ACK:
            print(f"  [PPP] IPCP ← Configure-ACK (id={ident})")
            self._ipcp_up = True
        elif code == IPCP_CONF_NAK:
            print(f"  [PPP] IPCP ← Configure-NAK (id={ident})")
            pos = 0
            while pos < len(data):
                if pos + 1 >= len(data):
                    break
                opt_type = data[pos]
                opt_len = data[pos + 1]
                if opt_len < 2 or pos + opt_len > len(data):
                    break
                if opt_type == IPCP_IPADDR:
                    self._local_ip = '.'.join(str(b) for b in data[pos + 2:pos + 6])
                elif opt_type == 0x81:
                    dns = '.'.join(str(b) for b in data[pos + 2:pos + 6])
                    if dns != '0.0.0.0' and dns not in self._dns:
                        self._dns.append(dns)
                elif opt_type == 0x83:
                    dns = '.'.join(str(b) for b in data[pos + 2:pos + 6])
                    if dns != '0.0.0.0' and dns not in self._dns:
                        self._dns.append(dns)
                pos += opt_len
            self._send_ipcp_conf_req()
        elif code == IPCP_CONF_REJ:
            print(f"  [PPP] IPCP ← Configure-Reject (id={ident})")
            pos = 0
            while pos < len(data):
                if pos + 1 >= len(data):
                    break
                opt_type = data[pos]
                opt_len = data[pos + 1]
                if opt_len < 2 or pos + opt_len > len(data):
                    break
                self._ipcp_rejected_options.add(opt_type)
                pos += opt_len
            self._send_ipcp_conf_req()

    def _handle_ipv6cp(self, info: bytes):
        if len(info) < 4:
            return
        code = info[0]
        ident = info[1]
        length = (info[2] << 8) | info[3]
        data = info[4:length]

        if code == IPCP_CONF_REQ:
            print(f"  [PPP] IPv6CP ← Configure-Request (id={ident})")
            self._send_ipv6cp_conf_ack(ident, data)
        elif code == IPCP_CONF_ACK:
            print(f"  [PPP] IPv6CP ← Configure-ACK (id={ident})")
        elif code == LCP_TERM_REQ:
            info = bytes([LCP_TERM_ACK, ident, 0, 0]) + data
            info = info[:2] + struct.pack('>H', len(info)) + info[4:]
            self._send_ppp(PPP_IPV6CP, info)

    # ---- TUN ----

    def _start_tun(self):
        try:
            self._tun = TunAdapter(self._local_ip, mtu=self._mru,
                                   dns_servers=self._dns)
            self._tun.create()
            self._tun_thread = threading.Thread(target=self._tun_forward, daemon=True)
            self._tun_thread.start()
            print(f"  [TUN] forwarding thread started, system traffic can flow through {self._local_ip} via PPP")

            for dns in self._dns:
                if dns and dns != '0.0.0.0':
                    self._tun_add_route(dns)

            ftp_host = self._harness.state.ftp_host
            if ftp_host:
                self._current_ftp_route = ftp_host
                self._tun_add_route(self._current_ftp_route)
        except Exception as e:
            print(f"  [TUN] creation failed: {e}")
            self._tun = None

    def _stop_tun(self, fast=False):
        if self._tun:
            if self._current_ftp_route and not fast:
                self._tun_del_route(self._current_ftp_route)
            self._tun._running = False
            if self._tun_thread:
                self._tun_thread.join(timeout=1 if fast else 2)
            self._tun.close()
            self._tun = None
            self._tun_thread = None

    def _tun_add_route(self, target: str):
        try:
            subprocess.run([
                'netsh', 'interface', 'ip', 'add', 'route',
                f'{target}/32', self._tun._adapter_name, 'metric=1',
            ], check=True, capture_output=True, text=True)
            print(f"  [TUN] route added: {target} → {self._tun._adapter_name}")
        except subprocess.CalledProcessError as e:
            print(f"  [TUN] route add failed (admin privileges required): {e.stderr.strip()}")

    def _tun_del_route(self, target: str):
        try:
            subprocess.run([
                'netsh', 'interface', 'ip', 'delete', 'route',
                f'{target}/32', self._tun._adapter_name,
            ], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            pass

    def _tun_forward(self):
        count = 0
        batch = bytearray()
        batch_max = 8192
        backoff = 0

        transport = self._harness.transport
        state = self._harness.state

        while self._tun and self._tun._running and self._running:
            timeout = 10 + backoff * 10
            ret = _kernel32.WaitForSingleObject(
                c_void_p(self._tun.get_read_event()), timeout)

            ser = transport.serial_port
            if ser and ser.is_open:
                try:
                    outq = ser.out_waiting
                except Exception:
                    outq = 0
                if outq > 32768:
                    backoff = min(backoff + 1, 50)
                    time.sleep(0.005)
                    continue
                elif outq > 16384:
                    backoff = min(backoff + 1, 20)
                else:
                    backoff = max(backoff - 1, 0)

            while True:
                try:
                    pkt = self._tun.read()
                except Exception:
                    break
                if pkt is None:
                    break
                count += 1

                if self._verbose:
                    proto = pkt[9] if len(pkt) > 9 else 0
                    src = '.'.join(str(b) for b in pkt[12:16]) if len(pkt) > 16 else '?'
                    dst = '.'.join(str(b) for b in pkt[16:20]) if len(pkt) > 20 else '?'
                    print(f"  [TUN→PPP] #{count} proto={proto} {src} → {dst} ({len(pkt)}B)")

                frame = make_ppp_frame(PPP_IP, pkt,
                                       acfc=self._acfc_enabled,
                                       pfc=self._pfc_enabled)
                if state.mode == 'serial':
                    transport.serial_port.write(frame)
                else:
                    frame_max = transport.frame_size if transport.frame_size > 0 else 127
                    total = len(frame)
                    offset = 0
                    while offset < total:
                        chunk = frame[offset:offset + frame_max]
                        transport.send(chunk, state.ppp_dlci)
                        offset += len(chunk)

    # ---- Dial / Disconnect helpers ----

    def _wait_serial_connect(self, timeout: float = 15) -> bool:
        transport = self._harness.transport
        start = time.time()
        buf = bytearray()
        while time.time() - start < timeout and self._running:
            raw = transport.serial_port.read(transport.serial_port.in_waiting or 1)
            if raw:
                buf.extend(raw)
                text = buf.decode('latin-1', errors='replace')
                if self._verbose:
                    print(f"  [PPP RAW] {raw.hex(' ')}")
                while b'\r\n' in buf:
                    idx = buf.index(b'\r\n')
                    line = buf[:idx]
                    buf = buf[idx + 2:]
                    if line.strip():
                        print(f"  [PPP] ← {line.decode('latin-1', errors='replace').strip()}")
                if 'CONNECT' in text:
                    return True
                else:
                    time.sleep(0.05)
        return False

    def _wait_cmux_connect(self, dlci: int, timeout: float = 15) -> bool:
        transport = self._harness.transport
        start = time.time()
        while time.time() - start < timeout and self._running:
            raw = transport.serial_port.read(transport.serial_port.in_waiting or 1)
            if raw:
                if self._verbose:
                    print(f"  [PPP RAW] {raw.hex(' ')}")
                transport.cmux.parser.feed(raw)
                while True:
                    frame = transport.cmux.parser.get_frame()
                    if frame is None:
                        break
                    if frame['dlci'] == dlci and frame['type'] == FrameType.UIH:
                        text = frame['info'].decode('latin-1', errors='replace')
                        print(f"  [PPP] ← {text.strip()}")
                        if 'CONNECT' in text:
                            return True
            else:
                time.sleep(0.05)
        return False

    def _disconnect(self, fast=False):
        self._harness.events.fire(Event.PPP_DISCONNECTING)

        self._stop_tun(fast=fast)

        if self._lcp_up:
            self._send_lcp_term_req()
            if fast:
                time.sleep(0.3)
            else:
                deadline = time.time() + 3.0
                while self._lcp_up and time.time() < deadline:
                    transport = self._harness.transport
                    raw = transport.serial_port.read(transport.serial_port.in_waiting or 1)
                    if raw:
                        if self._harness.state.mode == 'serial':
                            self._parser.feed(raw)
                        else:
                            transport.cmux.parser.feed(raw)
                            while True:
                                frame = transport.cmux.parser.get_frame()
                                if frame is None:
                                    break
                                if frame['dlci'] == self._harness.state.ppp_dlci and frame['type'] == FrameType.UIH:
                                    if frame['info']:
                                        self._parser.feed(frame['info'])
                    else:
                        time.sleep(0.05)
                if not self._lcp_up:
                    print(f"  [PPP] LCP disconnected ✓")

        time.sleep(0.3 if fast else 1.0)
        transport = self._harness.transport
        state = self._harness.state

        # +++ to exit transparent mode (must wait before ATH)
        if state.mode == 'serial':
            transport.serial_port.write(b'+++\r')
            print(f"  [PPP] → +++")
        else:
            transport.send(b'+++', state.ppp_dlci)
            print(f"  [PPP] → +++ (CMUX UIH)")

        time.sleep(0.5 if fast else 1.0)

        if not fast:
            if not self._read_cmux_until(['NO CARRIER'], timeout=10.0):
                print(f"  [PPP] NO CARRIER not received, continuing...")

        # ATH to hang up
        time.sleep(0.1)
        if state.mode == 'serial':
            transport.serial_port.write(b'ATH\r')
        else:
            transport.send(b'ATH\r', state.ppp_dlci)
        self._read_cmux_until(['OK'], timeout=2.0 if fast else 5.0)

        # AT to verify command mode
        time.sleep(0.1)
        if state.mode == 'serial':
            transport.serial_port.write(b'AT\r')
        else:
            transport.send(b'AT\r', state.ppp_dlci)
        self._read_cmux_until(['OK'], timeout=2.0)

        if not fast:
            time.sleep(0.1)
            if state.mode == 'serial':
                transport.serial_port.write(b'ATE0V1\r')
            else:
                transport.send(b'ATE0V1\r', state.ppp_dlci)
            self._read_cmux_until(['OK'], timeout=2.0)

            time.sleep(0.1)
            if state.mode == 'serial':
                transport.serial_port.write(b'AT\r')
            else:
                transport.send(b'AT\r', state.ppp_dlci)
            self._read_cmux_until(['OK'], timeout=2.0)

            time.sleep(0.1)
            if state.mode == 'serial':
                transport.serial_port.write(b'ATS0=0\r')
            else:
                transport.send(b'ATS0=0\r', state.ppp_dlci)

        print(f"  [PPP] disconnect sequence complete ✓")
        self._harness.events.fire(Event.PPP_DISCONNECTED)

    def _read_cmux_until(self, keywords: list, timeout: float = 3.0):
        transport = self._harness.transport
        state = self._harness.state
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            raw = transport.serial_port.read(transport.serial_port.in_waiting or 1)
            if raw:
                if state.mode == 'serial':
                    buf.extend(raw)
                    text = buf.decode('latin-1', errors='replace')
                    if self._verbose:
                        print(f"  [PPP] ← {text.strip()}")
                    for kw in keywords:
                        if kw in text:
                            if self._verbose:
                                print(f"  [PPP] ← matched '{kw}'")
                            return True
                else:
                    transport.cmux.parser.feed(raw)
                    while True:
                        frame = transport.cmux.parser.get_frame()
                        if frame is None:
                            break
                        if frame['dlci'] == state.ppp_dlci and frame['type'] == FrameType.UIH:
                            text = frame['info'].decode('latin-1', errors='replace')
                            if self._verbose:
                                print(f"  [PPP] ← {text.strip()}")
                            for kw in keywords:
                                if kw in text:
                                    if self._verbose:
                                        print(f"  [PPP] ← matched '{kw}'")
                                    return True
            else:
                time.sleep(0.05)
        return False

    # ---- TUN route management (for external services) ----

    def add_route(self, target: str) -> None:
        """Add a route through the TUN adapter."""
        if self._tun and self._tun._running:
            self._tun_add_route(target)

    def del_route(self, target: str) -> None:
        """Remove a route from the TUN adapter."""
        if self._tun and self._tun._running:
            self._tun_del_route(target)