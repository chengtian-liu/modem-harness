"""AT command service — sends AT commands on DLCI channels."""

import time
import threading
from typing import Optional, TYPE_CHECKING

from .base import ServiceInterface
from ..events import Event

if TYPE_CHECKING:
    from ..harness import CmuxHarness


# ============================================================
# AT command registry — Tab auto-completion
# Standard 3GPP + Quectel commands (industry standard)
# ============================================================

AT_COMMANDS = [
    # Basic AT commands
    "AT&C", "AT&D", "AT&F", "AT&V", "AT&W",
    "ATD", "ATH", "ATI", "ATO", "ATQ", "ATS0", "ATS3", "ATS4", "ATS5",
    "ATV", "ATZ",

    # Standard 3GPP commands (27.007 / 27.005)
    "AT+CBC", "AT+CCLK", "AT+CEER", "AT+CFUN",
    "AT+CGACT", "AT+CGATT", "AT+CGAUTH", "AT+CGDCONT",
    "AT+CGMI", "AT+CGMM", "AT+CGMR", "AT+CGPADDR", "AT+CGREG",
    "AT+CGSMS", "AT+CGSN",
    "AT+CHLD", "AT+CHUP",
    "AT+CIMI",
    "AT+CLAC", "AT+CLCK", "AT+CLIP", "AT+CLIR",
    "AT+CMEE", "AT+CMGC", "AT+CMGD", "AT+CMGF", "AT+CMGL",
    "AT+CMGR", "AT+CMGS", "AT+CMGW", "AT+CMSS",
    "AT+CMUX",
    "AT+CNMA", "AT+CNMI", "AT+CNUM",
    "AT+COPN", "AT+COPS",
    "AT+CPIN", "AT+CPINR", "AT+CPMS", "AT+CPOL", "AT+CPWD",
    "AT+CRC", "AT+CREG", "AT+CRLP", "AT+CRSM",
    "AT+CSCA", "AT+CSCB", "AT+CSCS", "AT+CSDH", "AT+CSIM",
    "AT+CSMP", "AT+CSMS", "AT+CSQ",
    "AT+CTZU", "AT+CTZR",
    "AT+CUSD",
    "AT+CVHU",

    # Standard 3GPP — supplementary
    "AT+CGCMOD", "AT+CGEQOS", "AT+CGTFT",
    "AT+CCHO", "AT+CCHC",
    "AT+CGLA", "AT+CLAC",
    "AT+CPAS", "AT+CPBS", "AT+CPBR", "AT+CPBF", "AT+CPBW",
    "AT+CCFC", "AT+CCWA", "AT+CLIP", "AT+CLIR", "AT+COLP", "AT+CDIP",
    "AT+CAOC", "AT+CACM", "AT+CAMM", "AT+CPUC",
    "AT+CSSN", "AT+CUSD", "AT+CCUG",
    "AT+CPLS", "AT+CEPOL", "AT+CTFR",
    "AT+CGDATA", "AT+CGANS", "AT+CGAUTO", "AT+CGEQREQ", "AT+CGEQMIN",
    "AT+CGTFTRDP", "AT+CGEREP", "AT+CGCONTRDP", "AT+CGSCONTRDP",
    "AT+CGEQOSRDP",
    "AT+CSODCP", "AT+CRTDCP", "AT+CDU",
    "AT+CEDRXS", "AT+CEDRXRDP", "AT+CEREG",
    "AT+CEMODE", "AT+CEID",
    "AT+CPSMS", "AT+CEDRXS",

    # Standard AT commands (ITU-T V.250)
    "AT+IPR", "AT+IFC", "AT+ICF",
    "AT+GMI", "AT+GMM", "AT+GMR", "AT+GSN",
    "AT+FCLASS",

    # Quectel AT commands (industry standard)
    "AT+QADC", "AT+QAUGDCNT", "AT+QBLACKCELL", "AT+QBLACKCELLCFG",
    "AT+QCAMAUTO", "AT+QCAMCAP", "AT+QCAMCFG", "AT+QCAMCLOSE",
    "AT+QCAMINF", "AT+QCAMOPEN", "AT+QCAMREAD", "AT+QCCID", "AT+QCELL",
    "AT+QCELLEX", "AT+QCELLINFO", "AT+QCFG", "AT+QCHIPINFO",
    "AT+QCMGR", "AT+QCMGS", "AT+QCRITICALDATA", "AT+QCSQ", "AT+QDSIM",
    "AT+QDTLSSTAT", "AT+QEHPLMN", "AT+QENG", "AT+QFCLOSE",
    "AT+QFDEL", "AT+QFDWL", "AT+QFLDS", "AT+QFLST", "AT+QFOPEN",
    "AT+QFOTADL", "AT+QFPOSITION", "AT+QFREAD", "AT+QFSEEK",
    "AT+QFTPCFG", "AT+QFTPCLOSE", "AT+QFTPCWD", "AT+QFTPDEL",
    "AT+QFTPGET", "AT+QFTPLEN", "AT+QFTPLIST", "AT+QFTPMDTM",
    "AT+QFTPMKDIR", "AT+QFTPMLSD", "AT+QFTPNLST", "AT+QFTPOPEN",
    "AT+QFTPPUT", "AT+QFTPPWD", "AT+QFTPRENAME", "AT+QFTPRMDIR",
    "AT+QFTPSIZE", "AT+QFTPSTAT", "AT+QFUPL", "AT+QFWRITE",
    "AT+QGDCNT", "AT+QGPS", "AT+QGSN", "AT+QHTTPCFG", "AT+QHTTPGET",
    "AT+QHTTPGETEX", "AT+QHTTPPOST", "AT+QHTTPPOSTFILE",
    "AT+QHTTPREAD", "AT+QHTTPREADFILE", "AT+QHTTPSTOP", "AT+QHTTPURL",
    "AT+QIACT", "AT+QIACTEX", "AT+QICFG", "AT+QICLOSE", "AT+QICSGP",
    "AT+QIDEACT", "AT+QIDEACTEX", "AT+QIDNSCFG", "AT+QIDNSGIP",
    "AT+QIGETERROR", "AT+QIMS", "AT+QIMSACT", "AT+QIMSACTEX",
    "AT+QIMSPRECD", "AT+QINDCFG", "AT+QINISTAT", "AT+QIOPEN",
    "AT+QIRD", "AT+QIREGAPP", "AT+QISDE", "AT+QISEND", "AT+QISENDEX",
    "AT+QISTATE", "AT+QISWTMD", "AT+QLAADDOBJ", "AT+QLACFG",
    "AT+QLACONFIG", "AT+QLADELOBJ", "AT+QLADEREG", "AT+QLAEXERSP",
    "AT+QLANOTIFY", "AT+QLAOBSRSP", "AT+QLARD", "AT+QLARDRSP",
    "AT+QLARECOVER", "AT+QLAREG", "AT+QLASTATUS", "AT+QLAUPDATE",
    "AT+QLAWRRSP", "AT+QLTS", "AT+QLWEVTIND", "AT+QLWFOTAIND",
    "AT+QLWSREGIND", "AT+QLWULDATA", "AT+QLWULDATAEX",
    "AT+QLWULDATASTATUS", "AT+QMTCFG", "AT+QMTCLOSE", "AT+QMTCONN",
    "AT+QMTDISC", "AT+QMTOPEN", "AT+QMTPUBEX", "AT+QMTRECV",
    "AT+QMTSUB", "AT+QMTUNS", "AT+QNETDEVCTL", "AT+QNTP",
    "AT+QNWINFO", "AT+QOPS", "AT+QOPSCFG", "AT+QPINC", "AT+QPING",
    "AT+QPOWD", "AT+QPPPDROP", "AT+QREGSWT", "AT+QRESETDTLS",
    "AT+QRFTEST", "AT+QRFTESTMODE", "AT+QRXFTM", "AT+QSCLK",
    "AT+QSCLKEX", "AT+QSECSWT", "AT+QSETPSK", "AT+QSIM0STAT",
    "AT+QSIM1STAT", "AT+QSIMDET", "AT+QSIMSTAT", "AT+QSIMSWITCH",
    "AT+QSPN", "AT+QSREGENABLE", "AT+QSSLCFG", "AT+QSSLCLOSE",
    "AT+QSSLOPEN", "AT+QSSLRECV", "AT+QSSLSEND", "AT+QSSLSTATE",
    "AT+QURCCFG", "AT+QUTCTIME", "AT+QWIFISCAN",
]
AT_COMMANDS = sorted(set(AT_COMMANDS), key=lambda x: x.upper())


