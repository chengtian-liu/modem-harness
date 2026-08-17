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
# ============================================================

AT_COMMANDS = [
    "AT&C", "AT&D", "AT&F", "AT&V", "AT&W",
    "AT*CalInfo", "AT*MRD_IMEI",
    "AT+A", "AT+ABORT", "AT+ASSERTCP", "AT+ATD", "AT+ATE", "AT+ATH",
    "AT+ATI", "AT+ATO", "AT+ATQ", "AT+ATS0", "AT+ATS3", "AT+ATS4",
    "AT+ATS5", "AT+ATV", "AT+ATZ", "AT+AUTHID",
    "AT+C", "AT+CACDC", "AT+CAVIMS", "AT+CCED", "AT+CCHC", "AT+CCHO",
    "AT+CCIOTOPT", "AT+CCLK", "AT+CDPRMLFT", "AT+CDPUPDATE", "AT+CDU",
    "AT+CEDRXRDP", "AT+CEDRXS", "AT+CEER", "AT+CEID", "AT+CEMODE",
    "AT+CEREG", "AT+CESQ", "AT+CFGRI", "AT+CFGRI2", "AT+CFUN",
    "AT+CGACT", "AT+CGAPNRC", "AT+CGATT", "AT+CGAUTH", "AT+CGCMOD",
    "AT+CGCONTRDP", "AT+CGDCONT", "AT+CGDSCONT", "AT+CGEQOS",
    "AT+CGEQOSRDP", "AT+CGEREP", "AT+CGLA", "AT+CGMI", "AT+CGMM",
    "AT+CGMR", "AT+CGPADDR", "AT+CGPIAF", "AT+CGREG", "AT+CGSCONTRDP",
    "AT+CGSMS", "AT+CGSN", "AT+CGTFT", "AT+CGTFTRDP", "AT+CHCCS",
    "AT+CHLD", "AT+CHUP", "AT+CIDACT", "AT+CIMI", "AT+CIPCA",
    "AT+CIPGSMLOC", "AT+CLAC", "AT+CLCK", "AT+CMADC", "AT+CMCCS",
    "AT+CMDNS", "AT+CMEE", "AT+CMGC", "AT+CMGD", "AT+CMGF", "AT+CMGL",
    "AT+CMGR", "AT+CMGS", "AT+CMGW", "AT+CMMS", "AT+CMNTP", "AT+CMOLR",
    "AT+CMSS", "AT+CMSYSCTRL", "AT+CMUX", "AT+CMVER", "AT+CMVERSION",
    "AT+CNEC", "AT+CNMA", "AT+CNMI", "AT+CNMPSD", "AT+CNUM",
    "AT+CODECIMS", "AT+COPN", "AT+COPS", "AT+CPIN", "AT+CPINR",
    "AT+CPMS", "AT+CPOF", "AT+CPOL", "AT+CPSDO", "AT+CPSMS", "AT+CPWD",
    "AT+CRC", "AT+CREG", "AT+CRLP", "AT+CRSM", "AT+CRTDCP", "AT+CSCA",
    "AT+CSCB", "AT+CSCON", "AT+CSCS", "AT+CSDH", "AT+CSIM", "AT+CSMP",
    "AT+CSMS", "AT+CSODCP", "AT+CSQ", "AT+CSTA",
    "AT+CTLWCFGRST", "AT+CTLWDEREG", "AT+CTLWDTLSHS",
    "AT+CTLWGETRECVDATA", "AT+CTLWGETSRVFRMDNS", "AT+CTLWGETSTATUS",
    "AT+CTLWRECV", "AT+CTLWREG", "AT+CTLWSEND", "AT+CTLWSESDATA",
    "AT+CTLWSETAUTH", "AT+CTLWSETLT", "AT+CTLWSETMOD",
    "AT+CTLWSETPCRYPT", "AT+CTLWSETPSK", "AT+CTLWSETREGMOD",
    "AT+CTLWSETSERVER", "AT+CTLWUPDATE", "AT+CTLWVER",
    "AT+CTMQCFG", "AT+CTMQCFRECV", "AT+CTMQCLOSE", "AT+CTMQCONN",
    "AT+CTMQFOTACTR", "AT+CTMQPUB", "AT+CTMQPUBEX", "AT+CTMQREAD",
    "AT+CTMQSTAT", "AT+CTMQSUB", "AT+CTMQTLS", "AT+CTMQTLSADD",
    "AT+CTMQTLSDEL", "AT+CTMQUNSUB", "AT+CTMQWILL",
    "AT+CTZR", "AT+CTZU", "AT+CUFOTACHK", "AT+CUFOTAUPD",
    "AT+CUSATA", "AT+CUSATD", "AT+CUSATE", "AT+CUSATR", "AT+CUSATT",
    "AT+CUSATW", "AT+CUSD", "AT+CVHU", "AT+CVMOD", "AT+CZPIMEI",
    "AT+D", "AT+DEBUG", "AT+ECAM", "AT+ECSIMCFG",
    "AT+F", "AT+FCLASS", "AT+FORCEDL",
    "AT+GMI", "AT+GMM", "AT+GMR", "AT+GNSS", "AT+GPIO", "AT+GSN",
    "AT+H", "AT+HEAPINFO", "AT+IFC", "AT+IPR",
    "AT+JDRCFG", "AT+JDRS", "AT+LIC", "AT+LOCK",
    "AT+MADC", "AT+MAUDPLCFG", "AT+MAUDPLFILE", "AT+MAUDPLPAUSE",
    "AT+MAUDPLRESUME", "AT+MAUDPLSTOP", "AT+MAUDRECCFG",
    "AT+MAUDRECFILE", "AT+MAUDRECPAUSE", "AT+MAUDRECRESUME",
    "AT+MAUDRECSTOP", "AT+MBAND", "AT+MBSVER", "AT+MCCID", "AT+MCGSNW",
    "AT+MCHIPINFO", "AT+MCSEARFCN", "AT+MDIALUP", "AT+MDIALUPCFG",
    "AT+MDMPCFG", "AT+MDMPCFGEX", "AT+MDNSCFG", "AT+MDNSGIP",
    "AT+MEMINFO", "AT+MEMMTIMER", "AT+MFCFG", "AT+MFCHECK",
    "AT+MFCLOSE", "AT+MFDELETE", "AT+MFGET", "AT+MFLIST", "AT+MFMOVE",
    "AT+MFOPEN", "AT+MFPUT", "AT+MFREAD", "AT+MFSEEK", "AT+MFSINFO",
    "AT+MFSIZE", "AT+MFSYNC", "AT+MFTPAPPE", "AT+MFTPCFG",
    "AT+MFTPCONN", "AT+MFTPCWD", "AT+MFTPDEL", "AT+MFTPDISC",
    "AT+MFTPLIST", "AT+MFTPMKD", "AT+MFTPPWD", "AT+MFTPRETR",
    "AT+MFTPRN", "AT+MFTPSTATE", "AT+MFTPSTOR", "AT+MFTRUNC",
    "AT+MFWCFG", "AT+MFWDLOAD", "AT+MFWERASE", "AT+MFWRITE",
    "AT+MFWUPGRADE", "AT+MGPIO", "AT+MHTTPCFG", "AT+MHTTPCONTENT",
    "AT+MHTTPCREATE", "AT+MHTTPDEL", "AT+MHTTPDLFILE",
    "AT+MHTTPHEADER", "AT+MHTTPREAD", "AT+MHTTPREQUEST",
    "AT+MHTTPTERM", "AT+MHWVER", "AT+MIPCALL", "AT+MIPCFG",
    "AT+MIPCLOSE", "AT+MIPLADDOBJ", "AT+MIPLCFG", "AT+MIPLCLOSE",
    "AT+MIPLCONFIG", "AT+MIPLCREATE", "AT+MIPLCREATEEX",
    "AT+MIPLDELETE", "AT+MIPLDELOBJ", "AT+MIPLDEVINFO",
    "AT+MIPLDISCOVERRSP", "AT+MIPLDTLSNAT", "AT+MIPLEXECUTERSP",
    "AT+MIPLFOTACFG", "AT+MIPLFOTAINIT", "AT+MIPLMGR",
    "AT+MIPLNMI", "AT+MIPLNOTIFY", "AT+MIPLOBSERVERSP",
    "AT+MIPLOPEN", "AT+MIPLPARAMETERRSP", "AT+MIPLQMGR",
    "AT+MIPLREADRSP", "AT+MIPLSOTAGET", "AT+MIPLSOTAPARAM",
    "AT+MIPLSOTARESULT", "AT+MIPLUPDATE", "AT+MIPLVER",
    "AT+MIPLWRITERSP", "AT+MIPMODE", "AT+MIPOPEN", "AT+MIPRD",
    "AT+MIPSACK", "AT+MIPSEND", "AT+MIPSTATE", "AT+MIPTKA",
    "AT+MLBSCFG", "AT+MLBSLOC", "AT+MLED", "AT+MLOCKFREQ",
    "AT+MLPMCFG", "AT+MNBIOTEVENT", "AT+MNTP", "AT+MPDSREGCFG",
    "AT+MPDSREGSWT", "AT+MPING", "AT+MPOF", "AT+MPRODUCTMODE",
    "AT+MPSRAT", "AT+MPTWEDRXS", "AT+MPWMCTRL", "AT+MPWMDATA",
    "AT+MQTTCFG", "AT+MQTTCONN", "AT+MQTTDISC", "AT+MQTTPUB",
    "AT+MQTTREAD", "AT+MQTTSTATE", "AT+MQTTSUB", "AT+MQTTUNSUB",
    "AT+MREBOOT", "AT+MSSLCERTRD", "AT+MSSLCERTWR", "AT+MSSLCFG",
    "AT+MSSLCHECK", "AT+MSSLCIPHER", "AT+MSSLKEYWR", "AT+MSSLLIST",
    "AT+MSSLRM", "AT+MSWVER", "AT+MTSETID", "AT+MTTSCFG",
    "AT+MTTSPLAY", "AT+MTTSSTOP", "AT+MUECONFIG", "AT+MUESTATS",
    "AT+MWHWVER", "AT+MWIFISCANCFG", "AT+MWIFISCANQUERY",
    "AT+MWIFISCANSTART", "AT+MWIFISCANSTOP",
    "AT+NATSPEED", "AT+NBAND", "AT+NCCID", "AT+NCDP", "AT+NCONFIG",
    "AT+NCPCDPR", "AT+NCSEARFCN", "AT+NCSG", "AT+NEARFCN",
    "AT+NETLOGCFG", "AT+NFPLMN", "AT+NFWUPD", "AT+NGACTR",
    "AT+NIPINFO", "AT+NITZ", "AT+NL2THP", "AT+NLOCKF", "AT+NMGR",
    "AT+NMGS", "AT+NMGSEXT", "AT+NMSTATUS", "AT+NNMI", "AT+NPING",
    "AT+NPINGSTOP", "AT+NPOPB", "AT+NPOWERCLASS", "AT+NPREEARFCN",
    "AT+NPTWEDRXS", "AT+NQMGR", "AT+NQMGS", "AT+NQPNPD", "AT+NQPODCP",
    "AT+NQSOS", "AT+NRB", "AT+NRNPDM", "AT+NRPM", "AT+NSET", "AT+NSMI",
    "AT+NSNPD", "AT+NSOCFG", "AT+NSOCL", "AT+NSOCO", "AT+NSOCR",
    "AT+NSONMI", "AT+NSORF", "AT+NSOSD", "AT+NSOST", "AT+NSOSTF",
    "AT+NST", "AT+NTSETID", "AT+NUESTATS", "AT+NV", "AT+NWDRX",
    "AT+NWSDVOLT",
    "AT+O", "AT+OFFTIME", "AT+ONETRMLFT",
    "AT+PCTTESTINFO", "AT+PHY", "AT+PSTEST", "AT+PSTESTMODE",
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
    "AT+RAI", "AT+RDNV", "AT+REGTEST", "AT+REQLIC", "AT+RESET",
    "AT+RF", "AT+RFNV", "AT+SIMST", "AT+SIMUUICC", "AT+STANDBY",
    "AT+TEST", "AT+TRB", "AT+TYAUTH",
    "AT+UNIAUTOREG", "AT+UNIAUTOREGCFG", "AT+UNICERTINFO",
    "AT+UNIDELCERTINFO", "AT+UNIDELKEYINFO", "AT+UNIDELKEYINFOM",
    "AT+UNIKEYINFO", "AT+UNIKEYINFOM", "AT+UNILWCFG", "AT+UNILWREG",
    "AT+UNILWSEND", "AT+UNILWSTATE", "AT+UNILWUNREG", "AT+UNILWUPDATE",
    "AT+UNILWVER", "AT+UNIMQTTCON", "AT+UNIMQTTDISCON",
    "AT+UNIMQTTPUB", "AT+UNIMQTTSTATE", "AT+UNIMQTTSUB",
    "AT+UNINETLOG", "AT+UNIPSMSET", "AT+UNISHCERTINFO",
    "AT+V", "AT+VBAT", "AT+VTD", "AT+VTS",
    "AT+W", "AT+WORKLOCK", "AT+XYACT", "AT+XYDMP", "AT+XYPERF",
    "AT+ZDMSWITCH", "AT+ZUEDMACTIVE",
    "ATD", "ATH", "ATO", "ATQ", "ATS0", "ATS3", "ATS4", "ATS5",
    "ATV", "ATZ",
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