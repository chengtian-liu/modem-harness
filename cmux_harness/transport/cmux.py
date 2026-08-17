"""CMUX transport backend — wraps CmuxTE controller."""

import time
import threading
from typing import Callable, Optional

import serial

from ..protocol.cmux import (
    FrameParser, FrameType, CtrlType,
    make_sabm, make_disc, make_uih_cmd, make_cld,
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
        if self.dlc_available[0]:
            self.send_raw(make_disc(0))
            time.sleep(0.5)
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