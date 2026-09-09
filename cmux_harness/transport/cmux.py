"""CMUX transport backend — wraps CmuxTE controller."""

import time
import threading
from typing import Callable, Optional

import serial

from ..protocol.cmux import (
    FrameParser, FrameType, CtrlType, CR_BIT,
    make_sabm, make_disc, make_uih_cmd, make_cld,
    make_msc_resp, make_msc_cmd, make_msc_fc,
    make_test, make_test_resp,
    SIGNAL_FC, decode_signals,
)


# Max frames to batch before forcing a serial write
CACHE_FRAMES = 20

# How long send() waits while a DLCI is flow-control blocked before giving
# up and dropping the frame, so a stuck link cannot hang the sender forever.
FC_WAIT_TIMEOUT = 5.0

# ============================================================
# AT+CMUX <port_speed> mapping
# ============================================================

# Quectel-style <port_speed> enumeration used in AT+CMUX=0,0,<speed>,<N1>
# (same values as quectel_speeds[] in the reference gsm0710muxd driver).
# NOTE: this is NOT the 3GPP TS 27.007 enumeration (which starts at 0=9600).
PORT_SPEED_INDEX = {
    9600: 1, 19200: 2, 38400: 3, 57600: 4,
    115200: 5, 230400: 6, 460800: 7, 921600: 8,
    1500000: 16, 2000000: 20, 3000000: 23, 4000000: 26,
}

# Default N1 advertised to the module when the user did not pick a frame
# size — same default the engine uses locally (see _full_frame_size).
DEFAULT_N1 = 127


# ============================================================
# Keepalive (TEST probes on DLCI 0)
# ============================================================

# Seconds between TEST probes. Typical CMUX keepalive intervals are 5–10 s;
# a probe frame is only ~14 bytes, so even a slow 115200 link barely notices.
DEFAULT_KEEPALIVE_INTERVAL = 0

# How many consecutive unanswered probes before the link is declared dead.
# Any frame from the modem (TEST response, NSC, MSC, PPP, AT — anything)
# counts as an answer. 3 × 10 s ≈ 30 s of total silence.
DEFAULT_KEEPALIVE_THRESHOLD = 3


# ============================================================
# CMUX TE controller
# ============================================================

