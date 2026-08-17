"""FTP speed test service — upload/download speed testing through PPP."""

import os
import time
import ftplib
import socket
from typing import Optional, TYPE_CHECKING

from .base import ServiceInterface
from ..events import Event

if TYPE_CHECKING:
    from ..harness import CmuxHarness

BLOCK_SIZE = 256 * 1024  # 256KB


class FtpSpeedTester:
    """FTP speed test client — based on ftplib, for post-PPP speed testing."""

    def __init__(self, host: str = '', port: int = 21,
                 user: str = '', password: str = '', timeout: int = 30):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self.ftp = None
        self._progress_last = 0
        self._file_size = 0
        self._t_first = 0.0
        self._t_last = 0.0
        self._t_last_print = 0.0

    def connect(self):
        print(f"  [FTP] connecting {self.host}:{self.port} ...")
        self.ftp = ftplib.FTP()
        self.ftp.connect(self.host, self.port, timeout=self.timeout)
        print(f"  [FTP] ← {self.ftp.getwelcome()}")
        self.ftp.login(self.user, self.password)
        print(f"  [FTP] login successful (user: {self.user})")
        self.ftp.voidcmd('TYPE I')
        print(f"  [FTP] transfer mode: BINARY")

        self._fix_pasv()
        self._fix_socket_opts()

    def _fix_pasv(self):
        original_makepasv = self.ftp.makepasv

        def patched_makepasv():
            host, port = original_makepasv()
            if host != self.host:
                print(f"  [FTP] PASV server returned {host}:{port}, replacing with {self.host}:{port}")
            return (self.host, port)

        self.ftp.makepasv = patched_makepasv

    def _fix_socket_opts(self):
        original_transfercmd = self.ftp.transfercmd

        def patched_transfercmd(cmd, rest=None):
            conn = original_transfercmd(cmd, rest)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
            return conn

        self.ftp.transfercmd = patched_transfercmd

    def close(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            self.ftp = None
            print(f"  [FTP] connection closed")

    def _progress_callback(self, block: bytes):
        transferred = self._progress_last + len(block)
        self._progress_last = transferred

        if self._t_first == 0.0:
            self._t_first = time.perf_counter()
            self._t_last = self._t_first
            self._t_last_print = 0.0
        else:
            self._t_last = time.perf_counter()

        now = self._t_last
        if now - self._t_last_print >= 0.1 or transferred >= self._file_size:
            self._t_last_print = now
            if self._t_first > 0 and self._file_size > 0:
                elapsed = self._t_last - self._t_first
                if elapsed > 0:
                    pct = transferred / self._file_size * 100
                    speed = transferred / elapsed
                    print(f"\r  [FTP] progress: {transferred}/{self._file_size} "
                          f"({pct:.1f}%)  {self._format_speed(speed)}    ", end='')

    def _format_speed(self, bytes_per_sec: float) -> str:
        mbps = bytes_per_sec * 8 / 1_000_000
        return f"{mbps:.2f} Mbps"

    def _format_size(self, size: int) -> str:
        if size >= 1_000_000:
            return f"{size / 1_000_000:.2f} MB"
        elif size >= 1_000:
            return f"{size / 1_000:.2f} KB"
        else:
            return f"{size} B"

    def _print_result(self, direction: str, file_size: int):
        if self._t_first == 0.0:
            print(f"\n  [FTP] ✗ no data received")
            return

        data_elapsed = self._t_last - self._t_first
        data_speed = file_size / data_elapsed if data_elapsed > 0 else 0

        print(f"\n  [FTP] ✓ {direction} complete!")
        print(f"  [FTP]   file size: {self._format_size(file_size)}")
        print(f"  [FTP]   elapsed: {data_elapsed:.2f}s,  "
              f"speed: {self._format_speed(data_speed)}")

    def upload(self, local_path: str, remote_path: str) -> dict:
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"local file not found: {local_path}")

        self._file_size = os.path.getsize(local_path)
        self._progress_last = 0
        self._t_first = 0.0

        print(f"  [FTP] ↑ upload: {local_path} → {remote_path}")
        print(f"  [FTP]   file size: {self._format_size(self._file_size)}")

        with open(local_path, 'rb') as f:
            self.ftp.storbinary(f'STOR {remote_path}', f,
                                callback=self._progress_callback,
                                blocksize=BLOCK_SIZE)

        self._print_result('upload', self._file_size)

        data_elapsed = self._t_last - self._t_first if self._t_first > 0 else 0
        return {
            'direction': 'upload',
            'file_size': self._file_size,
            'elapsed': data_elapsed,
            'speed': self._file_size / data_elapsed if data_elapsed > 0 else 0,
        }

    def download(self, remote_path: str, local_path: str) -> dict:
        try:
            self._file_size = self.ftp.size(remote_path)
            if self._file_size is None:
                self._file_size = 0
        except Exception:
            self._file_size = 0

        self._progress_last = 0
        self._t_first = 0.0
        self._t_last = 0.0

        print(f"  [FTP] ↓ download: {remote_path} → {local_path}")
        if self._file_size > 0:
            print(f"  [FTP]   file size: {self._format_size(self._file_size)}")

        with open(local_path, 'wb') as f:
            self.ftp.retrbinary(f'RETR {remote_path}',
                                lambda block: (f.write(block), self._progress_callback(block)),
                                blocksize=BLOCK_SIZE)

        actual_size = os.path.getsize(local_path)
        self._print_result('download', actual_size)

        data_elapsed = self._t_last - self._t_first if self._t_first > 0 else 0
        return {
            'direction': 'download',
            'file_size': actual_size,
            'elapsed': data_elapsed,
            'speed': actual_size / data_elapsed if data_elapsed > 0 else 0,
        }


