"""TUN virtual adapter — route system TCP/IP traffic through PPP serial.
Requires wintun.dll (https://www.wintun.net/)"""

import os
import sys
import ctypes
import subprocess
from ctypes import wintypes, c_void_p, c_wchar_p, c_uint32, c_uint64


# Windows API for efficient TUN wait
_kernel32 = ctypes.WinDLL('kernel32.dll')
_kernel32.WaitForSingleObject.restype = c_uint32
_kernel32.WaitForSingleObject.argtypes = [c_void_p, c_uint32]
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF


class TunAdapter:
    """TUN virtual adapter — created via wintun, routes system socket traffic through PPP serial"""

    def __init__(self, ip: str, netmask: str = '255.255.255.255', mtu: int = 1500,
                 dns_servers: list[str] | None = None):
        self.ip = ip
        self.netmask = netmask
        self.mtu = mtu
        self.dns_servers = dns_servers or []
        self._adapter = None
        self._session = None
        self._dll = None
        self._read_event = None
        self._running = False
        self._adapter_name = 'LTEcat1_TUN'

    def _load_dll(self):
        """load wintun.dll (supports PyInstaller bundle and local run)"""
        if getattr(sys, 'frozen', False):
            search_dirs = [sys._MEIPASS, os.path.dirname(sys.executable)]
        else:
            search_dirs = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]

        for d in search_dirs:
            path = os.path.join(d, 'wintun.dll')
            if os.path.isfile(path):
                self._setup_api(ctypes.WinDLL(path))
                return
        self._setup_api(ctypes.WinDLL('wintun.dll'))

    def _setup_api(self, dll):
        """set up wintun API function signatures"""
        self._dll = dll
        dll.WintunCreateAdapter.restype = c_void_p
        dll.WintunCreateAdapter.argtypes = [c_wchar_p, c_wchar_p, c_void_p]
        dll.WintunCloseAdapter.restype = None
        dll.WintunCloseAdapter.argtypes = [c_void_p]
        dll.WintunStartSession.restype = c_void_p
        dll.WintunStartSession.argtypes = [c_void_p, c_uint32]
        dll.WintunEndSession.restype = None
        dll.WintunEndSession.argtypes = [c_void_p]
        dll.WintunGetReadWaitEvent.restype = c_void_p
        dll.WintunGetReadWaitEvent.argtypes = [c_void_p]
        dll.WintunAllocateSendPacket.restype = c_void_p
        dll.WintunAllocateSendPacket.argtypes = [c_void_p, c_uint32]
        dll.WintunSendPacket.restype = None
        dll.WintunSendPacket.argtypes = [c_void_p, c_void_p]
        dll.WintunReceivePacket.restype = c_void_p
        dll.WintunReceivePacket.argtypes = [c_void_p, ctypes.POINTER(c_uint32)]
        dll.WintunReleaseReceivePacket.restype = None
        dll.WintunReleaseReceivePacket.argtypes = [c_void_p, c_void_p]

    def create(self):
        """create TUN adapter and configure IP"""
        self._load_dll()
        self._adapter = self._dll.WintunCreateAdapter(
            self._adapter_name, 'TUN', None
        )
        if not self._adapter:
            raise OSError("WintunCreateAdapter Failed (check: 1) run as admin 2) wintun.dll exists)")

        subprocess.run([
            'netsh', 'interface', 'ip', 'set', 'address',
            self._adapter_name, 'static', self.ip, self.netmask,
        ], check=True, capture_output=True, text=True)
        print(f"  [TUN] adapter {self._adapter_name} created, IP={self.ip}")

        if self.dns_servers:
            for i, dns in enumerate(self.dns_servers):
                if dns and dns != '0.0.0.0':
                    idx_arg = [] if i == 0 else [f'index={i+1}']
                    subprocess.run([
                        'netsh', 'interface', 'ip', 'set', 'dns',
                        self._adapter_name, 'static', dns,
                    ] + idx_arg, check=True, capture_output=True, text=True)
            print(f"  [TUN] DNS: {', '.join(self.dns_servers)}")

        self._session = self._dll.WintunStartSession(self._adapter, 1024 * 1024)
        if not self._session:
            raise OSError("WintunStartSession Failed")
        self._read_event = self._dll.WintunGetReadWaitEvent(self._session)
        self._running = True

    def read(self) -> bytes | None:
        """read one IP packet from TUN (non-blocking)"""
        size = c_uint32(0)
        ptr = self._dll.WintunReceivePacket(self._session, ctypes.byref(size))
        if not ptr or size.value == 0:
            return None
        data = ctypes.string_at(ptr, size.value)
        self._dll.WintunReleaseReceivePacket(self._session, ptr)
        return data

    def write(self, packet):
        """write one IP packet to TUN (bytes/memoryview/bytearray)"""
        if not self._session:
            return
        mv = memoryview(packet) if not isinstance(packet, memoryview) else packet
        pkt_len = mv.nbytes
        ptr = self._dll.WintunAllocateSendPacket(self._session, pkt_len)
        if not ptr:
            print(f"  [TUN] send buffer full, packet dropped")
            return
        try:
            src = (ctypes.c_char * pkt_len).from_buffer(mv)
        except (TypeError, BufferError):
            src = bytes(packet)
        ctypes.memmove(ptr, src, pkt_len)
        self._dll.WintunSendPacket(self._session, ptr)

    def get_read_event(self):
        """return read event handle for select/wait"""
        return self._read_event

    def close(self):
        """close TUN adapter"""
        self._running = False
        if self._session:
            self._dll.WintunEndSession(self._session)
            self._session = None
        if self._adapter:
            self._dll.WintunCloseAdapter(self._adapter)
            self._adapter = None
            print(f"  [TUN] adapter closed")