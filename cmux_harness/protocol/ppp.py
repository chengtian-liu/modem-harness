"""PPP protocol constants, FCS, byte-stuffing, and frame parser (RFC 1661/1332)"""

import struct

# ============================================================
# PPP protocol constants
# ============================================================

PPP_FLAG = 0x7E
PPP_ADDR = 0xFF
PPP_CTRL = 0x03

# PPP protocol numbers
PPP_IP   = 0x0021
PPP_LCP  = 0xC021
PPP_IPCP = 0x8021
PPP_PAP  = 0xC023
PPP_CHAP = 0xC223
PPP_IPV6CP = 0x8057

# LCP option types
LCP_MRU      = 1
LCP_ACCM     = 2
LCP_AUTH     = 3
LCP_MAGIC    = 5
LCP_PROTOCOMP = 7
LCP_ACFC     = 8

# LCP codes
LCP_CONF_REQ = 1
LCP_CONF_ACK = 2
LCP_CONF_NAK = 3
LCP_CONF_REJ = 4
LCP_TERM_REQ = 5
LCP_TERM_ACK = 6
LCP_CODE_REJ = 7
LCP_ECHO_REQ = 9
LCP_ECHO_REP = 10

# IPCP codes
IPCP_CONF_REQ = 1
IPCP_CONF_ACK = 2
IPCP_CONF_NAK = 3
IPCP_CONF_REJ = 4

# IPCP options
IPCP_IPADDR = 3
IPCP_PRIDNS = 129
IPCP_SECDNS = 131

# PAP codes
PAP_REQ = 1
PAP_ACK = 2
PAP_NAK = 3

# IPv6CP
IPV6CP_IFACE_ID = 1


# ============================================================
# PPP FCS lookup table
# ============================================================

PPP_FCS_TABLE = [
    0x0000, 0x1189, 0x2312, 0x329b, 0x4624, 0x57ad, 0x6536, 0x74bf,
    0x8c48, 0x9dc1, 0xaf5a, 0xbed3, 0xca6c, 0xdbe5, 0xe97e, 0xf8f7,
    0x1081, 0x0108, 0x3393, 0x221a, 0x56a5, 0x472c, 0x75b7, 0x643e,
    0x9cc9, 0x8d40, 0xbfdb, 0xae52, 0xdaed, 0xcb64, 0xf9ff, 0xe876,
    0x2102, 0x308b, 0x0210, 0x1399, 0x6726, 0x76af, 0x4434, 0x55bd,
    0xad4a, 0xbcc3, 0x8e58, 0x9fd1, 0xeb6e, 0xfae7, 0xc87c, 0xd9f5,
    0x3183, 0x200a, 0x1291, 0x0318, 0x77a7, 0x662e, 0x54b5, 0x453c,
    0xbdcb, 0xac42, 0x9ed9, 0x8f50, 0xfbef, 0xea66, 0xd8fd, 0xc974,
    0x4204, 0x538d, 0x6116, 0x709f, 0x0420, 0x15a9, 0x2732, 0x36bb,
    0xce4c, 0xdfc5, 0xed5e, 0xfcd7, 0x8868, 0x99e1, 0xab7a, 0xbaf3,
    0x5285, 0x430c, 0x7197, 0x601e, 0x14a1, 0x0528, 0x37b3, 0x263a,
    0xdecd, 0xcf44, 0xfddf, 0xec56, 0x98e9, 0x8960, 0xbbfb, 0xaa72,
    0x6306, 0x728f, 0x4014, 0x519d, 0x2522, 0x34ab, 0x0630, 0x17b9,
    0xef4e, 0xfec7, 0xcc5c, 0xddd5, 0xa96a, 0xb8e3, 0x8a78, 0x9bf1,
    0x7387, 0x620e, 0x5095, 0x411c, 0x35a3, 0x242a, 0x16b1, 0x0738,
    0xffcf, 0xee46, 0xdcdd, 0xcd54, 0xb9eb, 0xa862, 0x9af9, 0x8b70,
    0x8408, 0x9581, 0xa71a, 0xb693, 0xc22c, 0xd3a5, 0xe13e, 0xf0b7,
    0x0840, 0x19c9, 0x2b52, 0x3adb, 0x4e64, 0x5fed, 0x6d76, 0x7cff,
    0x9489, 0x8500, 0xb79b, 0xa612, 0xd2ad, 0xc324, 0xf1bf, 0xe036,
    0x18c1, 0x0948, 0x3bd3, 0x2a5a, 0x5ee5, 0x4f6c, 0x7df7, 0x6c7e,
    0xa50a, 0xb483, 0x8618, 0x9791, 0xe32e, 0xf2a7, 0xc03c, 0xd1b5,
    0x2942, 0x38cb, 0x0a50, 0x1bd9, 0x6f66, 0x7eef, 0x4c74, 0x5dfd,
    0xb58b, 0xa402, 0x9699, 0x8710, 0xf3af, 0xe226, 0xd0bd, 0xc134,
    0x39c3, 0x284a, 0x1ad1, 0x0b58, 0x7fe7, 0x6e6e, 0x5cf5, 0x4d7c,
    0xc60c, 0xd785, 0xe51e, 0xf497, 0x8028, 0x91a1, 0xa33a, 0xb2b3,
    0x4a44, 0x5bcd, 0x6956, 0x78df, 0x0c60, 0x1de9, 0x2f72, 0x3efb,
    0xd68d, 0xc704, 0xf59f, 0xe416, 0x90a9, 0x8120, 0xb3bb, 0xa232,
    0x5ac5, 0x4b4c, 0x79d7, 0x685e, 0x1ce1, 0x0d68, 0x3ff3, 0x2e7a,
    0xe70e, 0xf687, 0xc41c, 0xd595, 0xa12a, 0xb0a3, 0x8238, 0x93b1,
    0x6b46, 0x7acf, 0x4854, 0x59dd, 0x2d62, 0x3ceb, 0x0e70, 0x1ff9,
    0xf78f, 0xe606, 0xd49d, 0xc514, 0xb1ab, 0xa022, 0x92b9, 0x8330,
    0x7bc7, 0x6a4e, 0x58d5, 0x495c, 0x3de3, 0x2c6a, 0x1ef1, 0x0f78
]


