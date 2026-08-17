"""Serial transport backend — raw serial, no CMUX framing."""

import time
import threading
from typing import Callable, Optional

import serial


class SerialTransport:
    """Raw serial transport implementation."""

    def __init__(self, verbose: bool = False):
        self._ser: Optional[serial.Serial] = None
        self._verbose = verbose
        self._on_frame: Optional[Callable[[int, bytes], None]] = None
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._dialing = False

    # ---- TransportInterface methods ----

    def open(self, port: str, baudrate: int) -> None:
        self._ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        print(f"[Serial] {port} opened, baudrate={baudrate}")

    def close(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
            print(f"[Serial] {self._ser.port} closed")

    def send(self, data: bytes, dlci: int = 0) -> None:
        """Send raw data. dlci is ignored in serial mode."""
        with self._lock:
            self._ser.write(data)
            self._ser.flush()

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
        return self._ser is not None and self._ser.is_open

    @property
    def frame_size(self) -> int:
        return 0  # no frame size limit in serial mode

    @property
    def serial_port(self) -> serial.Serial:
        return self._ser

    # ---- Serial-specific accessors ----

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    @property
    def dialing(self) -> bool:
        return self._dialing

    @dialing.setter
    def dialing(self, value: bool) -> None:
        self._dialing = value

    # ---- Reader loop ----

    def _reader_loop(self):
        while self._running:
            try:
                if self._dialing:
                    time.sleep(0.05)
                    continue
                if self._ser and self._ser.is_open and self._ser.in_waiting:
                    with self._lock:
                        raw = self._ser.read(self._ser.in_waiting)
                    if raw and self._on_frame:
                        self._on_frame(0, raw)  # dlci=0 for raw serial
                else:
                    time.sleep(0.05)
            except Exception as e:
                if self._running:
                    print(f"\n  [Serial read error] {e}")
                break