class CmuxTE:
    """CMUX TE side controller"""

    def __init__(self, frame_size: int = 0, verbose: bool = False, channels: int = 2):
        self.frame_size = frame_size
        self.verbose = verbose
        self.port = None
        self.baudrate = 0   # current serial speed, set in open()
        self.open_baudrate = 0  # speed the port was first opened at (entry
                                # speed); some modules restore it after CLD
        self.cmux_baudrate = 0  # CMUX target speed; != baudrate triggers a switch
        # Set once the mux is actually established (DLCI 0 up); close() uses
        # it to decide whether CLD is needed even if dlc_available was reset.
        self.mux_established = False
        # Number of data channels (DLCI 1..channels); DLCI 0 is always the
        # control channel. GSM 07.10 allows up to 63 DLCIs, but the module
        # decides how many it actually grants — SABM on an unsupported DLCI
        # just gets a DM response and the channel stays down.
        self.channels = channels
        self.ser = None
        self.parser = FrameParser()
        self.dlc_available = {d: False for d in range(channels + 1)}
        self.frame_allowed = {d: True for d in range(1, channels + 1)}  # MSC flow control: assume allowed until told otherwise
        self.running = False
        self.lock = threading.Lock()
        # MSC flow control: senders block on this condition while a DLCI is
        # FC-blocked; _handle_msc notifies when the modem re-allows it.
        self._fc_cond = threading.Condition()
        # Frames dropped after FC_WAIT_TIMEOUT while a DLCI stayed blocked
        self.fc_drop_count = 0
        # Write batching: full-size data
        # frames accumulate here until a small frame arrives or the cache
        # reaches CACHE_FRAMES, then everything goes out in one write().
        self._write_cache = bytearray()
        self._cache_frames = 0

    def open(self, port: str, baudrate: int, cmux_baudrate: int = None):
        self.port = port
        self.baudrate = baudrate
        self.open_baudrate = baudrate
        self.cmux_baudrate = cmux_baudrate if cmux_baudrate else baudrate
        self.ser = self._make_serial(port, baudrate)
        print(f"[Serial] {port} opened, baudrate={baudrate}")
        if self.cmux_baudrate != baudrate:
            print(f"[Serial] CMUX target baudrate={self.cmux_baudrate} "
                  f"(module switches to it via AT+CMUX <port_speed>)")

    def _make_serial(self, port: str, baudrate: int) -> serial.Serial:
        return serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )

    # ---- Shutdown ----

    def close(self):
        """Exit CMUX mode on the modem, then close the serial port.

        Teardown follows the GSM 07.10 order:
          1. DISC every open data channel (lets the module release
             per-channel state such as a PPP session)
          2. CLD (Close Down) on the control channel to shut the whole
             multiplexer down
          3. probe with plain AT until the modem answers again

        Step 3 retries several times: the module can need a moment to
        leave multiplexer mode and re-arm its AT parser, and any AT sent
        mid-transition is eaten as 07.10 garbage. When the baud rate was
        switched on entry via AT+CMUX <port_speed>, a final probe at the
        entry speed is also tried, because some modules restore the
        pre-CMUX UART rate when the mux closes.
        """
        if self.dlc_available[0] or self.mux_established:
            # Step 0: swallow anything the module is still sending (PPP
            # tail data, URCs). The reader thread is already stopped at
            # this point; teardown frames sent into an unread backlog
            # have been observed to go unanswered entirely.
            self._drain_rx()

            # Step 1: close open data channels (DISC → UA)
            for dlci in range(1, self.channels + 1):
                if self.dlc_available.get(dlci):
                    self._shutdown_channel(dlci)

            # Step 2: CLD shuts down the whole multiplexer
            print("[CMUX] Sending CLD (Close Down)...")
            self.send_raw(make_cld())
            time.sleep(0.3)

            # Step 3: verify AT command mode, with retries
            print("[CMUX] Verifying AT command mode...")
            if self._wait_at_mode():
                print("[CMUX] ✓ Modem returned to AT command mode")
                self.mux_established = False
            else:
                print("[CMUX] ⚠ no AT response, retrying CLD...")
                self.send_raw(make_cld())
                if self._wait_at_mode():
                    print("[CMUX] ✓ Modem returned to AT command mode (2nd CLD)")
                    self.mux_established = False
                elif self.open_baudrate and self.baudrate != self.open_baudrate:
                    # possible rate restore on mux exit — probe at entry speed
                    print(f"[CMUX] ⚠ no response at {self.baudrate}, probing at "
                          f"entry baudrate {self.open_baudrate}...")
                    try:
                        self.ser.close()
                        time.sleep(0.2)
                        self.ser = self._make_serial(self.port, self.open_baudrate)
                        self.baudrate = self.open_baudrate
                        if self._wait_at_mode():
                            print(f"[CMUX] ✓ Modem returned to AT command mode "
                                  f"(at entry baudrate {self.open_baudrate})")
                            self.mux_established = False
                        else:
                            self._report_stuck()
                    except Exception as e:
                        print(f"[CMUX] ⚠ entry-baudrate probe failed: {e}")
                        self._report_stuck()
                else:
                    self._report_stuck()

        if self.ser and self.ser.is_open:
            self.ser.close()
            print(f"[Serial] {self.ser.port} closed")

    def _report_stuck(self):
        print("[CMUX] ⚠ No AT response after CLD — modem may still be in CMUX mode")
        print("        re-run this tool to recover (it re-attaches to a running mux)")

    def _drain_rx(self, timeout: float = 1.0, quiet: float = 0.15) -> None:
        """Read and discard pending RX data before teardown.

        Keeps reading until the line stays silent for `quiet` seconds or
        `timeout` elapses altogether. The reader thread is stopped by the
        time close() runs, so anything the module still has queued (PPP
        tail data, Terminate-Ack, URCs) must be consumed here — modules
        left with an unread backlog have been observed to ignore the
        DISC/CLD teardown frames entirely.
        """
        deadline = time.time() + timeout
        last_rx = time.time()
        while time.time() < deadline:
            try:
                waiting = self.ser.in_waiting
            except Exception:
                break
            if waiting:
                self.ser.read(waiting)
                last_rx = time.time()
            elif time.time() - last_rx >= quiet:
                break
            else:
                time.sleep(0.02)

    def _shutdown_channel(self, dlci: int) -> None:
        """DISC a data channel and wait briefly for the UA/DM answer."""
        print(f"[CMUX] Closing DLCI {dlci} (DISC)...")
        self.send_raw(make_disc(dlci))
        deadline = time.time() + 1.0
        while time.time() < deadline:
            frame = self.wait_for_frame(
                dlci=dlci, timeout=max(0.05, deadline - time.time()))
            if frame is None:
                break
            if frame['type'] == FrameType.UA:
                self.dlc_available[dlci] = False
                print(f"  DLCI {dlci} closed (UA)")
                return
            if frame['type'] == FrameType.DM:
                self.dlc_available[dlci] = False
                print(f"  DLCI {dlci} already closed (DM)")
                return
            # stray data frame — keep waiting for the DISC answer
        print(f"  DLCI {dlci}: no DISC answer — continuing, CLD will force-close")

    def _wait_at_mode(self, rounds: int = 3, round_timeout: float = 1.0) -> bool:
        """Probe with plain AT until the modem answers OK.

        Runs up to `rounds` attempts of `round_timeout` seconds each,
        draining the RX buffer before every probe. After CLD the module
        needs a moment to leave multiplexer mode, so a single probe is
        not reliable.
        """
        for _ in range(rounds):
            self.ser.reset_input_buffer()
            self.send_at("AT")
            deadline = time.time() + round_timeout
            buf = b''
            while time.time() < deadline:
                raw = self.ser.read(self.ser.in_waiting or 1)
                if raw:
                    buf += raw
                    if b"OK" in buf:
                        return True
                else:
                    time.sleep(0.02)
        return False

    @property
    def _full_frame_size(self) -> int:
        """Wire size of a full-size UIH frame (payload == N1).

        Overhead is flag+addr+ctrl+len(1 or 2)+fcs+flag = 6 bytes when
        N1 <= 127 (1-byte length), 7 bytes when N1 > 127 (2-byte length),
        e.g. N1=127 → 133 bytes on the wire.
        """
        n1 = self.frame_size if self.frame_size > 0 else 127
        return n1 + (7 if n1 > 127 else 6)

    def send_raw(self, data: bytes):
        """Queue a frame for transmission, batching consecutive frames.

        Frames accumulate in the write cache and go out in a single serial
        write when either
        - the frame is smaller than a full-size frame (control frames and
          packet-tail fragments must not be delayed), or
        - CACHE_FRAMES full-size frames have accumulated.

        Bulk transfers (PPP over TUN) are runs of full-size frames ending
        in a short tail, so each packet naturally drains in one write().
        """
        with self.lock:
            self._write_cache.extend(data)
            self._cache_frames += 1
            if len(data) < self._full_frame_size or self._cache_frames >= CACHE_FRAMES:
                self._flush_cache_locked()

    def _flush_cache_locked(self):
        """Write all cached frames out in one go. Caller must hold self.lock."""
        if not self._write_cache:
            return
        self.ser.write(bytes(self._write_cache))
        self.ser.flush()
        self._write_cache.clear()
        self._cache_frames = 0

    def send_at(self, cmd: str):
        self.send_raw((cmd + '\r').encode())

    def send_uih(self, dlci: int, data: bytes):
        frame = make_uih_cmd(dlci, data)
        self.send_raw(frame)

    def wait_for_frame(self, frame_type: int = None, dlci: int = None, timeout: float = 3.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ser.read(self.ser.in_waiting or 1)
            if raw:
                self.parser.feed(raw)
            frame = self.parser.get_frame()
            if frame:
                if frame_type is not None:
                    if frame['type'] == frame_type and (dlci is None or frame['dlci'] == dlci):
                        return frame
                elif dlci is not None:
                    if frame['dlci'] == dlci:
                        return frame
                else:
                    return frame
            else:
                time.sleep(0.01)
        return None

    def establish_dlc(self, dlci: int) -> bool:
        print(f"\n[Establish DLCI {dlci}] sending SABM...")
        self.send_raw(make_sabm(dlci))
        frame = self.wait_for_frame(dlci=dlci, timeout=3.0)
        if frame is None:
            print(f"  [Failed] timeout, no response")
            return False
        if frame['type'] == FrameType.UA:
            print(f"  [Success] received UA response, DLCI {dlci} established")
            self.dlc_available[dlci] = True
            return True
        elif frame['type'] == FrameType.DM:
            print(f"  [Failed] received DM response, UE rejected DLCI {dlci}")
            return False
        else:
            print(f"  [Failed] received unknown response type=0x{frame['type']:02X}")
            return False

    # ---- MSC (Modem Status Command) handling ----

    def _parse_msc(self, info: bytes) -> tuple:
        """Parse MSC information field.

        Returns (dlci, signals, is_command).
        """
        dlci = (info[2] & 0xFC) >> 2
        signals = info[3] if len(info) > 3 else 0
        is_command = bool(info[0] & 0x02)  # C/R bit
        return dlci, signals, is_command

    def _handle_msc(self, frame: dict):
        """Process incoming MSC on DLCI 0."""
        info = frame['info']
        if len(info) < 4:
            return

        dlci, signals, is_command = self._parse_msc(info)

        if is_command:
            # Update flow control state for this DLCI
            fc_set = bool(signals & SIGNAL_FC)
            old_allowed = self.frame_allowed.get(dlci, True)
            with self._fc_cond:
                self.frame_allowed[dlci] = not fc_set
                if not fc_set:
                    # Channel re-allowed — wake senders blocked in send()
                    self._fc_cond.notify_all()

            sig_desc = decode_signals(signals)
            if old_allowed != (not fc_set):
                # Flow control state actually changed — a real event, always shown
                action = 'BLOCKED' if fc_set else 'ALLOWED'
                print(f"  [MSC] DLCI {dlci}: FC={'on' if fc_set else 'off'} "
                      f"({sig_desc}) → {action}")
            elif self.verbose:
                # Same state again — noise unless debugging
                print(f"  [MSC] DLCI {dlci}: {sig_desc}")

            # Acknowledge — respond with C/R cleared, preserve P/F
            if self.ser and self.ser.is_open:
                pf = frame.get('pf', 0)
                resp = make_msc_resp(info, pf=pf)
                self.send_raw(resp)
        else:
            # C/R cleared — response to an MSC command we sent, or on modules
            # that never get commands from us, an unsolicited status report
            # (e.g. the data channel reporting RTC|RTR when it comes up).
            # Purely informational, so kept out of normal output.
            if self.verbose:
                sig_desc = decode_signals(signals)
                print(f"  [MSC ACK] DLCI {dlci}: {sig_desc}")

    def send_msc(self, dlci: int, fc_on: bool):
        """Send MSC command to modem to set flow control on a DLCI.

        Args:
            dlci:  Channel number.
            fc_on: True = tell modem to stop sending (FC=1),
                   False = allow modem to send (FC=0).
        """
        frame = make_msc_fc(dlci, fc_on)
        if self.ser and self.ser.is_open:
            self.send_raw(frame)
            action = 'stop' if fc_on else 'resume'
            print(f"  [MSC TX] DLCI {dlci}: telling modem to {action} sending")

    def _reopen_serial(self, step: int) -> None:
        """Reopen the host serial port at the CMUX target baud rate.

        On AT+CMUX=0,0,<port_speed>,<N1> the module switches its UART to
        <port_speed> as it enters mux mode, so the host side must follow
        before the SABM handshake.
        """
        target = self.cmux_baudrate
        print(f"\n[Step {step}] module switched to {target} baud, reopening serial port")
        self.ser.close()
        time.sleep(0.5)
        self.ser = self._make_serial(self.port, target)
        self.baudrate = target
        print(f"[Serial] {self.port} reopened, baudrate={target}")

    def _build_cmux_command(self) -> str:
        """Build the AT+CMUX command with <port_speed> and N1 parameters.

        Format: AT+CMUX=0,0,<port_speed>,<N1>
          <port_speed> — Quectel-style baud rate index (PORT_SPEED_INDEX),
                         tells the module the serial rate the CMUX port
                         runs at. Omitted when the baud rate has no index.
          <N1>         — max frame size; the user's choice or DEFAULT_N1
                         (same default the engine uses locally).
        """
        n1 = self.frame_size if self.frame_size > 0 else DEFAULT_N1
        speed_idx = PORT_SPEED_INDEX.get(self.cmux_baudrate)
        if speed_idx is None:
            # Non-standard baud rate — leave the module's speed untouched
            if self.frame_size > 0:
                return f"AT+CMUX=0,0,,{self.frame_size}"
            return "AT+CMUX=0"
        return f"AT+CMUX=0,0,{speed_idx},{n1}"

    def _read_cmux_response(self, timeout: float = 2.0) -> bytes:
        """Read the response after AT+CMUX (or a direct SABM).

        Collects bytes until OK/ERROR text arrives, or 07.10 frames are
        detected (module already in CMUX mode answering the SABM).
        """
        resp = b''
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                resp += chunk
                if b"OK" in resp or b"ERROR" in resp:
                    break
                if b'\xf9' in resp and resp.count(b'\xf9') >= 2:
                    time.sleep(0.05)
                    chunk = self.ser.read(self.ser.in_waiting or 1)
                    if not chunk:
                        break
                    resp += chunk
                    break
            else:
                time.sleep(0.02)
        return resp

    def init_cmux(self) -> bool:
        print("\n" + "=" * 60)
        print("  CMUX Initialization")
        print("=" * 60)

        step = 1
        print(f"\n[Step {step}] check serial port status...")
        self.send_at("AT")
        time.sleep(0.3)
        warmup = self.ser.read(self.ser.in_waiting or 1024)

        if b"OK" in warmup:
            print(f"  AT mode, response: {warmup.decode('utf-8', errors='replace').strip()}")
            step += 1
            cmux_cmd = self._build_cmux_command()
            print(f"\n[Step {step}] send {cmux_cmd}")
            speed_idx = PORT_SPEED_INDEX.get(self.cmux_baudrate)
            if speed_idx is not None:
                print(f"  port speed: {self.cmux_baudrate} (index {speed_idx})")
            else:
                print(f"  [Warning] baud rate {self.cmux_baudrate} has no <port_speed> "
                      f"index, module keeps its current speed")
            n1 = self.frame_size if self.frame_size > 0 else DEFAULT_N1
            print(f"  frame size N1={n1}" + ("" if self.frame_size > 0 else " (default)"))
            self.send_at(cmux_cmd)
            # the module switches its UART to <port_speed> as it enters CMUX
            speed_switch_pending = self.cmux_baudrate != self.baudrate
        else:
            print(f"  No AT response (hex: {warmup.hex() if warmup else 'empty'}), may already be in CMUX mode")
            print("  trying to send SABM frame directly...")
            self.send_raw(make_sabm(0))
            cmux_cmd = None
            speed_switch_pending = False

        resp = self._read_cmux_response()

        # Some firmware only accepts the bare command — if the extended
        # parameter set gets rejected, retry without parameters. The module
        # then enters CMUX at the current speed, so no host-side reopen.
        if b"ERROR" in resp and cmux_cmd is not None and cmux_cmd != "AT+CMUX=0":
            print(f"  [Retry] device rejected '{cmux_cmd}', falling back to AT+CMUX=0")
            self.send_at("AT+CMUX=0")
            resp = self._read_cmux_response()
            speed_switch_pending = False

        text = resp.decode('utf-8', errors='replace').strip()
        print(f"  Raw response: {resp.hex() if resp else 'empty'}")
        if text:
            print(f"  Text: {text}")

        self.parser.feed(resp)

        if b"ERROR" in resp:
            print("  [Failed] device returned ERROR")
            return False

        if b"OK" in resp:
            print("  CMUX mode activated")

        # Module UART now runs at <port_speed> — host side follows
        if speed_switch_pending and b"OK" in resp:
            step += 1
            self._reopen_serial(step)

        # wait for UE side serial handover (AT reader → CMUX reader)
        time.sleep(1.0)

        step += 1
        print(f"\n[Step {step}] Establish DLCI 0 (control channel)")
        if not self.establish_dlc(0):
            # serial handover may not be complete, retrying once
            print("  [Retry] wait 1s then retry DLCI 0...")
            time.sleep(1.0)
            if not self.establish_dlc(0):
                print("  [Fatal] control channel setup failed, cannot continue")
                return False
        self.mux_established = True
        time.sleep(0.2)

        step += 1
        print(f"\n[Step {step}] Establish data channels")
        for dlci in range(1, self.channels + 1):
            if not self.establish_dlc(dlci):
                print(f"  [Warning] DLCI {dlci} setup failed")
            time.sleep(0.2)

        print("\n" + "=" * 60)
        print("  CMUX Initialization complete!")
        for dlci in sorted(self.dlc_available):
            label = 'control' if dlci == 0 else f'data {dlci}'
            print(f"  DLCI {dlci} ({label}): {'✓' if self.dlc_available[dlci] else '✗'}")
        fc_status = ', '.join(
            f"DLCI {d}: {'allowed' if a else 'blocked'}"
            for d, a in self.frame_allowed.items()
        )
        print(f"  MSC flow control: {fc_status}")
        print("=" * 60)
        return True


# ============================================================
# CMUX Transport wrapper
# ============================================================

class CmuxTransport:
    """CMUX transport implementation — wraps CmuxTE."""

    def __init__(self, frame_size: int = 0, verbose: bool = False,
                 keepalive: float = DEFAULT_KEEPALIVE_INTERVAL,
                 keepalive_threshold: int = DEFAULT_KEEPALIVE_THRESHOLD,
                 channels: int = 2):
        self._cmux = CmuxTE(frame_size=frame_size, verbose=verbose, channels=channels)
        self._verbose = verbose
        self._on_frame: Optional[Callable[[int, bytes], None]] = None
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._dialing = False

        # ---- Keepalive (TEST probes on DLCI 0) ----
        # 0 disables keepalive entirely.
        self._keepalive_interval = float(keepalive)
        self._keepalive_threshold = int(keepalive_threshold)
        self._keepalive_running = False
        self._keepalive_thread: Optional[threading.Thread] = None
        # Consecutive TEST probes sent since the last frame received from
        # the modem. Any received frame — TEST response, NSC, MSC, PPP or AT
        # data — proves liveness and resets this to zero.
        self._unanswered = 0
        # While True the reader loop hands the serial port over to the
        # recovery routine (same exclusive-access idea as `_dialing`).
        self._recovering = False
        # Link-down already announced to the harness for the current outage
        # (so the callback fires once per outage, not once per retry cycle).
        self._link_down_reported = False
        # Stats
        self.keepalive_probes = 0   # total TEST probes sent
        self.recovery_count = 0     # successful link recoveries
        # Optional hooks for the harness layer:
        #   on_link_down      — link declared dead, before recovery starts
        #   on_link_recovered — CMUX re-established after a recovery
        self.on_link_down: Optional[Callable[[], None]] = None
        self.on_link_recovered: Optional[Callable[[], None]] = None

    # ---- TransportInterface methods ----

    def open(self, port: str, baudrate: int, cmux_baudrate: int = None) -> None:
        self._cmux.open(port, baudrate, cmux_baudrate)
        if not self._cmux.init_cmux():
            raise RuntimeError("CMUX initialization failed")

    def close(self) -> None:
        self._cmux.close()

    def send(self, data: bytes, dlci: int = 0) -> None:
        # MSC flow control: if the modem told us to stop (FC=1), wait for
        # the channel to be re-allowed instead of dropping the frame.
        # Senders stall here, so nothing is read out of the upstream buffer
        # (wintun) and backpressure propagates to the IP stack.
        te = self._cmux
        if dlci > 0 and not te.frame_allowed.get(dlci, True):
            deadline = time.time() + FC_WAIT_TIMEOUT
            with te._fc_cond:
                while not te.frame_allowed.get(dlci, True):
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        te.fc_drop_count += 1
                        print(f"  [Flow Control] DLCI {dlci} blocked for "
                              f"{FC_WAIT_TIMEOUT:.0f}s, frame dropped "
                              f"(total dropped: {te.fc_drop_count})")
                        return
                    te._fc_cond.wait(remaining)
        te.send_uih(dlci, data)

    def start_reader(self, on_frame: Callable[[int, bytes], None]) -> None:
        self._on_frame = on_frame
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # Keepalive starts together with the reader — CMUX is up at this point
        if self._keepalive_interval > 0:
            self._unanswered = 0
            self._keepalive_running = True
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, daemon=True)
            self._keepalive_thread.start()
            print(f"[CMUX] keepalive enabled: TEST probe every "
                  f"{self._keepalive_interval:g}s, link-down after "
                  f"{self._keepalive_threshold} unanswered "
                  f"(~{self._keepalive_interval * self._keepalive_threshold:g}s silence)")

    def stop_reader(self) -> None:
        # Stop keepalive first so no probe/recovery runs while we shut down
        self._keepalive_running = False
        if self._keepalive_thread:
            self._keepalive_thread.join(timeout=3)
            self._keepalive_thread = None
        self._running = False
        if self._reader_thread:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None

    @property
    def is_open(self) -> bool:
        return self._cmux.ser is not None and self._cmux.ser.is_open

    @property
    def frame_size(self) -> int:
        return self._cmux.frame_size

    @property
    def serial_port(self) -> serial.Serial:
        return self._cmux.ser

    # ---- CMUX-specific accessors ----

    @property
    def cmux(self) -> CmuxTE:
        """Direct access to the CmuxTE controller (for dialing, etc.)."""
        return self._cmux

    @property
    def dialing(self) -> bool:
        return self._dialing

    @dialing.setter
    def dialing(self, value: bool) -> None:
        self._dialing = value

    @property
    def dlc_available(self) -> dict[int, bool]:
        return self._cmux.dlc_available

    # ---- Reader loop ----

    def _reader_loop(self):
        while self._running:
            try:
                if self._dialing or self._recovering:
                    time.sleep(0.05)
                    continue
                if self._cmux.ser and self._cmux.ser.is_open and self._cmux.ser.in_waiting:
                    with self._cmux.lock:
                        raw = self._cmux.ser.read(self._cmux.ser.in_waiting)
                    self._cmux.parser.feed(raw)
                    while True:
                        frame = self._cmux.parser.get_frame()
                        if frame is None:
                            break
                        self._dispatch_frame(frame)
                else:
                    time.sleep(0.05)
            except Exception as e:
                if self._running:
                    print(f"\n  [read error] {e}")
                break

    def _dispatch_frame(self, frame: dict):
        dlci = frame['dlci']
        info = frame['info']

        # Any frame that made it through the parser is proof the modem is
        # alive — keepalive resets its unanswered-probe counter on it.
        self._unanswered = 0

        if dlci == 0:
            if info:
                # Mask off C/R so commands (0xE3) and responses (0xE1) both
                # match their CtrlType constant.
                ctrl_type = info[0] & ~CR_BIT

                # Handle MSC (Modem Status Command) — flow control
                if ctrl_type == CtrlType.MSC:
                    self._cmux._handle_msc(frame)
                    return

                # Handle TEST — if the modem probes *us*, echo the payload
                # back as a response (C/R cleared) so its keepalive is
                # answered too.
                if ctrl_type == CtrlType.TEST:
                    if info[0] & CR_BIT and len(info) >= 2:
                        resp = make_test_resp(info, pf=frame.get('pf', 0))
                        self._cmux.send_raw(resp)
                        print(f"  [DLCI 0] TEST command from modem, responded")
                    elif self._verbose:
                        print(f"  [DLCI 0] TEST response {info.hex(' ')}")
                    return

                type_names = {
                    CtrlType.CLD: 'CLD', CtrlType.TEST: 'TEST',
                    CtrlType.FCON: 'FCON', CtrlType.FCOFF: 'FCOFF',
                    CtrlType.MSC: 'MSC', CtrlType.NSC: 'NSC',
                }
                name = type_names.get(ctrl_type, f'0x{ctrl_type:02X}')
                if name == 'NSC' and self._verbose:
                    print(f"  [DLCI 0] NSC — modem rejected a control command "
                          f"(may not support it, link is still alive)")
                if self._verbose:
                    print(f"  [DLCI 0] {name} {info.hex(' ')}")
        elif self._on_frame:
            self._on_frame(dlci, info)

    # ---- Keepalive ----

    def _keepalive_loop(self):
        """Periodically send TEST probes on DLCI 0 and watch for total silence.

        Every interval a TEST command goes out; any frame coming back from
        the modem (TEST response, NSC, MSC, PPP data — anything) proves it
        is alive and resets the counter. When N consecutive probes go
        unanswered the link is declared dead, the harness is notified, and
        recovery (CLD + full CMUX re-init) is retried every cycle until it
        succeeds.
        """
        interval = self._keepalive_interval
        try:
            while self._keepalive_running:
                # Sleep in small steps so stop_reader() stays responsive
                deadline = time.time() + interval
                while self._keepalive_running and time.time() < deadline:
                    time.sleep(0.2)
                if not self._keepalive_running:
                    break

                te = self._cmux
                if not (te.ser and te.ser.is_open):
                    continue
                # During dialing/recovery the reader is paused, so any reply
                # would sit unread in the serial buffer — don't probe, don't
                # judge, and give the next window a fresh counter.
                if self._dialing or self._recovering:
                    self._unanswered = 0
                    continue

                te.send_raw(make_test())
                self.keepalive_probes += 1
                self._unanswered += 1
                if self._unanswered <= 1:
                    # Healthy heartbeat — silent unless --verbose is on.
                    if self._verbose:
                        print(f"  [Keepalive] TEST probe #{self.keepalive_probes}")
                else:
                    print(f"  [Keepalive] TEST probe #{self.keepalive_probes} "
                          f"— no reply, {self._unanswered} unanswered")

                if self._unanswered >= self._keepalive_threshold:
                    silent = interval * self._unanswered
                    print(f"\n  [Keepalive] no frame from modem for ~{silent:.0f}s "
                          f"({self._unanswered} unanswered probes), link presumed dead")
                    self._handle_link_down()
        except Exception as e:
            if self._keepalive_running:
                print(f"  [Keepalive] thread error: {e}")

    def _handle_link_down(self):
        """Notify the harness once per outage, then try to recover the link."""
        if not self._link_down_reported:
            self._link_down_reported = True
            if self.on_link_down:
                try:
                    self.on_link_down()
                except Exception as e:
                    print(f"  [Keepalive] on_link_down hook error: {e}")
        self._recover_link()

    def _recover_link(self):
        """Try to bring CMUX back: CLD → re-init → re-establish DLCIs.

        The reader loop is paused for the duration (`_recovering`) so the
        init routine gets exclusive access to the serial port, the same
        pattern PPP dialing uses. Retried every keepalive cycle until it
        succeeds — the modem may need several attempts to come back.
        """
        print("  [Keepalive] attempting link recovery (CLD + CMUX re-init)...")
        self._recovering = True
        try:
            te = self._cmux

            # Reset local mux state so init starts clean
            te.parser = FrameParser()
            te.dlc_available = {d: False for d in range(te.channels + 1)}
            te.frame_allowed = {d: True for d in range(1, te.channels + 1)}
            with te._fc_cond:
                te._fc_cond.notify_all()  # wake senders stalled on the dead link

            # If the modem still parses frames, ask it to shut its mux down
            # first; if it is already hung this just times out harmlessly.
            try:
                if te.ser and te.ser.is_open:
                    te.send_raw(make_cld())
                    time.sleep(0.5)
                    te.ser.reset_input_buffer()
            except Exception:
                pass

            if not self._keepalive_running:
                return False  # shutdown in progress, don't re-init

            ok = te.init_cmux()
            if not self._keepalive_running:
                return ok

            if ok:
                self._unanswered = 0
                self._link_down_reported = False
                self.recovery_count += 1
                print(f"  [Keepalive] ✓ link recovered "
                      f"(total recoveries: {self.recovery_count})")
                if self.on_link_recovered:
                    try:
                        self.on_link_recovered()
                    except Exception as e:
                        print(f"  [Keepalive] on_link_recovered hook error: {e}")
            else:
                print("  [Keepalive] ✗ recovery failed, retrying next cycle")
            return ok
        except Exception as e:
            print(f"  [Keepalive] recovery error: {e}")
            return False
        finally:
            self._recovering = False