"""
shm_tracer.py — Trace shared memory regions accessed by any process (e.g. SimHub)
===================================================================================
Uses NtQuerySystemInformation(SystemExtendedHandleInformation) to enumerate all
open kernel handles system-wide, filters to the target process, duplicates each
Section handle into our process, then calls NtQueryObject to get its name.

Usage:
    python shm_tracer.py                    # auto-find SimHub
    python shm_tracer.py --name SimHub
    python shm_tracer.py --pid 1234
    python shm_tracer.py --all-types        # show all handle types
    python shm_tracer.py --watch 3          # rescan every N seconds
    python shm_tracer.py --dump-regions     # hex dump AC SHM regions

Requires: Windows, run as Administrator
"""

import ctypes
import ctypes.wintypes
import struct
import sys
import os
import time
import argparse
import mmap
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# WinAPI setup
# ─────────────────────────────────────────────────────────────────────────────

ntdll    = ctypes.windll.ntdll
kernel32 = ctypes.windll.kernel32
psapi    = ctypes.windll.psapi
advapi32 = ctypes.windll.advapi32

# ── Set explicit return types on kernel32 functions ──────────────────────────
# Without this ctypes defaults to c_int (32-bit), truncating HANDLE values
# on x64 Windows and causing every handle comparison / DuplicateHandle to fail.
kernel32.OpenProcess.restype            = ctypes.wintypes.HANDLE
kernel32.GetCurrentProcess.restype      = ctypes.wintypes.HANDLE
kernel32.CreateFileMappingW.restype     = ctypes.wintypes.HANDLE
kernel32.DuplicateHandle.restype        = ctypes.wintypes.BOOL
kernel32.CloseHandle.restype            = ctypes.wintypes.BOOL
kernel32.MapViewOfFile.restype          = ctypes.c_void_p
kernel32.UnmapViewOfFile.restype        = ctypes.wintypes.BOOL
ntdll.NtQuerySystemInformation.restype  = ctypes.c_long
ntdll.NtQueryObject.restype             = ctypes.c_long

# NTSTATUS is a signed 32-bit int in ctypes, so error codes (bit 31 set)
# come back as negative numbers. Always use nt_code() before comparing.
def nt_code(s) -> int:
    return int(s) & 0xFFFFFFFF


# ── SeDebugPrivilege ──────────────────────────────────────────────────────────
# Being admin grants the privilege but doesn't activate it.
# DuplicateHandle across processes requires it to be explicitly enabled.

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY             = 0x0008
SE_DEBUG_NAME           = "SeDebugPrivilege"
SE_PRIVILEGE_ENABLED    = 0x0002

class LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", ctypes.c_ulong)]

class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", ctypes.c_ulong),
                ("Privileges",     LUID_AND_ATTRIBUTES * 1)]

def enable_debug_privilege() -> bool:
    h_token = ctypes.wintypes.HANDLE()
    # GetCurrentProcess() returns a pseudo-handle (0xFFFFFFFFFFFFFFFF on x64).
    # Pass it as c_void_p so ctypes doesn't try to truncate it to 32 bits.
    cur_proc = ctypes.cast(kernel32.GetCurrentProcess(), ctypes.c_void_p)
    if not advapi32.OpenProcessToken(
        cur_proc,
        ctypes.c_ulong(TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY),
        ctypes.byref(h_token),
    ):
        return False
    luid = LUID()
    if not advapi32.LookupPrivilegeValueW(None, SE_DEBUG_NAME, ctypes.byref(luid)):
        kernel32.CloseHandle(h_token)
        return False
    tp = TOKEN_PRIVILEGES()
    tp.PrivilegeCount           = 1
    tp.Privileges[0].Luid       = luid
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
    ok = advapi32.AdjustTokenPrivileges(
        h_token, False, ctypes.byref(tp),
        ctypes.c_ulong(ctypes.sizeof(tp)), None, None,
    )
    kernel32.CloseHandle(h_token)
    return bool(ok)

STATUS_SUCCESS              = 0x00000000
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
STATUS_BUFFER_OVERFLOW      = 0x80000005
STATUS_BUFFER_TOO_SMALL     = 0xC0000023

PROCESS_DUP_HANDLE        = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ           = 0x0010
DUPLICATE_SAME_ACCESS     = 0x0002
PAGE_READWRITE            = 0x04

# Use class 64 (SystemExtendedHandleInformation) not class 16.
# Class 16 truncates ProcessId and HandleValue to 16 bits on x64 Windows,
# causing PID mismatches. Class 64 uses pointer-sized (64-bit) fields.
SystemExtendedHandleInformation = 64

