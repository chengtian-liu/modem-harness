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
    def open(self, port: str, baudrate: int, cmux_baudrate: int = None) -> None:
        """Open the transport. Must be called before start_reader().

        baudrate — initial serial speed used for the AT handshake.
        cmux_baudrate — CMUX target speed; when it differs from baudrate
        the module switches its UART via AT+CMUX <port_speed> and the
        transport reopens the port at the new speed. None = same as
        baudrate. Ignored by the serial transport.
        """
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