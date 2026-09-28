#!/usr/bin/env python3
"""PRISM SW update - installs a release bundle on the PRISM device.

Run on the device (the CM4 inside the PRISM C100), as root:

    sudo prism-swupdate prism-release-1.0.tar.gz        # update from a bundle
    prism-swupdate --info                               # what is installed now

`prism-swupdate` is put in place by the install.  On a device that has never
been installed, unpack the bundle and run the copy inside it:

    tar xzf prism-release-1.0.tar.gz
    sudo python3 prism-release-1.0/prismlib/tools/swupdate.py prism-release-1.0.tar.gz

A release bundle holds the firmware of the measurement unit (AM2431) and the
prismlib source tree that was tested with it:

    prism-release-<release>.tar.gz
    +-- prism-release-<release>/
        +-- manifest.json
        +-- firmware/PRISM_ADC.rtfw
        +-- prismlib/          Makefile, include/, lib/, python/, tools/, install.sh ...

The archive is unpacked into a temporary folder that is removed afterwards.
What stays on the device is the installed library and, in
/usr/local/share/prismlib/, the tools: uninstall.sh, swupdate.py (linked as
/usr/local/bin/prism-swupdate) and installed.json.

Steps:

    1. unpack, check the manifest and the firmware image hash
    2. reach the measurement unit (nothing is changed if it cannot be reached)
    3. prismlib : install.sh   (make, make install, pip install)
    4. firmware : erase, write, verify  - a few seconds, do NOT cut power
    5. read back what is installed and compare with the manifest

A component that is already installed - same version and same content - is
skipped, unless --force is given (then both are written again).  A downgrade
is allowed after an explicit confirmation.  --firmware-only skips step 3 and
leaves prismlib as it is (install_scp.bat uses it right after installing
prismlib from its own folder).

Needs root for step 3.  Needs no internet.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

DEFAULT_IP = "192.168.7.1"
DEFAULT_PORT = 7777

# Firmware image header (.rtfw = 64-byte header + image body).
# Must match PRISM_ADC/src/BSP/drivers/fw_hdr.h.
FW_HDR_MAGIC = 0x57465452          # 'RTFW' little-endian
FW_HDR_BYTES = 64
FW_HASHED_HDR = 32                 # SHA-256 covers these bytes plus the body
# magic, hdr_rev, sw_rev[3], len, rsvd0, product[4], build_str[8], rsvd1
HDR_FMT = "<IB3sII4s8sI"

# Command protocol (TCP 7777)
#   request   [cmd][len][payload]
#   response  [cmd][status][plen][payload]
CMD_FW_BEGIN = 0x60
CMD_FW_DATA = 0x61
CMD_FW_END = 0x62
CMD_FW_INFO = 0x63

STATUS_OK = 0x00
STATUS_BUSY = 0xFE

CHUNK = 128                        # two chunks per 256-byte flash page

# Record of the last prismlib install: version and a hash of the source it
# was built from.  Used to skip an identical prismlib on the next update.
INSTALLED_RECORD = Path("/usr/local/share/prismlib/installed.json")
SOURCE_PARTS = ("Makefile", "install.sh", "include", "lib", "python")
SOURCE_SKIP_DIRS = {"__pycache__", "build", ".pytest_cache"}
SOURCE_SKIP_SUFFIX = {".pyc", ".pyo", ".o", ".so", ".d"}


class Fail(Exception):
    pass


# ---------------------------------------------------------------------------
# Firmware image
# ---------------------------------------------------------------------------

def decode_header(raw: bytes) -> dict:
    magic, rev, sw_rev, length, _r0, product, build, _r1 = struct.unpack(
        HDR_FMT, raw[:FW_HASHED_HDR])
    return {
        "magic": magic,
        "hdr_rev": rev,
        "sw_rev": "%d.%d.%d" % tuple(sw_rev),
        "len": length,
        "product": product.decode("ascii", "replace"),
        "build_str": build.rstrip(b"\0").decode("ascii", "replace"),
        "hash": raw[FW_HASHED_HDR:FW_HDR_BYTES],
    }


def split_package(data: bytes) -> tuple:
    """Split an .rtfw file into (header, body, decoded header)."""
    if len(data) < FW_HDR_BYTES + 1:
        raise Fail("Firmware file is too short - is this an .rtfw file?")
    header, body = data[:FW_HDR_BYTES], data[FW_HDR_BYTES:]
    (magic,) = struct.unpack_from("<I", header, 0)
    if magic != FW_HDR_MAGIC:
        raise Fail("Firmware file has no header (0x%08X)." % magic)
    h = decode_header(header)
    if h["len"] != len(body):
        raise Fail("Firmware header length (%d) does not match the body (%d)."
                   % (h["len"], len(body)))
    calc = hashlib.sha256(header[:FW_HASHED_HDR] + body).digest()
    if calc != h["hash"]:
        raise Fail("Firmware file is corrupt - hash mismatch.")
    return header, body, h


# ---------------------------------------------------------------------------
# Device link
# ---------------------------------------------------------------------------

def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        part = sock.recv(n - len(buf))
        if not part:
            raise Fail("Connection to the device was lost.")
        buf += part
    return buf


def command(sock: socket.socket, cmd: int, payload: bytes = b"", timeout: float = 5.0):
    """Send one command and return (status, payload).  The whole response
    payload is always read so the stream stays aligned."""
    if len(payload) > 255:
        raise Fail("Payload exceeds 255 bytes (%d)" % len(payload))
    sock.settimeout(timeout)
    sock.sendall(bytes([cmd, len(payload)]) + payload)
    head = recv_exact(sock, 3)
    if head[0] != cmd:
        raise Fail("Unexpected response command 0x%02X (expected 0x%02X)" % (head[0], cmd))
    body = recv_exact(sock, head[2]) if head[2] else b""
    return head[1], body


def status_text(st: int) -> str:
    if st == STATUS_OK:
        return "OK"
    if st == STATUS_BUSY:
        return "BUSY - a scan is running; stop it and retry"
    return "ERROR (0x%02X)" % st


def fw_info(sock: socket.socket):
    """Header of the image recorded on the unit, or None if none is recorded."""
    st, body = command(sock, CMD_FW_INFO)
    if st != STATUS_OK or len(body) != FW_HDR_BYTES:
        return None
    return decode_header(body)


def fw_write(sock: socket.socket, header: bytes, body: bytes) -> bytes:
    """Erase, write and verify.  Returns the hash the unit computed."""
    print("  Erasing... (a few seconds - do not touch the power)")
    t0 = time.time()
    st, _ = command(sock, CMD_FW_BEGIN, header, timeout=60.0)
    if st != STATUS_OK:
        raise Fail("Firmware BEGIN failed: %s\n"
                   "  Nothing has been erased; the device is unchanged." % status_text(st))
    print("  Erased (%.1f s)" % (time.time() - t0))

    total, sent, shown = len(body), 0, -1
    t0 = time.time()
    while sent < total:
        part = body[sent:sent + CHUNK]
        st, _ = command(sock, CMD_FW_DATA, struct.pack("<I", sent) + part, timeout=10.0)
        if st != STATUS_OK:
            raise Fail("Firmware DATA failed at %d: %s\n"
                       "  WARNING: the image is partially written. "
                       "Do not power off - run the update again." % (sent, status_text(st)))
        sent += len(part)
        pct = sent * 100 // total
        if pct != shown:
            shown = pct
            print("\r  Writing... %3d%% (%d/%d)" % (pct, sent, total), end="", flush=True)
    print("\r  Written  100%% (%d bytes, %.1f s)" % (total, time.time() - t0))

    print("  Verifying...")
    st, got = command(sock, CMD_FW_END, timeout=60.0)
    if st != STATUS_OK:
        raise Fail("Firmware verify failed: %s\n"
                   "  WARNING: the device has no valid image. "
                   "Do not power off - run the update again." % status_text(st))
    return got


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def find_manifest(root: Path):
    """manifest.json at root, or one directory down."""
    if (root / "manifest.json").is_file():
        return root
    subs = [p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    if len(subs) == 1:
        return subs[0]
    return None


def unpack(archive: Path) -> Path:
    """Unpack into a temporary folder and return it.  The caller removes it."""
    tmpdir = Path(tempfile.mkdtemp(prefix="prism-swupdate-"))
    with tarfile.open(archive, "r:gz") as tar:
        try:
            tar.extractall(tmpdir, filter="data")     # Python 3.12+
        except TypeError:
            tar.extractall(tmpdir)
    return tmpdir


def load_bundle(where: Path) -> dict:
    """Locate, read and check a bundle.  Nothing on the device is touched.
    The result carries "tmpdir" when the bundle was unpacked here."""
    tmpdir = None
    if where.is_file():
        print("Unpacking: %s" % where)
        where = tmpdir = unpack(where)
    try:
        b = _load_tree(where)
    except Exception:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    b["tmpdir"] = tmpdir
    return b


def _load_tree(where: Path) -> dict:
    if not where.is_dir():
        raise Fail("Bundle not found: %s" % where)

    root = find_manifest(where)
    if root is None:
        raise Fail("No manifest.json - is this a release bundle? (%s)" % where)

    try:
        m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        release = m["release"]
        product = m["product"]
        fw_ver, fw_file, fw_sha = (m["firmware"]["version"], m["firmware"]["file"],
                                   m["firmware"]["sha256"])
        lib_ver, lib_dir = m["prismlib"]["version"], m["prismlib"]["dir"]
    except (ValueError, KeyError, TypeError) as e:
        raise Fail("Malformed manifest.json: %s" % e)

    fw_path = root / fw_file
    if not fw_path.is_file():
        raise Fail("Firmware file missing: %s" % fw_path)
    data = fw_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != fw_sha.lower():
        raise Fail("Firmware file does not match the sha256 in the manifest: %s" % fw_path)
    header, body, h = split_package(data)
    if h["product"] != product:
        raise Fail("Firmware product code (%s) differs from the manifest (%s)."
                   % (h["product"], product))
    if h["build_str"] != fw_ver:
        raise Fail("Firmware header version (%s) differs from the manifest (%s)."
                   % (h["build_str"], fw_ver))

    lib_path = root / lib_dir
    if not (lib_path / "install.sh").is_file():
        raise Fail("No install.sh in the prismlib tree: %s" % lib_path)
    hdr_ver = header_version(lib_path / "include" / "prismlib.h")
    if hdr_ver != lib_ver:
        raise Fail("prismlib.h version (%s) differs from the manifest (%s)."
                   % (hdr_ver or "?", lib_ver))

    return {
        "root": root, "release": release, "product": product,
        "fw_ver": fw_ver, "fw_header": header, "fw_body": body, "fw_hash": h["hash"],
        "lib_ver": lib_ver, "lib_dir": lib_path, "lib_sha": source_hash(lib_path),
    }


# ---------------------------------------------------------------------------
# prismlib
# ---------------------------------------------------------------------------

def header_version(header: Path):
    """'MAJOR.MINOR.PATCH' from PRISMLIB_VERSION_* in prismlib.h, or None."""
    try:
        text = header.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parts = []
    for name in ("MAJOR", "MINOR", "PATCH"):
        m = re.search(r"#define\s+PRISMLIB_VERSION_%s\s+(\d+)" % name, text)
        if not m:
            return None
        parts.append(m.group(1))
    return ".".join(parts)


def source_hash(lib_dir: Path) -> str:
    """SHA-256 over the contents and relative paths of the source files the
    install is made from, in a fixed order.  Build output is skipped."""
    h = hashlib.sha256()
    for part in SOURCE_PARTS:
        p = lib_dir / part
        files = [p] if p.is_file() else sorted(q for q in p.rglob("*") if q.is_file())
        for f in files:
            rel = f.relative_to(lib_dir)
            if (SOURCE_SKIP_DIRS & set(rel.parts)
                    or any(x.endswith(".egg-info") for x in rel.parts)
                    or f.suffix in SOURCE_SKIP_SUFFIX):
                continue
            h.update(rel.as_posix().encode())
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def read_record():
    try:
        return json.loads(INSTALLED_RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_record(release: str, version: str, sha: str, folder=None) -> None:
    rec = {
        "release": release,
        "version": version,
        "source_sha256": sha,
        "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if folder:
        rec["folder"] = str(folder)       # the install folder (install.sh ran there)
    INSTALLED_RECORD.parent.mkdir(parents=True, exist_ok=True)
    INSTALLED_RECORD.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    os.sync()


def install_folder(rec):
    """The folder prismlib was installed from with install.sh (~/prismlib), as the
    record names it - or None when there is none or it is gone."""
    f = (rec or {}).get("folder")
    if not f:
        return None
    p = Path(f)
    return p if (p / "install.sh").is_file() else None


def sync_folder(src: Path, dst: Path) -> None:
    """Bring the install folder to the bundle's prismlib - sources, examples,
    documents, tools - so it matches what is installed.  Files are copied over;
    nothing the user added (saved scans and the like) is deleted.  The examples
    are rebuilt and the folder goes back to its owner."""
    st = dst.stat()
    for s in sorted(src.rglob("*")):
        rel = s.relative_to(src)
        if (SOURCE_SKIP_DIRS & set(rel.parts)
                or any(x.endswith(".egg-info") for x in rel.parts)
                or s.suffix in SOURCE_SKIP_SUFFIX
                or rel.name in (".gitattributes", ".gitignore")):   # git's own files - not for the device
            continue
        d = dst / rel
        if s.is_dir():
            d.mkdir(exist_ok=True)
        elif s.is_file():
            shutil.copy2(s, d)
    # Rebuild the examples against the new sources, then drop the build
    # intermediates the way install.sh does.
    for c in (dst / "examples" / "c").glob("*.c"):
        c.with_suffix("").unlink(missing_ok=True)
    subprocess.run(["make", "examples"], cwd=dst, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    for pattern in ("lib/*.o", "lib/*.d", "libprismlib.so", "examples/c/*.d"):
        for p in dst.glob(pattern):
            p.unlink(missing_ok=True)
    for p in [dst] + list(dst.rglob("*")):
        try:
            os.lchown(p, st.st_uid, st.st_gid)
        except OSError:
            pass
    os.sync()


def installed_lib_version():
    """(package version, C library version) as a fresh interpreter sees them,
    or (None, None) when prismlib is not installed."""
    r = subprocess.run(
        [sys.executable, "-c",
         "import prismlib; print(prismlib.__version__); print(prismlib.version())"],
        capture_output=True, text=True, cwd="/")
    if r.returncode != 0:
        return None, None
    lines = r.stdout.split()
    return (lines + [None, None])[:2]


def lib_install(lib_dir: Path) -> None:
    subprocess.run(["make", "clean"], cwd=lib_dir, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    r = subprocess.run(["bash", "./install.sh"], cwd=lib_dir)
    if r.returncode != 0:
        raise Fail("prismlib install failed (install.sh exit code %d)\n"
                   "  The firmware was not touched." % r.returncode)
    # Flush the installed files to the eMMC now.  A reboot or power loss within
    # the next minute would otherwise leave zero-length files behind (ext4
    # delayed allocation) - seen on a development board on 2026-09-27.
    os.sync()


def give_back(tree: Path) -> None:
    """Under sudo, hand the tree to the user who ran the command."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid is None or gid is None:
        return
    for p in [tree] + list(tree.rglob("*")):
        try:
            os.lchown(p, int(uid), int(gid))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def show_info(ip: str, port: int) -> int:
    pkg, clib = installed_lib_version()
    print("prismlib")
    if pkg is None:
        print("  not installed")
    else:
        print("  package    : %s" % pkg)
        print("  C library  : %s" % clib)
        rec = read_record()
        if rec:
            print("  installed  : %s, source %s..., %s"
                  % ("release %s" % rec["release"] if rec.get("release")
                     else "from a folder with install.sh",
                     (rec.get("source_sha256") or "")[:12], rec.get("installed")))
        else:
            print("  installed  : no record")

    print("Firmware (%s:%d)" % (ip, port))
    try:
        with socket.create_connection((ip, port), timeout=5.0) as sock:
            h = fw_info(sock)
    except OSError as e:
        print("  connection failed: %s" % e)
        return 2
    if h is None:
        print("  no image header recorded (an update with this tool records one)")
    else:
        print("  version    : %s" % h["build_str"])
        print("  product    : %s" % h["product"])
        print("  size       : %d bytes" % h["len"])
        print("  SHA-256    : %s" % h["hash"].hex())
    return 0


