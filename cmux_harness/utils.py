"""ICMP / IP utility functions (checksum, packet builders)"""

import struct


def calc_checksum(data: bytes) -> int:
    """calculate 16-bit one's complement checksum"""
    if len(data) % 2 == 1:
        data += b'\x00'
    s = 0
    for i in range(0, len(data), 2):
        w = (data[i] << 8) | data[i + 1]
        s += w
    s = (s & 0xFFFF) + (s >> 16)
    s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def build_icmp_echo_request(ident: int, seq: int, payload: bytes = b'') -> bytes:
    """build ICMP Echo Request (type=8, code=0)"""
    icmp_type = 8
    icmp_code = 0
    icmp_hdr = struct.pack('!BBHHH', icmp_type, icmp_code, 0, ident, seq)
    csum = calc_checksum(icmp_hdr + payload)
    icmp_hdr = struct.pack('!BBHHH', icmp_type, icmp_code, csum, ident, seq)
    return icmp_hdr + payload


def build_ip_packet(src_ip: str, dst_ip: str, protocol: int, payload: bytes,
                    ident: int = 0, ttl: int = 64) -> bytes:
    """build IP packet (with checksum)"""
    src = bytes(int(b) for b in src_ip.split('.'))
    dst = bytes(int(b) for b in dst_ip.split('.'))
    total_len = 20 + len(payload)
    ver_ihl = 0x45
    ip_hdr = struct.pack('!BBHHHBBH4s4s',
                         ver_ihl, 0, total_len, ident, 0, ttl, protocol, 0, src, dst)
    csum = calc_checksum(ip_hdr)
    ip_hdr = struct.pack('!BBHHHBBH4s4s',
                         ver_ihl, 0, total_len, ident, 0, ttl, protocol, csum, src, dst)
    return ip_hdr + payload


def parse_icmp(data: bytes) -> dict | None:
    """parse ICMP message"""
    if len(data) < 8:
        return None
    icmp_type, icmp_code, icmp_csum, icmp_id, icmp_seq = \
        struct.unpack('!BBHHH', data[:8])
    return {
        'type': icmp_type,
        'code': icmp_code,
        'checksum': icmp_csum,
        'id': icmp_id,
        'seq': icmp_seq,
        'data': data[8:],
    }