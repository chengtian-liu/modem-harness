"""CMUX transport backend — wraps CmuxTE controller."""

import time
import threading
from typing import Callable, Optional

import serial

from ..protocol.cmux import (
    FrameParser, FrameType, CtrlType,
    make_sabm, make_disc, make_uih_cmd, make_cld,
    make_msc_resp, make_msc_cmd, make_msc_fc,
    SIGNAL_FC, decode_signals,
)


# ============================================================
# CMUX TE controller
# ============================================================

class CmuxTE:
    """CMUX TE side controller"""

    def __init__(self, frame_size: int = 0):
        self.frame_size = frame_size
        self.ser = None
        self.parser = FrameParser()
        self.dlc_available = {0: False, 1: False, 2: False}
        self.frame_allowed = {1: True, 2: True}  # MSC flow control: assume allowed until told otherwise
        self.running = False
        self.lock = threading.Lock()

    def open(self, port: str, baudrate: int):
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        print(f"[Serial] {port} opened, baudrate={baudrate}")

    def close(self):
        """Exit CMUX mode on modem, then close serial port.

        Uses CLD (Close Down) — the standard GSM 07.10 command to shut down
        the entire multiplexer and return the modem to AT command mode.
        """
        if self.dlc_available[0]:
            # Step 1: Send CLD to shut down CMUX multiplexer
            print("[CMUX] Sending CLD (Close Down)...")
            self.send_raw(make_cld())
            time.sleep(0.3)

            # Step 2: Verify modem returned to AT command mode
            print("[CMUX] Verifying AT command mode...")
            self.ser.reset_input_buffer()
            self.send_at("AT")
            deadline = time.time() + 1.0
            while time.time() < deadline:
                raw = self.ser.read(self.ser.in_waiting or 1)
                if raw and b"OK" in raw:
                    print("[CMUX] ✓ Modem returned to AT command mode")
                    break
                time.sleep(0.05)
            else:
                print("[CMUX] ⚠ No AT response after CLD")

        if self.ser and self.ser.is_open:
            self.ser.close()
            print(f"[Serial] {self.ser.port} closed")

    def send_raw(self, data: bytes):
        with self.lock:
            self.ser.write(data)
            self.ser.flush()

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
            self.frame_allowed[dlci] = not fc_set

            sig_desc = decode_signals(signals)
            if old_allowed != (not fc_set):
                action = 'BLOCKED' if fc_set else 'ALLOWED'
                print(f"  [MSC] DLCI {dlci}: FC={'on' if fc_set else 'off'} "
                      f"({sig_desc}) → {action}")
            else:
                print(f"  [MSC] DLCI {dlci}: {sig_desc}")

            # Acknowledge — respond with C/R cleared, preserve P/F
            if self.ser and self.ser.is_open:
                pf = frame.get('pf', 0)
                resp = make_msc_resp(info, pf=pf)
                self.send_raw(resp)
        else:
            # ACK for our MSC command
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

    def init_cmux(self) -> bool:
        print("\n" + "=" * 60)
        print("  CMUX Initialization")
        print("=" * 60)

        print("\n[Step 1] check serial port status...")
        self.send_at("AT")
        time.sleep(0.3)
        warmup = self.ser.read(self.ser.in_waiting or 1024)

        if b"OK" in warmup:
            print(f"  AT mode, response: {warmup.decode('utf-8', errors='replace').strip()}")
            print("\n[Step 2] send AT+CMUX=0")
            if self.frame_size > 0:
                cmux_cmd = f"AT+CMUX=0,0,,{self.frame_size}"
                print(f"  specified frame size N1={self.frame_size}")
            else:
                cmux_cmd = "AT+CMUX=0"
            self.send_at(cmux_cmd)
        else:
            print(f"  No AT response (hex: {warmup.hex() if warmup else 'empty'}), may already be in CMUX mode")
            print("  trying to send SABM frame directly...")
            self.send_raw(make_sabm(0))

        resp = b''
        deadline = time.time() + 2.0
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

        # wait for UE side serial handover (AT reader → CMUX reader)
        time.sleep(1.0)

        print("\n[Step 3] Establish DLCI 0 (control channel)")
        if not self.establish_dlc(0):
            # serial handover may not be complete, retrying once
            print("  [Retry] wait 1s then retry DLCI 0...")
            time.sleep(1.0)
            if not self.establish_dlc(0):
                print("  [Fatal] control channel setup failed, cannot continue")
                return False
        time.sleep(0.2)

        print("\n[Step 4] Establish DLCI 1 (data channel 1)")
        if not self.establish_dlc(1):
            print("  [Warning] DLCI 1 setup failed")
        time.sleep(0.2)

        print("\n[Step 5] Establish DLCI 2 (data channel 2)")
        if not self.establish_dlc(2):
            print("  [Warning] DLCI 2 setup failed")

        print("\n" + "=" * 60)
        print("  CMUX Initialization complete!")
        print(f"  DLCI 0: {'✓' if self.dlc_available[0] else '✗'}")
        print(f"  DLCI 1: {'✓' if self.dlc_available[1] else '✗'}")
        print(f"  DLCI 2: {'✓' if self.dlc_available[2] else '✗'}")
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

    def __init__(self, frame_size: int = 0, verbose: bool = False):
        self._cmux = CmuxTE(frame_size=frame_size)
        self._verbose = verbose
        self._on_frame: Optional[Callable[[int, bytes], None]] = None
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._dialing = False

    # ---- TransportInterface methods ----

    def open(self, port: str, baudrate: int) -> None:
        self._cmux.open(port, baudrate)
        if not self._cmux.init_cmux():
            raise RuntimeError("CMUX initialization failed")

    def close(self) -> None:
        self._cmux.close()

    def send(self, data: bytes, dlci: int = 0) -> None:
        # Check MSC flow control — modem may have told us to stop sending
        if dlci > 0 and not self._cmux.frame_allowed.get(dlci, True):
            print(f"  [Flow Control] DLCI {dlci} blocked by MSC, frame not sent")
            return
        self._cmux.send_uih(dlci, data)

    def start_reader(self, on_frame: Callable[[int, bytes], None]) -> None:
        self._on_frame = on_frame
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def stop_reader(self) -> None:
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
                if self._dialing:
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

        if dlci == 0:
            if info:
                ctrl_type = info[0] & 0xEF if info else 0

                # Handle MSC (Modem Status Command) — flow control
                if ctrl_type == CtrlType.MSC:
                    self._cmux._handle_msc(frame)
                    return

                type_names = {
                    CtrlType.CLD: 'CLD', CtrlType.TEST: 'TEST',
                    CtrlType.FCON: 'FCON', CtrlType.FCOFF: 'FCOFF',
                    CtrlType.MSC: 'MSC', CtrlType.NSC: 'NSC',
                }
                name = type_names.get(ctrl_type, f'0x{ctrl_type:02X}')
                if self._verbose:
                    print(f"  [DLCI 0] {name} {info.hex(' ')}")
        elif self._on_frame:
            self._on_frame(dlci, info)