# ============================================================
# AT Channel
# ============================================================

class AtChannel:
    """AT command channel — send/receive AT commands on given DLCI."""

    def __init__(self, dlci: int, verbose: bool = False):
        self.dlci = dlci
        self.verbose = verbose
        self._response_buffer = bytearray()
        self._response_lock = threading.Lock()
        self._data_event = threading.Event()

    def feed_response(self, data: bytes):
        with self._response_lock:
            self._response_buffer.extend(data)
            self._data_event.set()
        if self.verbose:
            print(f"  [DLCI {self.dlci} RX] {data.hex(' ')}")

    def clear_buffer(self):
        with self._response_lock:
            self._response_buffer.clear()
            self._data_event.clear()

    def read_response(self, timeout: float = 2.0) -> str:
        if self._data_event.wait(timeout):
            with self._response_lock:
                if self._response_buffer:
                    data = bytes(self._response_buffer)
                    self._response_buffer.clear()
                    self._data_event.clear()
                    return data.decode('utf-8', errors='replace')
        return ''

    def read_all(self, timeout: float = 2.0) -> str:
        result = ''
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._data_event.wait(max(0, deadline - time.time())):
                with self._response_lock:
                    if self._response_buffer:
                        data = bytes(self._response_buffer)
                        self._response_buffer.clear()
                        self._data_event.clear()
                        result += data.decode('utf-8', errors='replace')
                        deadline = time.time() + 0.5  # extend deadline on new data
                continue
            break
        return result


