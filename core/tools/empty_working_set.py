# coding: utf-8
"""
Windows working-set utilities.

Trim a process working set to release resident physical pages.
Supported only on Windows.
"""

import ctypes
from typing import Optional
from platform import system

def empty_working_set(pid: int) -> None:
    """
    Trim the specified process's working set.
    
    Ask Windows to remove resident pages;
    this does not release virtual allocations or guarantee a particular paging outcome.
    
    Args:
        pid: Process identifier.
        
    Note:
        Calls on other platforms fail.
    """
    # Open the process with PROCESS_ALL_ACCESS (0x1F0FFF).
    handle = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, pid)
    
    if handle:
        # Trim the working set.
        ctypes.windll.psapi.EmptyWorkingSet(handle)
        
        # Close the process handle.
        ctypes.windll.kernel32.CloseHandle(handle)


def empty_current_working_set() -> None:
    """
    Trim the current process's working set.
    
    Get the current process ID and trim its resident pages.
    Typically called after initialization to reduce the resident startup footprint.
    """
    if system() == 'Windows':
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        empty_working_set(pid)