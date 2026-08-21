"""CMUX protocol constants, frame builder, and frame parser (GSM 07.10)"""

from collections import deque
from enum import IntEnum

# ============================================================
# GSM 07.10 CMUX protocol constants
# ============================================================

CMUX_FLAG = 0xF9
EA = 0x01
CR_BIT = 0x02
PF_BIT = 0x10


class FrameType(IntEnum):
    SABM = 0x2F   # Set Asynchronous Balanced Mode
    UA   = 0x63   # Unnumbered Acknowledgement
    DM   = 0x0F   # Disconnected Mode
    DISC = 0x43   # Disconnect
    UIH  = 0xEF   # Unnumbered Information with Header check
    UI   = 0x03   # Unnumbered Information


class CtrlType(IntEnum):
    """control channel message types (DLCI 0)"""
    CLD   = 0xC1   # Close Down
    TEST  = 0x21   # Test
    FCON  = 0xA1   # Flow Control On
    FCOFF = 0x61   # Flow Control Off
    MSC   = 0xE1   # Modem Status Command
    NSC   = 0x11   # Non Supported Command


# ============================================================
# V.24 signal bits (used in MSC — Modem Status Command)
# ============================================================

SIGNAL_FC  = 0x02   # Flow Control  — set = "do not send frames"
SIGNAL_RTC = 0x04   # Ready To Communicate
SIGNAL_RTR = 0x08   # Ready To Receive
SIGNAL_IC  = 0x40   # Incoming Call (Ring Indicator)
SIGNAL_DV  = 0x80   # Data Valid
SIGNAL_EA  = 0x01   # Extension bit (always set in V.24 signals)

# Human-readable names for logging
SIGNAL_NAMES = [
    (SIGNAL_FC, 'FC'), (SIGNAL_RTC, 'RTC'), (SIGNAL_RTR, 'RTR'),
    (SIGNAL_IC, 'IC'), (SIGNAL_DV, 'DV'),
]


def decode_signals(signals: int) -> str:
    """Decode V.24 signal byte to human-readable string."""
    return '|'.join(n for b, n in SIGNAL_NAMES if signals & b) or 'none'


# CRC table, poly=0x07, reversed
CRC_TABLE = [
    0x00, 0x91, 0xE3, 0x72, 0x07, 0x96, 0xE4, 0x75,
    0x0E, 0x9F, 0xED, 0x7C, 0x09, 0x98, 0xEA, 0x7B,
    0x1C, 0x8D, 0xFF, 0x6E, 0x1B, 0x8A, 0xF8, 0x69,
    0x12, 0x83, 0xF1, 0x60, 0x15, 0x84, 0xF6, 0x67,
    0x38, 0xA9, 0xDB, 0x4A, 0x3F, 0xAE, 0xDC, 0x4D,
    0x36, 0xA7, 0xD5, 0x44, 0x31, 0xA0, 0xD2, 0x43,
    0x24, 0xB5, 0xC7, 0x56, 0x23, 0xB2, 0xC0, 0x51,
    0x2A, 0xBB, 0xC9, 0x58, 0x2D, 0xBC, 0xCE, 0x5F,
    0x70, 0xE1, 0x93, 0x02, 0x77, 0xE6, 0x94, 0x05,
    0x7E, 0xEF, 0x9D, 0x0C, 0x79, 0xE8, 0x9A, 0x0B,
    0x6C, 0xFD, 0x8F, 0x1E, 0x6B, 0xFA, 0x88, 0x19,
    0x62, 0xF3, 0x81, 0x10, 0x65, 0xF4, 0x86, 0x17,
    0x48, 0xD9, 0xAB, 0x3A, 0x4F, 0xDE, 0xAC, 0x3D,
    0x46, 0xD7, 0xA5, 0x34, 0x41, 0xD0, 0xA2, 0x33,
    0x54, 0xC5, 0xB7, 0x26, 0x53, 0xC2, 0xB0, 0x21,
    0x5A, 0xCB, 0xB9, 0x28, 0x5D, 0xCC, 0xBE, 0x2F,
    0xE0, 0x71, 0x03, 0x92, 0xE7, 0x76, 0x04, 0x95,
    0xEE, 0x7F, 0x0D, 0x9C, 0xE9, 0x78, 0x0A, 0x9B,
    0xFC, 0x6D, 0x1F, 0x8E, 0xFB, 0x6A, 0x18, 0x89,
    0xF2, 0x63, 0x11, 0x80, 0xF5, 0x64, 0x16, 0x87,
    0xD8, 0x49, 0x3B, 0xAA, 0xDF, 0x4E, 0x3C, 0xAD,
    0xD6, 0x47, 0x35, 0xA4, 0xD1, 0x40, 0x32, 0xA3,
    0xC4, 0x55, 0x27, 0xB6, 0xC3, 0x52, 0x20, 0xB1,
    0xCA, 0x5B, 0x29, 0xB8, 0xCD, 0x5C, 0x2E, 0xBF,
    0x90, 0x01, 0x73, 0xE2, 0x97, 0x06, 0x74, 0xE5,
    0x9E, 0x0F, 0x7D, 0xEC, 0x99, 0x08, 0x7A, 0xEB,
    0x8C, 0x1D, 0x6F, 0xFE, 0x8B, 0x1A, 0x68, 0xF9,
    0x82, 0x13, 0x61, 0xF0, 0x85, 0x14, 0x66, 0xF7,
    0xA8, 0x39, 0x4B, 0xDA, 0xAF, 0x3E, 0x4C, 0xDD,
    0xA6, 0x37, 0x45, 0xD4, 0xA1, 0x30, 0x42, 0xD3,
    0xB4, 0x25, 0x57, 0xC6, 0xB3, 0x22, 0x50, 0xC1,
    0xBA, 0x2B, 0x59, 0xC8, 0xBD, 0x2C, 0x5E, 0xCF,
]