ObjectNameInformation = 1
ObjectTypeInformation = 2


# ─────────────────────────────────────────────────────────────────────────────
# Extended handle table entry — 40 bytes on x64
# ─────────────────────────────────────────────────────────────────────────────

class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object",                ctypes.c_void_p),   # 8
        ("UniqueProcessId",       ctypes.c_size_t),   # 8  (full 64-bit PID)
        ("HandleValue",           ctypes.c_size_t),   # 8  (full 64-bit handle)
        ("GrantedAccess",         ctypes.c_ulong),    # 4
        ("CreatorBackTraceIndex", ctypes.c_ushort),   # 2
        ("ObjectTypeIndex",       ctypes.c_ushort),   # 2
        ("HandleAttributes",      ctypes.c_ulong),    # 4
        ("Reserved",              ctypes.c_ulong),    # 4
    ]  # = 40 bytes

ENTRY_SIZE = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)


# ─────────────────────────────────────────────────────────────────────────────
# Process helpers
# ─────────────────────────────────────────────────────────────────────────────

def find_pids_by_name(substr: str) -> list:
    results  = []
    lower    = substr.lower()
    pid_arr  = (ctypes.wintypes.DWORD * 4096)()
    returned = ctypes.wintypes.DWORD()
    psapi.EnumProcesses(pid_arr, ctypes.sizeof(pid_arr), ctypes.byref(returned))
    count = returned.value // ctypes.sizeof(ctypes.wintypes.DWORD)
    buf   = ctypes.create_unicode_buffer(512)
    for pid in pid_arr[:count]:
        if not pid:
            continue
        h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            continue
        psapi.GetModuleFileNameExW(h, None, buf, 512)
        kernel32.CloseHandle(h)
        if buf.value and lower in buf.value.lower():
            results.append((int(pid), buf.value))
    return results