def vtuple(v) -> tuple:
    """'1.2.3' -> (1, 2, 3)"""
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def change_label(old, new: str, same: bool, why: str) -> str:
    if same:
        return "(same - skipped)"
    if not old:
        return "(new install)"
    if old == new:
        return "(same version - %s, reinstalling)" % why
    if vtuple(old) > vtuple(new):
        return "(downgrade)"
    return ""


def do_update(b: dict, ip: str, port: int, yes: bool, force: bool = False,
              firmware_only: bool = False) -> int:
    old_pkg, old_clib = installed_lib_version()
    rec = read_record()
    # The install folder (~/prismlib) holds the sources, examples and documents
    # the user works with. An update used to install from the bundle and leave
    # that folder at the old version (2026-09-28: installed 1.1.1, folder 1.1.0).
    folder = install_folder(rec)
    folder_git = folder is not None and (folder / ".git").exists()
    folder_same = folder is None or folder_git or source_hash(folder) == b["lib_sha"]
    lib_same = (old_pkg == b["lib_ver"] and old_clib == b["lib_ver"]
                and rec is not None and rec.get("source_sha256") == b["lib_sha"])
    lib_why = "no install record" if rec is None else "source differs"

    try:
        sock = socket.create_connection((ip, port), timeout=5.0)
    except OSError as e:
        raise Fail("Cannot reach the measurement unit (%s:%d): %s\n"
                   "  Nothing was changed." % (ip, port, e))
    with sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        cur = fw_info(sock)
        cur_ver = cur["build_str"] if cur else None
        fw_same = cur is not None and cur["hash"] == b["fw_hash"]   # same image, not just same number
        if force:
            # --force: write both again even when identical (recovery, production re-flash).
            lib_same = fw_same = False
            lib_why = fw_why = "forced"
        else:
            fw_why = "different image"
        if force:
            folder_same = folder is None or folder_git
        if firmware_only:
            # --firmware-only: prismlib is left as it is (install_scp.bat has just
            # installed it from its own folder).
            lib_same = True
            folder_same = True

        print("Release %s  (%s)" % (b["release"], b["root"]))
        if firmware_only:
            print("  prismlib : %s  (left as it is - --firmware-only)" % (old_pkg or "none"))
        else:
            print("  prismlib : %s -> %s  %s" % (old_pkg or "none", b["lib_ver"],
                                                 change_label(old_pkg, b["lib_ver"], lib_same, lib_why)))
        print("  firmware : %s -> %s  %s" % (cur_ver or "not recorded", b["fw_ver"],
                                             change_label(cur_ver, b["fw_ver"], fw_same,
                                                          fw_why)))
        if folder is not None:
            print("  folder   : %s  %s" % (folder,
                  "(git checkout - not touched; update it with git pull)" if folder_git
                  else "(same)" if folder_same else "(refreshed to this release)"))
        if lib_same and fw_same and folder_same:
            print()
            print("Already this release. Nothing to do.")
            return 0

        downgrade = [name for name, old, new, same in
                     (("prismlib", old_pkg, b["lib_ver"], lib_same),
                      ("firmware", cur_ver, b["fw_ver"], fw_same))
                     if not same and old and vtuple(old) > vtuple(new)]
        print()
        if downgrade:
            print("WARNING: downgrade - the %s on the device is newer than this bundle."
                  % " and ".join(downgrade))
            print("  Do not proceed unless you need this specific version.")
        if not fw_same:
            print("WARNING: if power is lost during the firmware update the device will not boot.")
            print("  Do not touch the power or the USB cable; it takes a few seconds.")
        print()

        if not yes:
            try:
                if input("Type 'yes' to continue: ").strip().lower() != "yes":
                    print("Cancelled. Nothing was changed.")
                    return 1
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled. Nothing was changed.")
                return 1
            print()

        if lib_same:
            print("[1/2] prismlib  skipped (%s)"
                  % ("--firmware-only" if firmware_only else "same version, same source"))
        else:
            print("[1/2] Installing prismlib")
            lib_install(b["lib_dir"])
            # install.sh just recorded the unpacked bundle as its folder - keep the
            # real install folder in the record instead.
            write_record(b["release"], b["lib_ver"], b["lib_sha"], folder)
        if not folder_same:
            print("      refreshing %s" % folder)
            sync_folder(b["lib_dir"], folder)
        print()

        if fw_same:
            print("[2/2] firmware  skipped (same image)")
        else:
            print("[2/2] Updating firmware")
            try:
                fw_write(sock, b["fw_header"], b["fw_body"])
            except KeyboardInterrupt:
                raise Fail("Interrupted.\n"
                           "  WARNING: the image may be partially written. "
                           "Do not power off - run the update again.")
        print()

        pkg, clib = installed_lib_version()
        after = fw_info(sock)

    ok = True
    print("SW update complete - release %s" % b["release"])
    checks = [] if firmware_only else [("prismlib package", pkg, b["lib_ver"]),
                                       ("prismlib C library", clib, b["lib_ver"])]
    checks.append(("firmware", after["build_str"] if after else None, b["fw_ver"]))
    for name, got, want in checks:
        mark = "OK" if got == want else "MISMATCH"
        ok = ok and got == want
        print("  %-20s %-8s (expected %s)  %s" % (name, got or "none", want, mark))
    if not fw_same:
        print("  Reboot the device to run the new firmware.")
    print()
    print(USAGE_EXAMPLES)
    return 0 if ok else 1