# ============================================================
# CMUX frame construction
# ============================================================

def cmux_fcs(data: bytes) -> int:
    """calculate FCS (CRC-8)"""
    fcs = 0xFF
    for b in data:
        fcs = CRC_TABLE[(fcs ^ b) & 0xFF]
    return (0xFF - fcs) & 0xFF


def make_address(dlci: int, cr: int) -> int:
    """build address field: EA=1 | C/R | (DLCI << 2)"""
    return EA | (cr << 1) | (dlci << 2)


def make_frame(dlci: int, cr: int, frame_type: int, pf: int, data: bytes = b'') -> bytes:
    """build a CMUX frame"""
    addr = make_address(dlci, cr)
    ctrl = frame_type | (pf << 4)
    length = len(data)

    if length > 127:
        len_bytes = bytes([
            (length & 0x7F) << 1,      # lower 7 bits, EA=0 (more bytes follow)
            (length >> 7),             # upper bits, no EA (C-compatible)
        ])
    else:
        len_bytes = bytes([(length << 1) | 1])

    fcs = cmux_fcs(bytes([addr, ctrl]) + len_bytes)

    frame = bytes([CMUX_FLAG, addr, ctrl]) + len_bytes
    if data:
        frame += data
    frame += bytes([fcs, CMUX_FLAG])
    return frame


def make_sabm(dlci: int) -> bytes:
    """build SABM frame (P=1, TE→UE command)"""
    return make_frame(dlci, cr=1, frame_type=FrameType.SABM, pf=1)


def make_disc(dlci: int) -> bytes:
    """build DISC frame (P=1, TE→UE command)"""
    return make_frame(dlci, cr=1, frame_type=FrameType.DISC, pf=1)


def make_uih(dlci: int, cr: int, data: bytes) -> bytes:
    """build UIH data frame (P/F=0)"""
    return make_frame(dlci, cr=cr, frame_type=FrameType.UIH, pf=0, data=data)


def make_uih_cmd(dlci: int, data: bytes) -> bytes:
    """build UIH command frame (TE→UE, C/R=1)"""
    return make_uih(dlci, cr=1, data=data)


def make_cld() -> bytes:
    """build CLD (close CMUX) command"""
    msg = bytes([CtrlType.CLD | CR_BIT, 0x01])
    return make_uih_cmd(0, msg)


# ============================================================
# MSC (Modem Status Command) frame builders
# ============================================================

def make_msc_info(dlci: int, signals: int) -> bytes:
    """Build MSC information field: [dlci_addr(EA=1,CR=1), signals]."""
    return bytes([(dlci << 2) | CR_BIT | EA, signals])


