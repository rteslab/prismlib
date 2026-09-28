# constants.py — PRISM-PyLib shared constants

# Result codes
RESULT_SUCCESS           =   0
RESULT_BAD_PARAMETER     =  -1
RESULT_BUSY              =  -2
RESULT_TIMEOUT           =  -3
RESULT_LOCK_TIMEOUT      =  -4
RESULT_RESOURCE_UNAVAIL  =  -6
RESULT_COMMS_FAILURE     =  -7
RESULT_UNDEFINED         = -10

# Sample rate enum (matches PrismSampleRate_e in prismlib.h)
class SampleRate:
    """Per-channel sample rate.  Value 2 is not supported."""
    SR_64K  = 0       #  64,000 S/s
    SR_128K = 1       # 128,000 S/s
    SR_256K = 3       # 256,000 S/s
    SR_512K = 4       # 512,000 S/s (max)
    SR_32K  = 5       #  32,000 S/s
    SR_16K  = 6       #  16,000 S/s
    SR_8K   = 7       #   8,000 S/s
    SR_4K   = 8       #   4,000 S/s
    SR_2K   = 9       #   2,000 S/s
    SR_1K   = 10      #   1,000 S/s
    SR_500  = 11      #     500 S/s (min)


#: Indexed by SampleRate: data rate in S/s.  0 marks an unsupported value.
SR_HZ = [64000, 128000, 0, 256000, 512000,
         32000, 16000, 8000, 4000, 2000, 1000, 500]

#: Indexed by SampleRate: short display name.  None marks an unsupported value.
SR_NAME = ["64K", "128K", None, "256K", "512K",
           "32K", "16K", "8K", "4K", "2K", "1K", "500"]


def sr_hz(sample_rate: int) -> int:
    """Return the data rate in S/s, or 0 if unsupported."""
    if 0 <= sample_rate < len(SR_HZ):
        return SR_HZ[sample_rate]
    return 0


def sr_name(sample_rate: int) -> str:
    """Return the short display name, or '?' if unsupported."""
    if 0 <= sample_rate < len(SR_NAME) and SR_NAME[sample_rate]:
        return SR_NAME[sample_rate]
    return "?"


def sr_supported() -> list:
    """Return supported rates as (enum, hz, name), slowest first."""
    out = [(i, SR_HZ[i], SR_NAME[i]) for i in range(len(SR_HZ)) if SR_HZ[i] > 0]
    return sorted(out, key=lambda t: t[1])

# Scan options (OR-combine)
class ScanOptions:
    DEFAULT          = 0x0000
    NOSCALEDATA      = 0x0001
    NOCALIBRATEDATA  = 0x0002
    CONTINUOUS       = 0x0010
    TCP_DATA         = 0x0020   # push scan data over TCP instead of UDP

# Scan status flags
class ScanStatus:
    HW_OVERRUN     = 0x0001
    BUFFER_OVERRUN = 0x0002
    DATA_LOST      = 0x0004   # UDP data frame(s) never arrived (sticky) - see scan_lost()
    RUNNING        = 0x0008

# Command IDs (wire protocol)
CMD_OPEN          = 0x01
CMD_CLOSE         = 0x02
CMD_IS_OPEN       = 0x03
CMD_INFO          = 0x04
CMD_FW_VERSION    = 0x06
CMD_SERIAL        = 0x07
CMD_CAL_DATE      = 0x10
CMD_CAL_READ      = 0x11
CMD_IEPE_READ     = 0x20
CMD_IEPE_WRITE    = 0x21
CMD_IEPE_DIAG     = 0x22
CMD_SAMPLERATE_READ  = 0x40
CMD_SAMPLERATE_WRITE = 0x41
CMD_SCAN_START    = 0x50
CMD_SCAN_STOP     = 0x51
CMD_SCAN_DATA     = 0x52
CMD_SCAN_STATUS   = 0x53
CMD_SCAN_CLEANUP  = 0x54
CMD_SCAN_CH_COUNT = 0x55
CMD_SCAN_BUF_SIZE = 0x56

# Server status bytes
SRV_OK           = 0x00
SRV_HW_OVERRUN   = 0x01
SRV_SCAN_STOPPED = 0x02
SRV_BUSY         = 0xFE
SRV_BAD_PARAM    = 0xFF

# GPIO LED identifiers (CM4)
class LedId:
    PWR   = 21   # ACT LED  — /sys/class/leds/ACT/ (default ON  at open)
    ERR   = 20   # GPIO 20  — gpiod               (default OFF at open)
    ALARM = 16   # GPIO 16  — gpiod               (default OFF at open)

# Scan frame constants
# Header: [CMD:1][N_SMPL_LO:1][N_SMPL_HI:1][STATUS:1]  (LE uint16 n_samples)
SCAN_HEADER_SIZE  = 4
SCAN_N_SAMPLES_MAX = 512
SCAN_N_CH         = 4
SCAN_BYTES_PER_S  = 3
SCAN_PAYLOAD_MAX  = SCAN_N_SAMPLES_MAX * SCAN_N_CH * SCAN_BYTES_PER_S  # 6144 B
