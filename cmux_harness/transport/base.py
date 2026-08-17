"""Transport abstraction — CMUX and Serial backends implement this interface."""

from abc import ABC, abstractmethod
from typing import Callable

import serial


class TransportInterface(ABC):
    """Transport backend abstract base class.

    The reader callback signature is: on_frame(dlci: int, data: bytes)
    - CMUX transport: dlci is the decoded DLCI number, data is the UIH info payload
    - Serial transport: dlci is always 0, data is raw bytes from the serial port
    """

    @abstractmethod
    def open(self, port: str, baudrate: int) -> None:
        """Open the transport. Must be called before start_reader()."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the transport and release all resources."""
        ...

    @abstractmethod
    def send(self, data: bytes, dlci: int = 0) -> None:
        """Send data. dlci is ignored in serial mode."""
        ...

    @abstractmethod
    def start_reader(self, on_frame: Callable[[int, bytes], None]) -> None:
        """Start background reader thread. Calls on_frame(dlci, data) for each received frame."""
        ...

    @abstractmethod
    def stop_reader(self) -> None:
        """Stop the reader thread."""
        ...

    @property
    @abstractmethod
    def is_open(self) -> bool:
        ...

    @property
    @abstractmethod
    def frame_size(self) -> int:
        """CMUX max frame size N1. Returns 0 for serial mode (no limit)."""
        ...

    @property
    @abstractmethod
    def serial_port(self) -> serial.Serial:
        """The underlying pyserial Serial object (for backpressure checks, etc.)."""
        ...