def get_process_exe(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return f"<pid {pid}>"
    buf = ctypes.create_unicode_buffer(512)
    psapi.GetModuleFileNameExW(h, None, buf, 512)
    kernel32.CloseHandle(h)
    return buf.value or f"<pid {pid}>"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Enumerate all system handles
# ─────────────────────────────────────────────────────────────────────────────

def get_all_handles() -> list:
    """
    Calls NtQuerySystemInformation(SystemExtendedHandleInformation=64).
    Buffer grows automatically; prints progress so you can see it working.

    Buffer layout:
        [0..7]   NumberOfHandles  (ULONG_PTR, 8 bytes)
        [8..15]  Reserved         (ULONG_PTR, 8 bytes)
        [16..]   Entries          (40 bytes each)
    """
    buf_size = 8 * 1024 * 1024   # start at 8 MB — .NET apps have massive handle tables

    for attempt in range(20):
        print(f"    attempt {attempt+1}: allocating {buf_size//1024}KB … ", end="", flush=True)
        buf     = ctypes.create_string_buffer(buf_size)
        ret_len = ctypes.c_ulong(0)

        status = ntdll.NtQuerySystemInformation(
            ctypes.c_int(SystemExtendedHandleInformation),
            buf,
            ctypes.c_ulong(buf_size),
            ctypes.byref(ret_len),
        )
        code = nt_code(status)

        if code == STATUS_SUCCESS:
            print("ok")
            break

        if code == STATUS_INFO_LENGTH_MISMATCH:
            reported = ret_len.value
            needed   = reported if reported > buf_size else buf_size * 2
            buf_size = needed + 1024 * 512    # 512 KB headroom
            print(f"buffer too small (need ~{reported//1024}KB), retrying…")
            continue

        print(f"FAILED")
        raise RuntimeError(f"NtQuerySystemInformation(64) failed: 0x{code:08X}")
    else:
        raise RuntimeError("NtQuerySystemInformation: could not fit after 20 attempts")

    # Parse: first 8 bytes = count (ULONG_PTR), next 8 = reserved, then entries
    count = struct.unpack_from("<Q", buf.raw, 0)[0]
    base  = 16   # skip NumberOfHandles(8) + Reserved(8)
    raw   = buf.raw

    entries = []
    for i in range(count):
        off   = base + i * ENTRY_SIZE
        chunk = raw[off : off + ENTRY_SIZE]
        if len(chunk) < ENTRY_SIZE:
            break
        entries.append(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_buffer_copy(chunk))

    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Determine Section ObjectTypeIndex without calling NtQueryObject
# ─────────────────────────────────────────────────────────────────────────────

def get_section_type_index(all_handles: list) -> int | None:
    """
    Create a real anonymous Section in our own process, find it in the handle
    table by matching PID + handle value, and read its ObjectTypeIndex.
    This lets us pre-filter without ever calling NtQueryObject for type.

    IMPORTANT: CreateFileMappingW.restype must be set to HANDLE (pointer-sized)
    before calling this — otherwise ctypes truncates the value on x64.
    """
    our_pid = os.getpid()
    tmp = kernel32.CreateFileMappingW(
        ctypes.wintypes.HANDLE(-1), None,
        ctypes.c_ulong(PAGE_READWRITE),
        ctypes.c_ulong(0), ctypes.c_ulong(4096),
        None,
    )
    if not tmp:
        print(f"    CreateFileMappingW failed: {kernel32.GetLastError()}")
        return None

    try:
        # HANDLE is a void pointer — cast to size_t for numeric comparison
        tmp_val = ctypes.cast(tmp, ctypes.c_void_p).value
        if tmp_val is None:
            return None
        for entry in all_handles:
            if entry.UniqueProcessId == our_pid and entry.HandleValue == tmp_val:
                return entry.ObjectTypeIndex
        # If not found by exact value, dump nearby entries to diagnose
        our_entries = [e for e in all_handles if e.UniqueProcessId == our_pid]
        print(f"    Debug: our PID {our_pid} has {len(our_entries)} handles in table")
        print(f"    Debug: looking for HandleValue=0x{tmp_val:x}")
        if our_entries:
            vals = sorted(e.HandleValue for e in our_entries)
            print(f"    Debug: first 10 handle values: {[hex(v) for v in vals[:10]]}")
    finally:
        kernel32.CloseHandle(tmp)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Query handle name
# ─────────────────────────────────────────────────────────────────────────────

def query_handle_name(handle) -> str | None:
    """
    Call NtQueryObject(ObjectNameInformation) and decode.

    x64 output buffer layout:
        bytes 0-1:   USHORT Length         (byte count of string, no null)
        bytes 2-3:   USHORT MaximumLength
        bytes 4-7:   4 bytes padding        (alignment for 8-byte pointer)
        bytes 8-15:  PVOID Buffer           (points into this same buffer)
        bytes 16+:   wchar_t string data    (UTF-16LE)
    """
    buf_size = 1024
    for _ in range(2):
        buf     = ctypes.create_string_buffer(buf_size)
        ret_len = ctypes.c_ulong(0)
        status  = ntdll.NtQueryObject(
            handle,
            ctypes.c_int(ObjectNameInformation),
            buf,
            ctypes.c_ulong(buf_size),
            ctypes.byref(ret_len),
        )
        code = nt_code(status)

        if code in (STATUS_BUFFER_OVERFLOW, STATUS_BUFFER_TOO_SMALL) or \
                (ret_len.value > 0 and ret_len.value > buf_size):
            buf_size = ret_len.value + 64
            continue

        if code != STATUS_SUCCESS:
            return None
        break
    else:
        return None

    if len(buf.raw) < 2:
        return None
    length_bytes = struct.unpack_from("<H", buf.raw, 0)[0]
    if length_bytes == 0:
        return ""

    # String data starts at offset 16 on x64
    start = 16
    end   = start + length_bytes
    if end > len(buf.raw):
        return None
    try:
        return buf.raw[start:end].decode("utf-16-le")
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main enumeration
# ─────────────────────────────────────────────────────────────────────────────

def enumerate_process_handles(target_pid: int, type_filter: str | None = "Section") -> list:
    """
    Key design: NtQueryObject hangs on I/O completion ports, named pipes,
    and other sync objects — very common in .NET processes like SimHub.

    We avoid calling NtQueryObject for type entirely:
      1. Create a known Section in our own process
      2. Find its ObjectTypeIndex from the raw handle table (no API call)
      3. Pre-filter handles by that index before duplicating anything
      4. Only call NtQueryObject(name) on the small filtered set
    """
    our_process = ctypes.cast(kernel32.GetCurrentProcess(), ctypes.c_void_p)

    print(f"\n  Enumerating system handle table…")
    all_handles = get_all_handles()
    print(f"  Total handles system-wide: {len(all_handles)}")

    target_handles = [h for h in all_handles if h.UniqueProcessId == target_pid]
    print(f"  Handles in PID {target_pid}: {len(target_handles)}")

    if not target_handles:
        print(f"\n  ⚠️  No handles found for PID {target_pid}.")
        print(f"  The process may have restarted — re-run the script.")
        return []

    # Find Section type index
    section_idx = None
    if type_filter and type_filter.lower() == "section":
        section_idx = get_section_type_index(all_handles)
        if section_idx is not None:
            print(f"  Section ObjectTypeIndex = {section_idx}  (pre-filtering by index)")
        else:
            print(f"  ⚠️  Could not find Section type index — will query all handles")

    # Pre-filter by type index to avoid NtQueryObject hangs
    if section_idx is not None:
        candidates = [h for h in target_handles if h.ObjectTypeIndex == section_idx]
    else:
        candidates = target_handles

    print(f"  Candidates after type filter: {len(candidates)}")

    if len(candidates) == 0:
        print(f"  ⚠️  No Section handles found.")
        type_counts = {}
        for h in target_handles:
            type_counts[h.ObjectTypeIndex] = type_counts.get(h.ObjectTypeIndex, 0) + 1
        print(f"  Type index distribution in this process:")
        for idx, count in sorted(type_counts.items(), key=lambda x: -x[1])[:10]:
            marker = "  ← expected Section" if section_idx and idx == section_idx else ""
            print(f"    index {idx:>3}: {count} handles{marker}")
        return []

    # Open target for duplication
    target_proc = kernel32.OpenProcess(
        ctypes.c_ulong(PROCESS_DUP_HANDLE), False, ctypes.c_ulong(target_pid)
    )
    if not target_proc:
        err = kernel32.GetLastError()
        raise PermissionError(
            f"Cannot open PID {target_pid} for PROCESS_DUP_HANDLE "
            f"(error {err}) — run as Administrator and ensure SeDebugPrivilege is enabled"
        )

    our_process = ctypes.cast(kernel32.GetCurrentProcess(), ctypes.c_void_p)
    results  = []
    dup_ok   = 0
    dup_fail = 0
    fail_err = {}

    for entry in candidates:
        dup = ctypes.wintypes.HANDLE()
        ok  = kernel32.DuplicateHandle(
            target_proc,
            ctypes.c_size_t(entry.HandleValue),
            our_process,
            ctypes.byref(dup),
            ctypes.c_ulong(0),
            False,
            ctypes.c_ulong(DUPLICATE_SAME_ACCESS),
        )
        if not ok:
            err = kernel32.GetLastError()
            fail_err[err] = fail_err.get(err, 0) + 1
            dup_fail += 1
            continue
        dup_ok += 1

        try:
            name = query_handle_name(dup) or ""
        finally:
            kernel32.CloseHandle(dup)

        results.append({
            "handle": entry.HandleValue,
            "type":   "Section" if section_idx is not None else "?",
            "name":   name,
            "access": entry.GrantedAccess,
        })

    kernel32.CloseHandle(target_proc)

    named = sum(1 for r in results if r["name"])
    print(f"  DuplicateHandle: {dup_ok} ok / {dup_fail} failed")
    if fail_err:
        for err, count in sorted(fail_err.items(), key=lambda x: -x[1]):
            # Common codes: 5=ACCESS_DENIED, 6=INVALID_HANDLE, 87=INVALID_PARAM
            meaning = {5: "ACCESS_DENIED (need SeDebugPrivilege)",
                       6: "INVALID_HANDLE (handle closed between scan and dup)",
                       87: "INVALID_PARAMETER"}.get(err, "")
            print(f"    error {err}: {count} times  {meaning}")
    print(f"  Named sections : {named} of {len(results)}")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

AC_KNOWN = {"acpmf_physics", "acpmf_graphics", "acpmf_static"}


def dump_section_bytes(kernel_name: str, max_size: int = 8192) -> bytes | None:
    name = kernel_name
    for prefix in (
        "\\BaseNamedObjects\\",
        "\\Sessions\\1\\BaseNamedObjects\\",
        "\\Sessions\\2\\BaseNamedObjects\\",
        "\\Sessions\\3\\BaseNamedObjects\\",
    ):
        if name.startswith(prefix):
            name = "Local\\" + name[len(prefix):]
            break
    try:
        mm   = mmap.mmap(-1, max_size, tagname=name, access=mmap.ACCESS_READ)
        data = mm.read(max_size)
        mm.close()
        return data
    except Exception:
        return None


def hex_dump_short(data: bytes, rows: int = 4) -> str:
    lines = []
    for i in range(0, min(len(data), rows * 16), 16):
        chunk = data[i:i+16]
        h = " ".join(f"{b:02x}" for b in chunk)
        a = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"      {i:04x}  {h:<48}  {a}")
    return "\n".join(lines)


