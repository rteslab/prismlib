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
    SR_64K  = 0
    SR_128K = 1
    SR_170K = 2
    SR_256K = 3
    SR_512K = 4

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
