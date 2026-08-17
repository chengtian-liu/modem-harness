#!/usr/bin/env python3
"""
CMUX Multiplex Terminal — AT commands + PPP dialup (CMUX or Direct Serial)

Harness architecture: transport layer (CMUX/Serial) and application layer (AT/PPP/Ping/FTP/iperf3)
are completely decoupled. Each application module is independently registered via
harness.register(Service()). Adding a new service (e.g., HTTP testing) requires only
creating a new ServiceInterface class and registering it with one line.

Usage:
  python cmux_terminal.py                              # Interactive mode
  python cmux_terminal.py COM11 115200                 # CMUX mode
  python cmux_terminal.py COM11 115200 --serial        # Direct Serial mode
  python cmux_terminal.py COM11 115200 --verbose
  python cmux_terminal.py COM11 115200 --frame-size 512
"""

import sys
import os
import time
import ctypes
import argparse
import threading

# ---- prompt_toolkit optional dependency ----
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout as _pt_patch_stdout
    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

# ---- cmux_harness ----
from cmux_harness import (
    CmuxHarness, SharedState, Event, EventBus, ServiceInterface,
    AtService, AtChannel, PppService, PingService, FtpService, FtpSpeedTester, IperfService,
)
from cmux_harness.services.at import AT_COMMANDS


# ============================================================
# Internal commands for tab completion
# ============================================================

INTERNAL_COMMANDS = [
    "at", "ppp", "ppp stop", "ping", "ping stop",
    "ftp", "ftp upload", "ftp download", "ftp config",
    "iperf3",
    "quit", "help",
]


# ============================================================
# Tab completion
# ============================================================

_at_completer = None
_at_session = None

if _HAS_PROMPT_TOOLKIT:
    import re as _re

    class CmuxCompleter(Completer):
        """CMUX terminal custom completer — handles DLCI prefix and at/ftp prefix."""

        def __init__(self, at_commands, internal_commands):
            self.at_commands = at_commands
            self.internal = internal_commands

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text:
                return

            # 1. "1>AT+CSQ" format — DLCI prefix + AT command
            m = _re.match(r'^(\d+>)\s*(.*)', text)
            if m:
                prefix = m.group(1) + ' '
                word = m.group(2)
                for cmd in self.at_commands:
                    if word.lower() in cmd.lower():
                        yield Completion(
                            prefix + cmd,
                            start_position=-len(word),
                            display=prefix + cmd,
                        )
                return

            # 2. "at AT+CSQ" format — at prefix + AT command
            m = _re.match(r'^(at|AT)\s+(.*)', text)
            if m:
                prefix = m.group(1) + ' '
                word = m.group(2)
                for cmd in self.at_commands:
                    if word.lower() in cmd.lower():
                        yield Completion(
                            prefix + cmd,
                            start_position=-len(word),
                            display=prefix + cmd,
                        )
                return

            # 3. "ftp upload" format — ftp prefix + subcommand
            m = _re.match(r'^(ftp)\s+(\S*)', text)
            if m:
                prefix = 'ftp '
                word = m.group(2)
                sub_cmds = ['upload', 'download', 'config']
                for cmd in sub_cmds:
                    if word.lower() in cmd.lower():
                        yield Completion(
                            prefix + cmd,
                            start_position=-len(word),
                            display=prefix + cmd,
                        )
                return

            # 4. no prefix — match all commands (internal + AT)
            word = text
            for cmd in self.internal + self.at_commands:
                if word.lower() in cmd.lower():
                    yield Completion(
                        cmd,
                        start_position=-len(word),
                        display=cmd,
                    )


def _get_at_completer():
    global _at_completer
    if _at_completer is None:
        _at_completer = CmuxCompleter(AT_COMMANDS, INTERNAL_COMMANDS)
    return _at_completer


def _get_prompt_session() -> PromptSession:
    global _at_session
    if _at_session is None:
        history_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     '.cmux_terminal_history')
        _at_session = PromptSession(
            history=FileHistory(history_path),
            completer=_get_at_completer(),
            complete_while_typing=False,
        )
    return _at_session


