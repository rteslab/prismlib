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

# Scan options (OR-combine)
class ScanOptions:
    DEFAULT          = 0x0000
    NOSCALEDATA      = 0x0001
    NOCALIBRATEDATA  = 0x0002
    CONTINUOUS       = 0x0010

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
CMD_CLOCK_READ    = 0x40
CMD_CLOCK_WRITE   = 0x41
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

# Scan frame constants
SCAN_N_SAMPLES    = 64
SCAN_N_CH         = 4
SCAN_BYTES_PER_S  = 3
SCAN_PAYLOAD_SIZE = SCAN_N_SAMPLES * SCAN_N_CH * SCAN_BYTES_PER_S  # 768
SCAN_FRAME_SIZE   = 3 + SCAN_PAYLOAD_SIZE                           # 771