def make_msc_cmd(dlci: int, signals: int) -> bytes:
    """Build MSC command frame (TE→UE, C/R=1)."""
    info = make_msc_info(dlci, signals)
    msg = bytes([CtrlType.MSC | CR_BIT, (len(info) << 1) | EA]) + info
    return make_uih_cmd(0, msg)


def make_msc_resp(info: bytes, pf: int = 0) -> bytes:
    """Build MSC response frame (C/R cleared in info[0], send as UIH on DLCI 0).

    This matches the C code behavior: the info field is sent directly as the
    UIH payload, with only the C/R bit cleared. No extra MSC header is added.

    Args:
        info: Original MSC info field from the modem's command.
              info[0] has C/R=1; this function clears it for the response.
        pf:   P/F bit — set to 1 if the modem's command had P/F=1.
    """
    resp = bytearray(info)
    resp[0] = resp[0] & ~CR_BIT  # clear C/R → response
    return make_frame(0, cr=1, frame_type=FrameType.UIH, pf=pf, data=bytes(resp))


def make_msc_fc(dlci: int, fc_on: bool) -> bytes:
    """Build MSC command to set flow control on a specific DLCI.

    Args:
        dlci:  Channel number.
        fc_on: True = tell modem to stop sending (FC=1),
               False = allow modem to send (FC=0).
    """
    signals = SIGNAL_EA
    if fc_on:
        signals |= SIGNAL_FC
    return make_msc_cmd(dlci, signals)


# ============================================================
# CMUX frame parser
# ============================================================

class FrameParser:
    """CMUX frame parser"""

    def __init__(self):
        self.buffer = bytearray()
        self.frames = deque()

    def feed(self, data: bytes):
        self.buffer.extend(data)
        self._parse()

    def _parse(self):
        while True:
            idx = self.buffer.find(CMUX_FLAG)
            if idx == -1:
                break
            if idx > 0:
                print(f"  [Discarded {idx} bytes non-frame data: {self.buffer[:idx].hex()}]")
            self.buffer = self.buffer[idx:]

            start = 1
            while start < len(self.buffer) and self.buffer[start] == CMUX_FLAG:
                start += 1

            # Minimum header: addr(1) + ctrl(1) + len(1) + fcs(1) + end_flag(1) = 5
            if len(self.buffer) - start < 5:
                break

            pos = start
            addr = self.buffer[pos]; pos += 1
            ctrl = self.buffer[pos]; pos += 1

            ea = addr & 0x01
            cr = (addr & 0x02) >> 1
            dlci = (addr & 0xFC) >> 2

            pf = (ctrl & 0x10) >> 4
            frame_type = ctrl & 0xEF

            # Parse length field — check EA bit BEFORE advancing pos
            length = (self.buffer[pos] & 0xFE) >> 1
            ea_len = self.buffer[pos] & 0x01
            if ea_len == 0:
                # 2-byte length: need addr(1) + ctrl(1) + len(2) + fcs(1) + end(1) = 6
                if len(self.buffer) - start < 6:
                    break
                pos += 1
                # byte 2 value directly * 128 (matches C code: *local_readp*128)
                length += self.buffer[pos] * 128
            pos += 1

            # Need remaining: payload(length) + fcs(1) + end_flag(1)
            if len(self.buffer) - pos < length + 2:
                break

            info = bytes(self.buffer[pos:pos + length])
            header_end = pos  # position right after length field(s)
            pos += length

            fcs_byte = self.buffer[pos]; pos += 1
            end_flag = self.buffer[pos]; pos += 1

            frame_raw = bytes(self.buffer[start:pos])

            # Extract header + length bytes for FCS verification
            # header = addr + ctrl, len_bytes = everything from length field start to header_end
            header = bytes([addr, ctrl])
            len_bytes = frame_raw[2:(header_end - start)]
            expected_fcs = cmux_fcs(header + len_bytes)

            if end_flag != CMUX_FLAG:
                print(f"  [frame tail error: expected 0xF9, got 0x{end_flag:02X}]")
            elif fcs_byte != expected_fcs:
                print(f"  [FCS error: expected 0x{expected_fcs:02X}, got 0x{fcs_byte:02X}]")
            else:
                self.frames.append({
                    'dlci': dlci,
                    'cr': cr,
                    'pf': pf,
                    'type': frame_type,
                    'info': info,
                    'raw': frame_raw,
                })

            self.buffer = self.buffer[pos:]

    def get_frame(self):
        if self.frames:
            return self.frames.popleft()
        return None