def _input_with_completion(prompt: str = "> ") -> str:
    if not _HAS_PROMPT_TOOLKIT:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise
    try:
        with _pt_patch_stdout():
            return _get_prompt_session().prompt(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise


# ============================================================
# Auto-elevation
# ============================================================

def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _ensure_admin():
    if _is_admin():
        return

    print("[Elevate] Not running as admin, requesting admin privileges...")
    cwd = os.getcwd()

    if getattr(sys, 'frozen', False):
        exe_path = os.path.abspath(sys.executable)
        params = ' '.join(f'"{a}"' if ' ' in a else a for a in sys.argv[1:])
    else:
        exe_path = sys.executable
        script = os.path.abspath(sys.argv[0])
        raw_params = ' '.join(f'"{a}"' if ' ' in a else a for a in sys.argv[1:])
        params = f'"{script}" {raw_params}'

    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe_path, params, cwd, 1,
    )
    if ret <= 32:
        error_codes = {
            2: "File not found", 3: "Path not found",
            5: "Access denied (user cancelled UAC prompt)",
            8: "Out of memory", 32: "DLL not found",
        }
        msg = error_codes.get(ret, f"error code {ret}")
        print(f"[Elevate] Failed: {msg}")
        input("Press Enter to close...")
    sys.exit(0)


# ============================================================
# Interactive port selection
# ============================================================