def print_report(pid: int, exe: str, handles: list, dump: bool):
    print(f"\n{'═'*65}")
    print(f"  {os.path.basename(exe)}   PID {pid}   {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'═'*65}")

    if not handles:
        print("\n  No Section handles to report.")
        return

    ac    = [h for h in handles if any(k in h["name"] for k in AC_KNOWN)]
    other = [h for h in handles if h not in ac]

    if ac:
        print(f"\n  ✅  ASSETTO CORSA SHARED MEMORY ({len(ac)} regions)\n")
        for h in ac:
            base = h["name"].split("\\")[-1]
            print(f"    0x{h['handle']:04x}  {base}")
            print(f"           full name : {h['name']}")
            print(f"           access    : 0x{h['access']:08x}")
            if dump:
                raw = dump_section_bytes(h["name"])
                if raw:
                    print(f"           first bytes:")
                    print(hex_dump_short(raw))
            print()
    else:
        print("\n  ℹ️  No AC/ACEvo shared memory regions found.")
        print("      (Is AC Evo running and in a session?)")

    if other:
        named   = sorted([h for h in other if h["name"]], key=lambda h: h["name"])
        unnamed = [h for h in other if not h["name"]]
        print(f"\n  Other Section handles: {len(named)} named, {len(unnamed)} unnamed\n")
        prev_ns = None
        for h in named:
            ns = h["name"].rsplit("\\", 1)[0] if "\\" in h["name"] else ""
            if ns != prev_ns:
                print(f"    [{ns or 'root'}]")
                prev_ns = ns
            base = h["name"].split("\\")[-1]
            print(f"      0x{h['handle']:04x}  {base:<44}  0x{h['access']:08x}")
        if unnamed:
            print(f"\n    [(unnamed — {len(unnamed)} handles)]")

    all_names = sorted(set(h["name"] for h in handles if h["name"]))
    if all_names:
        print(f"\n  ── Full list of named Section objects ──")
        for n in all_names:
            tag = "   ← AC/Evo" if any(k in n for k in AC_KNOWN) else ""
            print(f"    {n}{tag}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trace shared memory handles in any process")
    parser.add_argument("--name",         default="SimHub",
                        help="Process name substring (default: SimHub)")
    parser.add_argument("--pid",          type=int, default=0,
                        help="Target process PID")
    parser.add_argument("--all-types",    action="store_true",
                        help="Show all handle types not just Section")
    parser.add_argument("--dump-regions", action="store_true",
                        help="Hex dump the first bytes of each AC SHM region")
    parser.add_argument("--watch",        type=float, default=0,
                        help="Rescan every N seconds (0 = run once)")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("ERROR: Windows only.")
        sys.exit(1)

    if not is_admin():
        print("⚠️  Not running as Administrator.")
        print("   DuplicateHandle across processes requires admin rights.")
        print("   Right-click → Run as Administrator if this fails.\n")
    else:
        ok = enable_debug_privilege()
        print(f"  SeDebugPrivilege: {'enabled ✅' if ok else 'FAILED to enable ⚠️'}\n")

    type_filter = None if args.all_types else "Section"

    def run_once():
        if args.pid:
            pid = args.pid
            exe = get_process_exe(pid)
        else:
            matches = find_pids_by_name(args.name)
            if not matches:
                print(f"No process matching '{args.name}' found.")
                print("Running processes:")
                all_p = find_pids_by_name("")
                for p, e in sorted(all_p, key=lambda x: os.path.basename(x[1]).lower()):
                    print(f"  {p:>6}  {os.path.basename(e)}")
                return
            if len(matches) > 1:
                print(f"Multiple matches for '{args.name}':")
                for p, e in matches:
                    print(f"  {p:>6}  {os.path.basename(e)}")
            pid, exe = matches[0]
            print(f"Target: {os.path.basename(exe)}  PID {pid}")

        try:
            handles = enumerate_process_handles(pid, type_filter=type_filter)
        except PermissionError as e:
            print(f"\nERROR: {e}")
            return

        print_report(pid, exe, handles, args.dump_regions)

    if args.watch > 0:
        try:
            while True:
                os.system("cls")
                run_once()
                print(f"\n  [watching — next scan in {args.watch}s, Ctrl+C to stop]")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        run_once()


if __name__ == "__main__":
    main()