class FtpService(ServiceInterface):
    """FTP speed test service — upload/download testing through PPP."""

    name = 'ftp'
    commands = ['ftp']

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._harness: Optional['CmuxHarness'] = None
        self._ppp_ready = False
        self._tester: Optional[FtpSpeedTester] = None

    def on_register(self, harness: 'CmuxHarness') -> None:
        self._harness = harness
        harness.events.subscribe(Event.PPP_IPCP_UP, self._on_ppp_up)
        harness.events.subscribe(Event.PPP_DISCONNECTING, self._on_ppp_down)

    def on_command(self, args: list[str]) -> Optional[str]:
        if not self._ppp_ready:
            print("  PPP未连接，请先执行 ppp 建立连接")
            return None

        subcmd = args[1] if len(args) > 1 else 'help'

        if subcmd == 'config':
            return self._cmd_config(args[2:])
        elif subcmd in ('upload', 'download'):
            if not self._harness.state.ftp_host:
                print("  FTP not configured. Set it first:")
                print("    ftp config host <host>")
                print("    ftp config user <user>")
                print("    ftp config pass <password>")
                return None
            return self._cmd_upload(args[2:]) if subcmd == 'upload' else self._cmd_download(args[2:])
        elif subcmd == 'help' or subcmd == '-h':
            print("  ftp upload <local> <remote>  Upload file")
            print("  ftp download <remote> <local>  Download file")
            print("  ftp config host <host>     Set FTP server host")
            print("  ftp config port <port>     Set FTP server port")
            print("  ftp config user <user>     Set FTP username")
            print("  ftp config pass <pass>     Set FTP password")
            return None
        else:
            print(f"  Unknown ftp subcommand: {subcmd}")
            return None

    def on_shutdown(self) -> None:
        if self._tester:
            self._tester.close()
            self._tester = None
        self._harness.events.unsubscribe(Event.PPP_IPCP_UP, self._on_ppp_up)
        self._harness.events.unsubscribe(Event.PPP_DISCONNECTING, self._on_ppp_down)

    def _on_ppp_up(self, **kwargs):
        self._ppp_ready = True
        # Add route for FTP server
        ppp_service = self._harness.get_service('ppp')
        if ppp_service:
            ftp_host = self._harness.state.ftp_host
            if ftp_host:
                ppp_service.add_route(ftp_host)

    def _on_ppp_down(self, **kwargs):
        self._ppp_ready = False
        if self._tester:
            self._tester.close()
            self._tester = None

    def _cmd_config(self, args: list[str]) -> Optional[str]:
        if len(args) < 2:
            state = self._harness.state
            print(f"  FTP config:")
            print(f"    host: {state.ftp_host}")
            print(f"    port: {state.ftp_port}")
            print(f"    user: {state.ftp_user}")
            print(f"    pass: {'***' if state.ftp_password else '(empty)'}")
            return None

        key, value = args[0], args[1]
        if key == 'host':
            self._harness.state.update(ftp_host=value)
            print(f"  FTP host = {value}")
        elif key == 'port':
            self._harness.state.update(ftp_port=int(value))
            print(f"  FTP port = {value}")
        elif key == 'user':
            self._harness.state.update(ftp_user=value)
            print(f"  FTP user = {value}")
        elif key == 'pass':
            self._harness.state.update(ftp_password=value)
            print(f"  FTP password updated")
        else:
            print(f"  Unknown config key: {key}")
        return None

    def _ensure_tester(self) -> Optional[FtpSpeedTester]:
        state = self._harness.state
        if self._tester and self._tester.ftp:
            try:
                self._tester.ftp.voidcmd('NOOP')
                return self._tester
            except Exception:
                self._tester.close()
                self._tester = None

        self._tester = FtpSpeedTester(
            host=state.ftp_host,
            port=state.ftp_port,
            user=state.ftp_user,
            password=state.ftp_password,
        )
        try:
            self._tester.connect()
            return self._tester
        except Exception as e:
            print(f"  [FTP] connection failed: {e}")
            self._tester = None
            return None

    def _cmd_upload(self, args: list[str]) -> Optional[str]:
        if len(args) < 2:
            print("  Usage: ftp upload <local_path> <remote_path>")
            return None

        tester = self._ensure_tester()
        if not tester:
            return None

        try:
            tester.upload(args[0], args[1])
        except Exception as e:
            print(f"  [FTP] upload error: {e}")
        return None

    def _cmd_download(self, args: list[str]) -> Optional[str]:
        if len(args) < 2:
            print("  Usage: ftp download <remote_path> <local_path>")
            return None

        tester = self._ensure_tester()
        if not tester:
            return None

        try:
            tester.download(args[0], args[1])
        except Exception as e:
            print(f"  [FTP] download error: {e}")
        return None