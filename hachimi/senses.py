"""猫的感官：两脚兽在不在电脑前、坐了多久；还有跑到两脚兽面前（把窗口叫到最前面）。

只看键盘鼠标多久没动过，不看屏幕、不看按了什么。
  Windows：GetLastInputInfo
  Mac：ioreg 里的 HIDIdleTime
  Linux：有 xprintidle 就用，没有就当不知道
"""
import os
import re
import shutil
import subprocess
import sys


def idle_seconds():
    """键盘鼠标多久没动过（秒）。不知道就返回 None。"""
    try:
        if os.name == "nt":
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            lii = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                return None
            now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
            return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000
        if sys.platform == "darwin":
            r = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=3)
            m = re.search(r'"HIDIdleTime" = (\d+)', r.stdout)
            return int(m.group(1)) / 1e9 if m else None
        if shutil.which("xprintidle"):
            r = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=3)
            return int(r.stdout.strip()) / 1000
    except Exception:
        pass
    return None


def _win_front():
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    k.GetConsoleWindow.restype = wintypes.HWND
    u.GetAncestor.restype = wintypes.HWND
    u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    for f in ("IsIconic", "ShowWindow", "SetForegroundWindow", "BringWindowToTop"):
        getattr(u, f).argtypes = [wintypes.HWND] + ([ctypes.c_int] if f == "ShowWindow" else [])
    hwnd = k.GetConsoleWindow()
    if not hwnd:
        return False
    # Windows Terminal 里 GetConsoleWindow 是个看不见的假窗口，它的主人才是真窗口
    hwnd = u.GetAncestor(hwnd, 3) or hwnd  # GA_ROOTOWNER
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = u.GetForegroundWindow()
    if fg == hwnd:
        return True
    me, them = k.GetCurrentThreadId(), u.GetWindowThreadProcessId(fg, None)
    u.AttachThreadInput(them, me, True)  # 不借一下前台的手，Windows 不让后台窗口跳到前面
    ok = bool(u.SetForegroundWindow(hwnd))
    u.BringWindowToTop(hwnd)
    u.AttachThreadInput(them, me, False)

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", wintypes.HWND), ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]

    # 跳不到前面也要在任务栏上闪，闪到人看过来为止
    u.FlashWindowEx(ctypes.byref(FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3 | 12, 3, 0)))
    return ok


def come_to_front():
    """跑到两脚兽面前：把这个终端窗口叫到最前面。做不到也没关系，返回 False。"""
    try:
        if os.name == "nt":
            return _win_front()
        if sys.platform == "darwin":
            app = {"iTerm.app": "iTerm", "WezTerm": "WezTerm", "Apple_Terminal": "Terminal"}.get(
                os.environ.get("TERM_PROGRAM", ""))
            if app:
                subprocess.run(["osascript", "-e", f'tell application "{app}" to activate'], timeout=3)
                return True
        elif shutil.which("wmctrl") and os.environ.get("WINDOWID"):
            subprocess.run(["wmctrl", "-i", "-a", os.environ["WINDOWID"]], timeout=3)
            return True
    except Exception:
        pass
    return False