# ============================================================
# AT Service
# ============================================================

class AtService(ServiceInterface):
    """AT command service — manages AT channels and handles AT commands."""

    name = 'at'
    commands = ['at']

    def __init__(self, verbose: bool = False):
        self._verbose = verbose
        self._harness: Optional['CmuxHarness'] = None
        self._channels: dict[int, AtChannel] = {}

    @property
    def channels(self) -> dict[int, AtChannel]:
        return self._channels

    def on_register(self, harness: 'CmuxHarness') -> None:
        self._harness = harness

    def on_command(self, args: list[str]) -> Optional[str]:
        return None  # AT commands are handled by harness directly

    def on_shutdown(self) -> None:
        self._channels.clear()

    def create_channel(self, dlci: int) -> None:
        """Create an AT channel for the given DLCI."""
        self._channels[dlci] = AtChannel(dlci, verbose=self._verbose)

    def send(self, dlci: int, cmd: str) -> None:
        """Send an AT command on the given DLCI."""
        if self._harness.state.mode == 'serial':
            # Direct serial mode
            full_cmd = (cmd + '\r').encode()
            print(f"  [Serial] → {cmd}")
            self._harness.transport.send(full_cmd)
        elif dlci in self._channels:
            print(f"  [DLCI {dlci}] → {cmd}")
            self._harness.transport.send((cmd + '\r').encode(), dlci)
        else:
            print(f"  DLCI {dlci} unavailable")

    def feed_response(self, dlci: int, data: bytes) -> None:
        """Feed response data to the appropriate AT channel."""
        if dlci in self._channels:
            if self._verbose:
                text = data.decode('utf-8', errors='replace').strip()
                if text:
                    print(f"  [DLCI {dlci}] ← {text}")
            self._channels[dlci].feed_response(data)

    def read_response(self, dlci: int, timeout: float = 5.0) -> str:
        """Read AT response from a channel."""
        if dlci in self._channels:
            return self._channels[dlci].read_all(timeout=timeout)
        return ''

    def clear_buffer(self, dlci: int) -> None:
        """Clear the response buffer for a channel."""
        if dlci in self._channels:
            self._channels[dlci].clear_buffer()