# Printed by --help, when no bundle is given, and after every update - the same
# text everywhere, so the options are never only in the manual.
USAGE_EXAMPLES = """usage examples (on the device):
  sudo prism-swupdate prism-release-x.y.tar.gz
      update what differs from the bundle - items already at its version are skipped
  sudo prism-swupdate prism-release-x.y.tar.gz --force
      reinstall prismlib and rewrite the firmware even if already this release
  sudo prism-swupdate prism-release-x.y.tar.gz --firmware-only
      update the firmware only, leave prismlib as it is
  prism-swupdate --info
      show the installed prismlib and firmware
  sudo bash /usr/local/share/prismlib/uninstall.sh
      remove prismlib

A downgrade asks first. After a firmware update, reboot the device to run it.
No internet needed."""


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="prism-swupdate",
        description="PRISM SW update - installs firmware and prismlib together",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=USAGE_EXAMPLES)
    ap.add_argument("bundle", nargs="?",
                    help="prism-release-<release>.tar.gz or an unpacked folder "
                         "(default: the bundle this file is inside of, if any)")
    ap.add_argument("--ip", default=DEFAULT_IP,
                    help="measurement unit address (default %s)" % DEFAULT_IP)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--info", action="store_true", help="show installed versions only")
    ap.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--force", action="store_true",
                    help="reinstall prismlib and rewrite the firmware even if they are "
                         "already this release")
    ap.add_argument("--firmware-only", action="store_true",
                    help="update the firmware only and leave prismlib as it is")
    # install.sh calls this at its end. Without the record an update - and the
    # EOL tester's version check - cannot tell that the installed prismlib is the
    # bundle's, and installs it again (2026-09-27: install_scp.bat, then EOL
    # reinstalled prismlib). Not for users, so not in --help.
    ap.add_argument("--record-install", metavar="DIR", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.info:
        return show_info(args.ip, args.port)

    if os.geteuid() != 0:
        print("Run as root: sudo python3 %s ..." % Path(sys.argv[0]).name, file=sys.stderr)
        return 2

    if args.record_install:
        pkg, _clib = installed_lib_version()
        if pkg is None:
            print("prismlib is not installed - no record written", file=sys.stderr)
            return 1
        where = Path(args.record_install).resolve()
        sha = source_hash(where)
        write_record("", pkg, sha, where)
        print("  record     : %s (prismlib %s, source %s...)" % (INSTALLED_RECORD, pkg, sha[:12]))
        return 0

    if args.bundle:
        where = Path(args.bundle)
    else:
        where = Path(__file__).resolve().parent.parent.parent
        if find_manifest(where) is None:
            print("Give the bundle to install.\n", file=sys.stderr)
            ap.print_help(sys.stderr)
            return 2
    b = None
    try:
        b = load_bundle(where.resolve())
        return do_update(b, args.ip, args.port, args.yes, args.force, args.firmware_only)
    except Fail as e:
        print("\n%s" % e, file=sys.stderr)
        return 1
    except (OSError, socket.timeout) as e:
        print("\nCommunication failed: %s" % e, file=sys.stderr)
        return 2
    finally:
        if b is not None:
            if b["tmpdir"] is not None:
                shutil.rmtree(b["tmpdir"], ignore_errors=True)   # nothing stays behind
            else:
                give_back(b["root"])   # a folder the user unpacked: no root-owned build output


if __name__ == "__main__":
    sys.exit(main())
