"""iperf3 speed test service — TCP/UDP throughput testing through PPP."""

import os
import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from .base import ServiceInterface
from ..events import Event

if TYPE_CHECKING:
    from ..harness import CmuxHarness


class IperfService(ServiceInterface):
    """iperf3 speed test service — TCP throughput testing through PPP."""

    name = 'iperf3'
    commands = ['iperf3']

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._harness: Optional['CmuxHarness'] = None
        self._ppp_ready = False

    def on_register(self, harness: 'CmuxHarness') -> None:
        self._harness = harness
        harness.events.subscribe(Event.PPP_IPCP_UP, self._on_ppp_up)
        harness.events.subscribe(Event.PPP_DISCONNECTING, self._on_ppp_down)

    def on_command(self, args: list[str]) -> Optional[str]:
        if not self._ppp_ready:
            print("  PPP未连接，请先执行 ppp 建立连接")
            return None

        server_ip = '192.168.10.1'
        port = 5201
        duration = 10

        i = 1
        while i < len(args):
            if args[i] == '-p' and i + 1 < len(args):
                port = int(args[i + 1])
                i += 2
            elif args[i] == '-t' and i + 1 < len(args):
                duration = int(args[i + 1])
                i += 2
            elif not args[i].startswith('-'):
                server_ip = args[i]
                i += 1
            else:
                i += 1

        self._run_iperf3(server_ip, port, duration)
        return None

    def on_shutdown(self) -> None:
        self._harness.events.unsubscribe(Event.PPP_IPCP_UP, self._on_ppp_up)
        self._harness.events.unsubscribe(Event.PPP_DISCONNECTING, self._on_ppp_down)

    def _on_ppp_up(self, **kwargs):
        self._ppp_ready = True

    def _on_ppp_down(self, **kwargs):
        self._ppp_ready = False

    def _run_iperf3(self, server_ip: str, port: int = 5201, duration: int = 10):
        from cmux_harness.iperf3.iperf3_client import Iperf3Client
        from cmux_harness.iperf3.utils import setup_logging
        from cmux_harness.iperf3.iperf3_api import Iperf3TestProto

        logger = logging.getLogger('cmux_harness.iperf3')
        if not logger.handlers:
            setup_logging(debug=False, log_filename=None)

        ppp_service = self._harness.get_service('ppp')
        if ppp_service:
            ppp_service.add_route(server_ip)

        loop = None
        client = None
        try:
            print(f"\n  [iperf3] connecting to {server_ip}:{port}, duration={duration}s")

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            client = Iperf3Client(loop=loop)

            params = {
                'server_address': server_ip,
                'server_port': port,
                'test_duration': duration,
                'reverse': False,
                'parallel': 1,
                'test_protocol': Iperf3TestProto.TCP,
                'protocol': 'TCP',
                'server': False,
                'one_off': False,
                'client_port': None,
                'client_address': None,
                'ip_version': None,
                'debug': False,
                'log_filename': None,
                'blockcount': None,
                'no_delay': False,
                'title': None,
                'get_server_output': False,
                'window': None,
                'bytes': None,
                'udp64bitcounters': None,
                'block_size': None,
                'report_interval': 1.0,
            }

            client.create_test(test_parameters=params)

            # Ensure the loop doesn't run forever (connection timeout + test duration + margin)
            max_runtime = duration + 15
            loop.call_later(max_runtime, loop.stop)

            if os.name == 'nt':
                def wakeup():
                    loop.call_later(0.5, wakeup)
                loop.call_later(0.5, wakeup)

            try:
                loop.call_soon(client.run_all_tests)
                loop.run_forever()
            except KeyboardInterrupt:
                client.stop_all_tests()
                print("\n  [iperf3] interrupted by user")
            finally:
                loop.close()

        except Exception as e:
            print(f"  [iperf3] error: {e}")
            if loop and not loop.is_closed():
                loop.close()
        finally:
            if ppp_service:
                ppp_service.del_route(server_ip)