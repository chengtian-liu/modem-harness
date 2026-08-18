# Modem Harness
<img width="981" height="625" alt="demo" src="https://github.com/user-attachments/assets/bb8202c7-b6e0-4203-8510-a684334d53e5" />

[![Python](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey.svg)](#requirements)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A pluggable LTE module testing platform — AT commands, CMUX multiplexing, PPP dialup, TUN networking, and built-in FTP/iperf3 benchmarking. Harness architecture: add new services without touching existing code.

**Requirements:** Python 3.7+, `pyserial`, and `wintun.dll` for TUN support.

## Features

**Transport layer** — two modes, all features supported in both:

| Mode                | Flag       | Description                                                                                      |
|---------------------|------------|--------------------------------------------------------------------------------------------------|
| **CMUX** (default)  | —          | GSM 07.10 multiplexing — AT commands and PPP data run simultaneously on separate DLCI channels.  |
| **Direct Serial**   | `--serial` | Raw serial — no CMUX overhead, simpler to debug. Same feature set, different transport.          |

**Application layer** — available in both modes:

- **AT commands** — 200+ commands with Tab auto-completion
- **PPP dialup** — LCP/IPCP/IPv6CP negotiation with PAP authentication
- **TUN virtual adapter** — route system TCP/IP traffic through the PPP serial link
- **ICMP ping** — built-in connectivity testing
- **FTP** — upload/download throughput measurement over the PPP connection
- **iperf3** — TCP speed benchmark (built-in, integrated in `cmux_harness/iperf3/`)
- **Auto-elevation** — automatic UAC admin privilege request for TUN adapter
- **Tab completion** — AT command auto-completion and command history (requires `prompt_toolkit`)

## Why This Tool?

Testing LTE modules involves a chain of technologies — serial AT commands, CMUX multiplexing, PPP dialup, TUN virtual networking, and throughput measurement. Existing tools each cover only one or two links in this chain:

| Tool                        | AT Commands | CMUX | PPP | TUN | FTP Test | iperf3 | Windows | Open Source |
|-----------------------------|:-----------:|:----:|:---:|:---:|:--------:|:------:|:-------:|:-----------:|
| **QCOM / QPST** (Qualcomm)  | ✅          | ✅   | ✅  | —   | —        | —      | ✅      | —           |
| **ModemManager** (Linux)    | ✅          | ✅   | ✅  | —   | —        | —      | —       | ✅          |
| **pppd**                    | —           | —    | ✅  | —   | —        | —      | —       | ✅          |
| **AT Command Tester**       | ✅          | —    | —   | —   | —        | —      | ✅      | ✅          |
| **iperf3** (standalone)     | —           | —    | —   | —   | —        | ✅     | ✅      | ✅          |
| **Modem Harness**           | ✅          | ✅   | ✅  | ✅  | ✅       | ✅     | ✅      | ✅          |

**What makes Modem Harness different:**

- **Harness architecture — the killer feature.** Transport and application logic are completely decoupled. Each service (AT, PPP, ping, FTP, iperf3) is an independent plugin. Adding a new service requires **zero changes to existing code** — just one file, one `register()` call:

  ```python
  # That's it. Your new service gets AT commands, PPP events, TUN routing — all for free.
  harness.register(MyCustomService())
  ```

  Want to add HTTP testing? MQTT benchmarking? DNS latency measurement? Write your service class, register it, done. No touching the core. This is the same plugin philosophy behind tools like Claude Code and VS Code extensions — the harness provides transport, events, and state; you focus on your application logic.

- **Full-stack integration** — serial → CMUX → PPP → TUN → system TCP/IP, all in one tool. No need to chain 5 different programs together.
- **Windows + pure Python** — Most LTE testing tools are either Linux-only (ModemManager, pppd) or closed-source vendor tools (QCOM). This runs on Windows with zero compilation — just Python 3.7+ and `pyserial`.
- **Dual transport modes** — CMUX (GSM 07.10) and Direct Serial share the same service layer. Switch modes without restarting the program.
- **Built-in benchmarking** — FTP upload/download speed tests and iperf3 TCP throughput benchmarks are integrated directly into the terminal, routing through the PPP/TUN link.

## Dual-Mode Design

The tool separates transport from application logic: CMUX and Direct Serial are just two transport backends, and all upper-layer features (AT commands, PPP, FTP, ping, iperf3) work identically in both.

**Switching modes:**

- CLI: pass `--serial` to use Direct Serial mode, omit for CMUX (default)
- Interactive: choose mode at startup, and after quitting a session it returns to the main menu — switch modes without restarting the program

**When to use which:**

| Scenario                                      | Recommended mode |
|-----------------------------------------------|------------------|
| Concurrent AT + data on separate channels     | CMUX             |
| Simple AT testing, quick debugging            | Direct Serial    |
| Module doesn't support CMUX                   | Direct Serial    |
| Full PPP/FTP/iperf3 throughput testing        | Either — both work |

## Quick Start

```bash
# CMUX mode (default) — AT commands + PPP on separate DLCI channels
python modem_harness.py COM11 115200 --frame-size 512

# Direct Serial mode — all features available, no CMUX overhead
python modem_harness.py COM11 115200 --serial

# Interactive mode — select port, baud rate, frame size, and mode step by step
python modem_harness.py
```

If you double-click `modem_harness.py` in File Explorer, it will start in interactive mode
and ask for the serial port, baud rate, CMUX frame size, and mode (CMUX or Direct Serial) step by step.

Once connected:

```
# CMUX mode
[cmux] > 1>AT+CSQ                       # Send AT on DLCI 1
[cmux] > 2>AT+CGMI                      # Send AT on DLCI 2
[cmux] > ppp                            # Start PPP dialup on DLCI 2

# Direct Serial mode
[serial] > AT+CSQ                       # Send AT directly
[serial] > ppp                          # Start PPP dialup

# PPP mode (both modes)
[ppp] > ping 8.8.8.8                    # Test connectivity
[ppp] > ftp upload test.bin test.bin    # Upload speed test
[ppp] > ftp download test.bin dl.bin    # Download speed test
[ppp] > ppp stop                        # Stop PPP
```

## Commands

| Command                         | Description                                                                 |
|---------------------------------|-----------------------------------------------------------------------------|
| `at <cmd>`                      | Send AT command (DLCI 1 in CMUX mode, direct in serial mode)                |
| `1>AT+CSQ`                      | Send AT command on DLCI 1 (CMUX mode only)                                  |
| `2>AT+CGMI`                     | Send AT command on DLCI 2 (CMUX mode only)                                  |
| `ppp [dlci] [--apn APN]`        | Start PPP dialup on given DLCI (default DLCI 2, no DLCI in serial mode)     |
| `ppp stop`                      | Stop PPP                                                                    |
| `ping [ip] [size]`              | Ping target IP (default 8.8.8.8, 32B)                                       |
| `ftp upload <local> <remote>`   | Upload file and measure speed                                               |
| `ftp download <remote> <local>` | Download file and measure speed                                             |
| `ftp config`                    | View/modify FTP server config                                               |
| `quit`                          | Exit                                                                        |

### CLI Options

| Option               | Description                                                              |
|----------------------|--------------------------------------------------------------------------|
| `port`               | Serial port (e.g. `COM11`). If omitted, prompts interactively.           |
| `baudrate`           | Baud rate (default `115200`).                                            |
| `--verbose`, `-v`    | Print raw serial data (hex dump).                                        |
| `--frame-size`, `-f` | CMUX max frame size N1 (default `0` = auto).                             |
| `--serial`           | Direct Serial PPP mode (skip CMUX, PPP runs directly on serial).         |

## FTP Configuration

The built-in FTP tester uploads/downloads files to measure throughput over the PPP link.
Configure it before first use:

```
[PPP] > ftp config host 192.168.1.100
[PPP] > ftp config port 21
[PPP] > ftp config user myuser
[PPP] > ftp config pass mypassword
[PPP] > ftp config              # view current config
```

> **Note:** After changing the FTP server IP, the TUN routing table is automatically updated
> to route traffic to the new server through the PPP tunnel.

## Requirements

- **Windows** — TUN adapter requires Windows; tested on Windows 10/11
- **Python 3.7+**
- **pyserial** — serial port communication
- **Administrator privileges** — required for TUN virtual adapter (auto-elevation is built in)
- `prompt_toolkit` (optional) — for Tab completion and command history

### wintun.dll

The TUN virtual adapter depends on [**wintun**](https://www.wintun.net/), a lightweight kernel-level TUN driver for Windows maintained by the WireGuard project.

A **64-bit** `wintun.dll` (v0.14.1) is included in the project — no separate download needed. Place it alongside `modem_harness.py`:

```bash
dir
├── modem_harness.py
├── wintun.dll      ← 64-bit, included
├── LICENSE
└── README.md
```

|                   |                                          |
|-------------------|------------------------------------------|
| **Official site** | https://www.wintun.net/                  |
| **Source code**   | https://github.com/WireGuard/wintun      |
| **License**       | MIT (same as this project)               |

> **Note:** If you need a 32-bit version, download `wintun-0.14.1.zip` from https://www.wintun.net/ and extract from the `x86` folder.

## Installation

```bash
git clone https://github.com/<your-username>/modem-harness.git
cd modem-harness

# Install required dependency
pip install pyserial

# Optional: install prompt_toolkit for Tab completion
pip install prompt_toolkit
```

## How It Works

**CMUX mode** — GSM 07.10 multiplexing, AT and PPP on separate DLCI channels:

```
┌─────────────┐    Serial     ┌─────────────┐
│  LTE Module │◄────────────► │  CMUX Engine│
│  (UE side)  │               │  (TE side)  │
└─────────────┘               └─────┬───────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                 DLCI 0          DLCI 1          DLCI 2
              (Control)      (AT Commands)    (PPP Data)
                                               │
                                        ┌──────┴──────┐
                                        │  PPP Engine │
                                        └──────┬──────┘
                                               │
                                        ┌──────┴──────┐
                                        │ TUN Adapter │──► System TCP/IP
                                        └─────────────┘
```

**Direct Serial mode** — raw serial, no CMUX overhead, PPP takes over the line:

```
┌─────────────┐    Serial     ┌──────────────────┐
│  LTE Module │◄────────────► │ SerialTransport  │
│  (UE side)  │               │  (raw serial)    │
└─────────────┘               └────────┬─────────┘
                                       │
                           ┌───────────┴───────────┐
                           │                       │
                      PPP off:                 PPP on:
                    AT Commands              PPP Engine
                    (direct)                    │
                                         ┌──────┴──────┐
                                         │ TUN Adapter │──► System TCP/IP
                                         └─────────────┘
```

## Harness Architecture — Adding New Services

The tool is built on a **harness architecture** that completely decouples transport from application logic. Each feature (AT, PPP, ping, FTP, iperf3) is a standalone `Service` that registers itself with the central `CmuxHarness`. Adding a new service requires **zero changes** to existing code.

### Architecture Overview

```
                        ┌──────────────────────────────────┐
                        │         modem_harness.py         │
                        │     (thin CLI entry, ~420 LOC)   │
                        └──────────────┬───────────────────┘
                                       │ register() x N
                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                           CmuxHarness                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │  Transport   │  │   Services   │  │ SharedState  │  │ EventBus  │ │
│  │  Interface   │  │   Registry   │  │ (thread-safe)│  │ (pub/sub) │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘  └─────┬─────┘ │
│         │                 │                                  │       │
└─────────┼─────────────────┼──────────────────────────────────┼───────┘
          │                 │                                  │
          ▼                 ▼                                  ▼
┌───────────────────┐  ┌──────────────────────────────────────────────┐
│ Transport Backend │  │              Service Layer                   │
│                   │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│ ┌────────────────┐│  │  │ AtService│ │PppService│ │FtpService│ ...  │
│ │ CmuxTransport  ││  │  │ (AT cmd) │ │(LCP/IPCP │ │(FTP test)│      │
│ │ (GSM 07.10)    ││  │  │          │ │ +TUN fwd)│ │          │      │
│ └────────────────┘│  │  └──────────┘ └──────────┘ └──────────┘      │
│ ┌────────────────┐│  │  ┌──────────┐ ┌──────────┐                   │
│ │ SerialTransport││  │  │PingSvc   │ │IperfSvc  │                   │
│ │ (raw serial)   ││  │  │(sys ping)│ │(iperf3)  │                   │
│ └────────────────┘│  │  └──────────┘ └──────────┘                   │
└───────────────────┘  └──────────────────────────────────────────────┘
          │                                   │
          ▼                                   ▼
   ┌─────────────┐                    ┌──────────────┐
   │  LTE Module │                    │  TUN Adapter │──► System TCP/IP
   │  (serial)   │                    │  (wintun)    │
   └─────────────┘                    └──────────────┘

   Services communicate ONLY through EventBus — never directly.
   New services are one file, one register() call, zero changes to core.
```

### Service Lifecycle

Every service implements `ServiceInterface` with three hooks:

```python
from cmux_harness.services.base import ServiceInterface
from cmux_harness.events import Event

class MyService(ServiceInterface):
    name = 'myservice'          # unique name
    commands = ['mycmd']        # CLI commands to register

    def on_register(self, harness):
        """Called once at startup — store harness ref, subscribe to events."""
        self._harness = harness
        harness.events.subscribe(Event.PPP_IPCP_UP, self._on_ppp_up)

    def on_command(self, args: list[str]) -> str | None:
        """Handle a user command. Return output string, or None."""
        subcmd = args[1] if len(args) > 1 else 'start'
        if subcmd == 'start':
            return 'Hello from MyService!'
        return None

    def on_shutdown(self):
        """Clean up — stop threads, close connections."""
        pass
```

### Event Bus

Services communicate through the event bus — they never reference each other directly:

| Event                  | When                            | Data                              |
|------------------------|---------------------------------|-----------------------------------|
| `PPP_IPCP_UP`          | PPP negotiation completes       | `local_ip`, `remote_ip`, `dns`   |
| `PPP_DISCONNECTING`    | PPP is about to disconnect      | —                                 |
| `PPP_ICMP_REPLY`       | ICMP echo reply received        | `icmp`, `src`, `ttl`              |
| `TRANSPORT_OPENED`     | Transport is ready              | `mode`                            |
| `TRANSPORT_CLOSED`     | Transport is shutting down      | —                                 |

### Example: Registering the FTP Service

The FTP service is a real example already in the codebase. Here's how it plugs in:

```python
# cmux_harness/services/ftp.py (simplified)
import ftplib
from .base import ServiceInterface
from ..events import Event

class FtpService(ServiceInterface):
    name = 'ftp'
    commands = ['ftp']

    def on_register(self, harness):
        self._harness = harness
        # Listen for PPP up/down to know when FTP is usable
        harness.events.subscribe(Event.PPP_IPCP_UP, self._on_ppp_up)
        harness.events.subscribe(Event.PPP_DISCONNECTING, self._on_ppp_down)

    def on_command(self, args):
        if not self._ppp_ready:
            return "PPP not connected, please run ppp first"
        subcmd = args[1] if len(args) > 1 else 'help'
        if subcmd == 'upload':
            return self._upload(args[2], args[3])
        elif subcmd == 'download':
            return self._download(args[2], args[3])
        elif subcmd == 'config':
            return self._show_config()
        return None

    def _on_ppp_up(self, **kw):   self._ppp_ready = True
    def _on_ppp_down(self, **kw): self._ppp_ready = False
    def on_shutdown(self):        pass
```

Then register it in `modem_harness.py`:

```python
# In modem_harness.py:
from cmux_harness.services.ftp import FtpService

# ... in the harness setup section:
harness.register(FtpService())
```

That's it — `ftp upload`, `ftp download`, `ftp config` are now available in the terminal. The FTP service doesn't know about PPP, AT, or the transport layer — it just subscribes to `PPP_IPCP_UP` and waits for the green light.

### Project Structure

```
cmux_harness/
├── harness.py          # CmuxHarness — owns transport, services, state, events
├── state.py            # SharedState — thread-safe dataclass
├── events.py           # EventBus — pub/sub for inter-service communication
├── tun.py              # TunAdapter — wintun.dll wrapper
├── utils.py            # ICMP/IP checksum helpers
├── protocol/
│   ├── cmux.py         # CMUX frames, CRC, FrameParser
│   └── ppp.py          # PPP frames, FCS, escape, PppParser
├── transport/
│   ├── base.py         # TransportInterface ABC
│   ├── cmux.py         # CmuxTransport (CMUX mode)
│   └── serial.py       # SerialTransport (raw serial mode)
├── services/
│   ├── base.py         # ServiceInterface ABC
│   ├── at.py           # AtService — 200+ AT commands
│   ├── ppp.py          # PppService — LCP/IPCP/IPv6CP + TUN forwarding
│   ├── ping.py         # PingService — ICMP echo via PPP
│   ├── ftp.py          # FtpService — upload/download speed testing
│   └── iperf.py        # IperfService — TCP throughput benchmark
└── iperf3/             # Native Python iperf3 client/server library
    ├── iperf3_client.py
    ├── iperf3_server.py
    ├── iperf3_test.py
    └── ...
```

## Troubleshooting

### "WintunCreateAdapter failed"

1. Make sure you are running as **Administrator** (right-click → Run as administrator).
2. Verify `wintun.dll` is in the same directory as `modem_harness.py` (it is included in the project by default).
3. If you're on 32-bit Windows, download the x86 version from https://www.wintun.net/.

### "Access denied" / UAC prompt cancelled

The TUN virtual adapter requires administrator privileges. When the UAC prompt appears, click **Yes** to grant permission.

### "No available serial ports detected"

- Check that the LTE module is connected via USB and recognized as a COM port.
- You can manually enter the port name (e.g. `COM11`) when prompted.
- In Device Manager, look under "Ports (COM & LPT)" to find the correct port number.

### "CONNECT not received" / PPP negotiation timeout

- Verify the APN is correct: `ppp --apn cmnet` (or your carrier's APN).
- Check that the LTE module is registered on the network: `at AT+CREG?`
- Try a different DLCI: `ppp 3`

### Tab completion not working

Install `prompt_toolkit`:

```bash
pip install prompt_toolkit
```

### FTP connection fails during speed test

- Make sure PPP is connected first: `ppp`
- Verify the FTP server is reachable from the PPP network.
- Try changing the FTP server: `ftp config <ip> <port> <user> <password>`

## License

MIT License — see [LICENSE](LICENSE) for details.