# ============================================================
# PPP FCS and byte-stuffing
# ============================================================

def ppp_fcs(data: bytes) -> int:
    """calculate PPP FCS"""
    fcs = 0xFFFF
    for b in data:
        fcs = (fcs >> 8) ^ PPP_FCS_TABLE[(fcs ^ b) & 0xFF]
    return fcs ^ 0xFFFF


def ppp_escape(data: bytes) -> bytes:
    """PPP byte stuffing (escape 0x00-0x1F, 0x7D, 0x7E)"""
    result = bytearray()
    for b in data:
        if b <= 0x1F or b == 0x7D or b == 0x7E:
            result.extend([0x7D, b ^ 0x20])
        else:
            result.append(b)
    return bytes(result)


def ppp_unescape(data: bytes) -> bytes:
    """PPP byte unstuffing"""
    result = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x7D and i + 1 < len(data):
            result.append(data[i + 1] ^ 0x20)
            i += 2
        else:
            result.append(b)
            i += 1
    return bytes(result)


def make_ppp_frame(protocol: int, info: bytes, acfc: bool = False, pfc: bool = False) -> bytes:
    """build PPP frame, supports ACFC and PFC"""
    if acfc:
        if pfc and (protocol & 0xFF00) == 0:
            proto_bytes = bytes([protocol & 0xFF])
        else:
            proto_bytes = struct.pack('!H', protocol)
        payload = proto_bytes + info
        fcs = ppp_fcs(payload)
        fcs_bytes = struct.pack('<H', fcs)
        body = ppp_escape(payload + fcs_bytes)
    else:
        payload = struct.pack('!BBH', PPP_ADDR, PPP_CTRL, protocol) + info
        fcs = ppp_fcs(payload)
        fcs_bytes = struct.pack('<H', fcs)
        body = ppp_escape(payload + fcs_bytes)
    return bytes([PPP_FLAG]) + body + bytes([PPP_FLAG])


# ============================================================
# PPP frame parser
# ============================================================

class PppParser:
    """PPP frame parser"""

    def __init__(self, verbose: bool = False):
        self.buffer = bytearray()
        self.frames = []
        self.verbose = verbose
        self._peer_acfc = False
        self._peer_pfc = False

    def set_peer_compression(self, acfc: bool = False, pfc: bool = False):
        self._peer_acfc = acfc
        self._peer_pfc = pfc

    def feed(self, data: bytes):
        had_pending = len(self.buffer) > 0
        self.buffer.extend(data)
        self._parse()
        if self.verbose and len(self.buffer) > 0:
            print(f"  [PPP] ← fragmented frame: +{len(data)}B, buffered {len(self.buffer)}B")
        elif self.verbose and had_pending:
            print(f"  [PPP] ← fragmented frame: +{len(data)}B, reassembled")

    def _parse(self):
        while True:
            try:
                start = self.buffer.index(PPP_FLAG)
            except ValueError:
                self.buffer.clear()
                return

            try:
                end = self.buffer.index(PPP_FLAG, start + 1)
            except ValueError:
                if start > 0:
                    del self.buffer[:start]
                return

            frame_data = self.buffer[start + 1:end]
            del self.buffer[:end + 1]

            if len(frame_data) < 2:
                continue

            frame_data = ppp_unescape(frame_data)

            if len(frame_data) < 2:
                continue

            if len(frame_data) >= 2 and frame_data[0] == PPP_ADDR and frame_data[1] == PPP_CTRL:
                if len(frame_data) < 6:
                    continue
                addr = frame_data[0]
                ctrl = frame_data[1]
                protocol = (frame_data[2] << 8) | frame_data[3]
                info = frame_data[4:-2]
            else:
                addr = PPP_ADDR
                ctrl = PPP_CTRL
                if frame_data[0] & 1:
                    protocol = frame_data[0]
                    info = frame_data[1:-2]
                else:
                    if len(frame_data) < 4:
                        continue
                    protocol = (frame_data[0] << 8) | frame_data[1]
                    info = frame_data[2:-2]

            fcs_received = (frame_data[-2] | (frame_data[-1] << 8))
            fcs_calc = ppp_fcs(frame_data[:-2])
            if fcs_calc != fcs_received:
                continue

            self.frames.append({
                'addr': addr,
                'ctrl': ctrl,
                'protocol': protocol,
                'info': info,
            })

    def get_frame(self):
        if self.frames:
            return self.frames.pop(0)
        return None