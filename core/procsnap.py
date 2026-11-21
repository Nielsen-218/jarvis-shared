"""Every process's id, name and working-set size from ONE system call.

psutil opens each process in turn for its memory figure: about 11 seconds for the owner's 850 processes on a quiet PC and
over 20 when it is busy, which is what `performance_check` used to cost him every time he asked. NtQuerySystemInformation
(SystemProcessInformation) returns the whole table at once in about a tenth of a second, with the same numbers.
"""
from __future__ import annotations

import ctypes
import os
from typing import List, Tuple

Row = Tuple[int, str, int]          # pid, image name, working set in bytes
Timed = Tuple[int, str, int, int]   # pid, image name, working set in bytes, processor time used so far (100 ns units)


def _windows() -> List[Row]:
    return [(pid, name, mem) for pid, name, mem, _ in _windows_timed()]


def _windows_timed() -> List[Timed]:
    from ctypes import wintypes as w

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", ctypes.c_void_p)]

    class _Entry(ctypes.Structure):          # SYSTEM_PROCESS_INFORMATION (64-bit layout), up to WorkingSetSize
        _fields_ = [("NextEntryOffset", w.ULONG), ("NumberOfThreads", w.ULONG), ("Reserved1", ctypes.c_byte * 48),
                    ("ImageName", _UnicodeString), ("BasePriority", w.LONG), ("UniqueProcessId", ctypes.c_void_p),
                    ("InheritedFromUniqueProcessId", ctypes.c_void_p), ("HandleCount", w.ULONG), ("SessionId", w.ULONG),
                    ("UniqueProcessKey", ctypes.c_void_p), ("PeakVirtualSize", ctypes.c_size_t),
                    ("VirtualSize", ctypes.c_size_t), ("PageFaultCount", w.ULONG),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t)]

    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError("procsnap: 64-bit Python only")
    ntdll = ctypes.WinDLL("ntdll")
    size = w.ULONG(1 << 20)
    for _ in range(8):
        buf = ctypes.create_string_buffer(size.value)
        status = ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(size)) & 0xFFFFFFFF
        if status == 0:
            break
        if status != 0xC0000004:             # anything but "buffer too small"
            raise OSError(f"NtQuerySystemInformation failed: {status:#x}")
        size = w.ULONG(size.value + (1 << 16))
    else:
        raise OSError("NtQuerySystemInformation: the table kept growing")
    rows, offset, limit = [], 0, len(buf) - ctypes.sizeof(_Entry)
    while 0 <= offset <= limit:
        entry = _Entry.from_buffer(buf, offset)
        name = ctypes.wstring_at(entry.ImageName.Buffer, entry.ImageName.Length // 2) if entry.ImageName.Buffer else ""
        # Reserved1 holds, from the entry's byte 40: UserTime then KernelTime, each a 64-bit count of 100 ns.
        user = ctypes.c_longlong.from_buffer(buf, offset + 40).value
        kernel = ctypes.c_longlong.from_buffer(buf, offset + 48).value
        rows.append((int(entry.UniqueProcessId or 0), name, int(entry.WorkingSetSize), int(user + kernel)))
        if not entry.NextEntryOffset:
            break
        offset += entry.NextEntryOffset
    return rows


def _portable() -> List[Row]:
    import psutil
    rows = []
    for p in psutil.process_iter(["name", "memory_info"]):
        mem = p.info["memory_info"]
        rows.append((p.pid, p.info["name"] or "", int(mem.rss) if mem else 0))
    return rows


def processes() -> List[Row]:
    """(pid, name, working-set bytes) for every process. One call on Windows; psutil elsewhere or if that call fails."""
    if os.name == "nt":
        try:
            return _windows()
        except OSError:
            pass
    return _portable()


def timed() -> List[Timed]:
    """(pid, name, working-set bytes, processor time in 100 ns units) for every process: two of these a moment apart
    say who is using the processor now, without opening 850 processes one by one."""
    if os.name == "nt":
        try:
            return _windows_timed()
        except OSError:
            pass
    import psutil
    rows = []
    for p in psutil.process_iter(["name", "memory_info", "cpu_times"]):
        mem, cpu = p.info["memory_info"], p.info["cpu_times"]
        rows.append((p.pid, p.info["name"] or "", int(mem.rss) if mem else 0,
                     int(((cpu.user + cpu.system) if cpu else 0) * 1e7)))
    return rows