def _interactive_setup(is_first_run: bool = True):
    import serial.tools.list_ports as list_ports

    if is_first_run:
        print("=" * 60)
        print("  CMUX Multiplex Terminal — AT commands + PPP dialup")
        print("  Copyright (c) 2026 chengtian.liu")
        print("  Author: chengtian.liu")
        print("=" * 60)
        print()
    else:
        print("\n" + "=" * 60)
        print("  Back to main menu")
        print("=" * 60)
        print()

    ports = [p for p in sorted(list_ports.comports(), key=lambda p: p.device)
              if p.device and (p.device.upper().startswith('COM') or p.device.startswith('/dev/'))]
    if not ports:
        print("  no available serial ports detected!")
        port = input("  please enter serial port manually (e.g. COM11): ").strip()
        if not port:
            print("no serial port entered, press Enter to exit...")
            input()
            sys.exit(1)
    else:
        print("  Available serial ports:")
        for i, p in enumerate(ports):
            desc = p.description or p.name or ''
            hwid = p.hwid or ''
            print(f"    [{i}]  {p.device}  -  {desc}  ({hwid})" if hwid else f"    [{i}]  {p.device}  -  {desc}")

        print()
        port = None
        while port is None:
            choice = input(f"  Select serial port [0-{len(ports)-1}] or enter serial port directly: ").strip()
            if not choice:
                print("  no serial port selected, press Enter to exit...")
                input()
                sys.exit(1)
            try:
                idx = int(choice)
                if 0 <= idx < len(ports):
                    port = ports[idx].device
                else:
                    print(f"  index out of range 0-{len(ports)-1}, please re-enter")
            except ValueError:
                port = choice

    baudrate = None
    while baudrate is None:
        baud_str = input("  enter baud rate (default 115200): ").strip()
        if not baud_str:
            baudrate = 115200
        else:
            try:
                baudrate = int(baud_str)
            except ValueError:
                print(f"  invalid baud rate: {baud_str}, please re-enter")

    frame_size = None
    while frame_size is None:
        frame_str = input("  enter CMUX max frame size N1 (default 0=auto, e.g. 128/256/512/1024/1500): ").strip()
        if not frame_str:
            frame_size = 0
        else:
            try:
                frame_size = int(frame_str)
            except ValueError:
                print(f"  invalid frame size: {frame_str}, please re-enter")

    mode = 'cmux'
    mode_choice = input("  Select mode: 1) CMUX (default)  2) Direct Serial: ").strip()
    if mode_choice == '2':
        mode = 'serial'

    return argparse.Namespace(port=port, baudrate=baudrate, verbose=False,
                               frame_size=frame_size, serial=(mode == 'serial'))


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='CMUX Multiplex Terminal — AT commands + PPP dialup (CMUX or Direct Serial)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cmux_terminal.py COM11 115200                # CMUX mode (default)
  python cmux_terminal.py COM11 115200 --serial       # Direct Serial mode
  python cmux_terminal.py COM11 115200 --verbose
        """
    )
    parser.add_argument('port', nargs='?', default=None, help='serial port')
    parser.add_argument('baudrate', type=int, nargs='?', default=115200, help='baud rate')
    parser.add_argument('--verbose', '-v', action='store_true', help='print raw data')
    parser.add_argument('--frame-size', '-f', type=int, default=0,
                        help='CMUX max frame size (N1), 0=default, e.g. 128/256/512/1024/1500')
    parser.add_argument('--serial', action='store_true',
                        help='Direct Serial mode (skip CMUX, PPP runs directly on serial)')

    first_run = True

    while True:
        # ---- Interactive setup / args parsing ----
        if len(sys.argv) == 1 and first_run:
            args = _interactive_setup(is_first_run=True)
        elif len(sys.argv) == 1:
            args = _interactive_setup(is_first_run=False)
        else:
            args = parser.parse_args()
            if args.port is None:
                parser.print_help()
                print("\npress Enter to exit...")
                input()
                sys.exit(1)

        first_run = False

        # ---- Build harness ----
        mode = 'serial' if args.serial else 'cmux'
        harness = CmuxHarness(mode=mode, verbose=args.verbose, frame_size=args.frame_size)

        # Register services
        at_service = AtService(verbose=args.verbose)
        harness.register(at_service)
        harness._at_service = at_service

        ppp_service = PppService(verbose=args.verbose)
        harness.register(ppp_service)

        ping_service = PingService(verbose=args.verbose)
        harness.register(ping_service)

        ftp_service = FtpService(verbose=args.verbose)
        harness.register(ftp_service)

        iperf_service = IperfService(verbose=args.verbose)
        harness.register(iperf_service)

        # ---- Console close handler ----
        _console_handler_ref = None
        if sys.platform == 'win32':
            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
            def _console_handler(ctrl_type):
                print("\n  window closing, disconnecting...")
                harness.shutdown()
                os._exit(0)
            if ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True):
                _console_handler_ref = _console_handler

        try:
            if not harness.start(args.port, args.baudrate):
                if len(sys.argv) > 1:
                    sys.exit(1)
                continue  # back to menu on failure

            while harness.running:
                try:
                    mode_label = 'serial' if mode == 'serial' else 'cmux'
                    if harness.state.ppp_running and harness.state.ppp_ipcp_up:
                        pp = f" [ppp: {harness.state.ppp_local_ip or '...'}]"
                    else:
                        pp = ''
                    prompt = f'\n[{mode_label}{pp}] > '
                    cmd = _input_with_completion(prompt)
                except (EOFError, KeyboardInterrupt):
                    break

                if not cmd:
                    continue

                result = harness.dispatch(cmd)

                if result == 'quit':
                    if len(sys.argv) > 1:
                        break  # CLI mode: exit program
                    print("\n  returning to main menu...")
                    break

        except KeyboardInterrupt:
            print()
        finally:
            harness.shutdown()
            print("session ended")

        # CLI mode: exit after first run
        if len(sys.argv) > 1:
            break
        # Interactive mode: loop back to main menu

    # keep window open when exe is double-clicked
    if getattr(sys, 'frozen', False):
        input("\npress Enter to exit...")


def _main_wrapper():
    try:
        main()
    except Exception as e:
        print(f"\n[Fatal error] {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close...")
        sys.exit(1)


if __name__ == '__main__':
    _ensure_admin()
    _main_wrapper()