# -*- coding: utf-8 -*-
"""Windows 窗口截图 / 裁剪 / 连拍 / 送输入。**截图相关的唯一实现**。

合并前散在四处（work/_grab_window.py、work/_grab_ck3.py、tools/burst_capture.py、
tools/crop_shot.py、tools/ck3_click.py），各自只修了一部分坑。这里把四个
硬教训一次性固化，别再各写一份：

1. **必须先声明 DPI 感知**。3840×2160 @200% 时 `GetWindowRect` 返回**逻辑**
   坐标（1933×1039），而 `ImageGrab` 抓**物理**像素 —— bbox 只覆盖左上四分之一。
   表现是「人物偏右下、被裁掉、侧栏不见了」，看着像取景 bug，其实是量错了。
2. **ImageGrab 抓的是屏幕，不是窗口**。目标没真正置前就静默抓到别的程序
   （抓到过两次别的游戏）。所以抓完要**回读前台窗口的 PID** 核对。
   见 memory [[screenshot-must-verify-window-identity]]。
3. **SetForegroundWindow 的返回值不可信** —— Windows 会拒绝非前台进程的
   抢焦点请求却报告成功。所以默认优先用 `PrintWindow`，它不要求置前。
4. **PW_RENDERFULLCONTENT(2)** 是抓 D3D/合成内容的必需标志；独占全屏仍抓不到，
   那时只能把游戏改成窗口化（判据：整图亮度最大值 < 12 即全黑）。

单帧看不出「动作正确」还是「卡在某个姿势」—— moe_greet_bow 的手臂锁死在
水平伸出，单帧正常，8 连拍才看出 f0→f7 几乎没变化。所以有 `burst()`。

    from win_capture import find_window, grab, crop, burst, contact_sheet
    hwnd, title = find_window(pid=9392)          # 或 title_contains="Crusader"
    img = grab(hwnd)                             # 自动选策略
    crop(img, 0.40, 0.15, 0.60, 0.75, zoom=4)    # 分数坐标，换分辨率不用重算
"""
import ctypes
import ctypes.wintypes as wt
import time

from PIL import Image

U = ctypes.windll.user32
G = ctypes.windll.gdi32

# 模块导入时就声明，晚于任何 GetWindowRect 就来不及了（教训 1）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE_V2
except Exception:                                    # noqa: BLE001
    U.SetProcessDPIAware()

_CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _title(h):
    n = U.GetWindowTextLengthW(h)
    if not n:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    U.GetWindowTextW(h, b, n + 1)
    return b.value


def _pid(h):
    p = ctypes.c_ulong()
    U.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def list_windows():
    """所有可见且有标题的顶层窗口 [(hwnd, pid, title)]。"""
    out = []

    def cb(h, _):
        if U.IsWindowVisible(h):
            t = _title(h)
            if t:
                out.append((h, _pid(h), t))
        return True

    U.EnumWindows(_CB(cb), 0)
    return out


def find_window(pid=None, title_contains=None):
    """按 PID 或标题子串找窗口，返回 (hwnd, title)。两个都给则都要满足。

    找不到就抛 —— **静默返回 None 会让调用方抓到别的程序**（教训 2）。
    """
    if pid is None and not title_contains:
        raise ValueError("至少给 pid 或 title_contains 之一")
    hits = [(h, t) for h, p, t in list_windows()
            if (pid is None or p == pid)
            and (not title_contains or title_contains in t)]
    if not hits:
        raise SystemExit(f"**找不到窗口**（pid={pid} title~{title_contains!r}）")
    return hits[0]


class _BMI(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab_printwindow(hwnd):
    """PrintWindow 抓客户区。**不要求窗口在前台**，被遮挡也能抓。

    返回 (img, is_black)。独占全屏时拿到全黑图 —— 这是引擎绕过桌面合成器，
    没有别的解法，只能把游戏改窗口化。
    """
    # **最小化的窗口客户区是 0×0**，PrintWindow 抓不出东西。先还原。
    # 这一步是实测撞出来的：CK3 被最小化时这里报「客户区尺寸异常 0x0」，
    # 看着像 API 用错，其实只是窗口不在屏上。
    if U.IsIconic(hwnd):
        U.ShowWindow(hwnd, 9)                # SW_RESTORE
        time.sleep(0.8)
    r = wt.RECT()
    U.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    if w <= 0 or h <= 0:
        raise RuntimeError(f"客户区仍是 {w}x{h}（窗口不在屏上？）")
    hdc = U.GetDC(hwnd)
    mdc = G.CreateCompatibleDC(hdc)
    bmp = G.CreateCompatibleBitmap(mdc, w, h)
    G.SelectObject(mdc, bmp)
    try:
        U.PrintWindow(hwnd, mdc, 2)          # 2 = PW_RENDERFULLCONTENT（教训 4）
        bi = _BMI(ctypes.sizeof(_BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        G.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer("RGBA", (w, h), buf,
                               "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        G.DeleteObject(bmp)
        G.DeleteDC(mdc)
        U.ReleaseDC(hwnd, hdc)
    return img, img.convert("L").getextrema()[1] < 12


def grab_foreground(hwnd, pid=None, settle=1.5):
    """置前 + ImageGrab。**抓完回读前台 PID 核对**，不一致就抛（教训 2、3）。"""
    from PIL import ImageGrab
    U.ShowWindow(hwnd, 9)                    # SW_RESTORE
    U.SetForegroundWindow(hwnd)
    time.sleep(settle)
    fg = U.GetForegroundWindow()
    want = pid if pid is not None else _pid(hwnd)
    if _pid(fg) != want:
        raise SystemExit(f"**置前失败**：前台是 pid {_pid(fg)} "
                         f"({_title(fg)!r})，不抓 —— 否则会拿到别的程序的图")
    r = wt.RECT()
    U.GetWindowRect(hwnd, ctypes.byref(r))
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom),
                         all_screens=True)
    want_w = r.right - r.left
    if img.width < want_w * 0.9:
        raise SystemExit(f"**抓到 {img.width} 远小于窗口 {want_w}** —— "
                         f"DPI 换算仍不对，别用这张图判断")
    return img


def grab(hwnd, pid=None, prefer="printwindow", max_width=2400):
    """抓一张图。默认先试 PrintWindow（不需置前），全黑再退回置前 ImageGrab。

    max_width：超过就等比缩一半，免得 4K 图大到没法看。传 0 关掉。
    """
    img = None
    if prefer == "printwindow":
        try:
            img, black = grab_printwindow(hwnd)
        except RuntimeError as e:
            print(f"  PrintWindow 不可用（{e}），改用置前抓")
            img = None
        else:
            if black:
                print("  PrintWindow 拿到全黑（多半是独占全屏），改用置前抓")
                img = None
    if img is None:
        img = grab_foreground(hwnd, pid=pid)
    if max_width and img.width > max_width:
        img = img.resize((img.width // 2, img.height // 2))
    return img


def crop(img, x0, y0, x1, y1, zoom=1):
    """按**分数坐标**（0~1，相对整图）裁剪并放大。

    用分数而不是像素：换分辨率不用重算。放大等价于「凑近看」—— Read 工具会把
    大图缩到约 512 宽，书签界面里的角色只占几十像素，不先裁就是一团糊。
    """
    w, h = img.size
    box = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
    out = img.crop(box)
    if zoom != 1:
        out = out.resize((out.width * zoom, out.height * zoom),
                         Image.LANCZOS)
    return out


def burst(hwnd, n=8, interval=0.25, pid=None, max_width=2400):
    """连拍 n 张。单帧分不出「动作正确」和「卡住」，必须看序列。"""
    out = []
    for i in range(n):
        out.append(grab(hwnd, pid=pid, max_width=max_width))
        if i < n - 1:
            time.sleep(interval)
    return out


def contact_sheet(imgs, cols=4, label=True):
    """把多张图拼成一张 contact sheet，按 cols 换行。"""
    if not imgs:
        raise ValueError("没有图可拼")
    w, h = imgs[0].size
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (w * min(cols, len(imgs)), h * rows), (20, 20, 24))
    for i, im in enumerate(imgs):
        if im.size != (w, h):
            im = im.resize((w, h))
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    if label:
        try:
            from PIL import ImageDraw
            d = ImageDraw.Draw(sheet)
            for i in range(len(imgs)):
                d.text(((i % cols) * w + 6, (i // cols) * h + 6),
                       f"f{i}", fill=(255, 240, 120))
        except Exception:                              # noqa: BLE001
            pass
    return sheet


def frame_deltas(imgs):
    """相邻帧平均绝对差。**全接近 0 = 动作卡住**，这是连拍的判据。"""
    import statistics
    out = []
    for a, b in zip(imgs, imgs[1:]):
        pa, pb = a.convert("L").getdata(), b.convert("L").getdata()
        out.append(statistics.fmean(abs(x - y) for x, y in zip(pa, pb)))
    return out


# ------------------------------------------------------------------ 送输入
#
# 用来无人值守走到书签界面：验收肖像最快的入口是**书签选择界面**（直接渲染
# 3D 肖像，不用进地图），但从主菜单要点两下，纯截图工具够不着。
# 坐标同样用分数（0~1 相对客户区），换分辨率不用重算。

_MOUSE_DOWN, _MOUSE_UP, _WHEEL, _WHEEL_DELTA = 0x0002, 0x0004, 0x0800, 120
_RMOUSE_DOWN, _RMOUSE_UP = 0x0008, 0x0010
_KEYUP = 0x0002
VK = {"esc": 0x1B, "enter": 0x0D, "space": 0x20, "tab": 0x09,
      "f1": 0x70, "grave": 0xC0}


def client_origin(hwnd):
    """客户区左上角的屏幕坐标 + 客户区尺寸 (ox, oy, w, h)。"""
    r = wt.RECT()
    U.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    U.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right, r.bottom


def click(hwnd, x, y, settle=0.2):
    """点击客户区。x/y 在 (0,1) 内按分数解释，否则按像素。"""
    ox, oy, w, h = client_origin(hwnd)
    px = x * w if 0.0 < x < 1.0 else x
    py = y * h if 0.0 < y < 1.0 else y
    U.SetForegroundWindow(hwnd)
    time.sleep(settle)
    U.SetCursorPos(int(ox + px), int(oy + py))
    time.sleep(0.05)
    U.mouse_event(_MOUSE_DOWN, 0, 0, 0, 0)
    U.mouse_event(_MOUSE_UP, 0, 0, 0, 0)


def rclick(hwnd, x, y, settle=0.2):
    """右键点击客户区。坐标规则同 `click`。

    CK3 的角色交互菜单是右键出的，左键只会打开角色面板。
    """
    ox, oy, w, h = client_origin(hwnd)
    px = x * w if 0.0 < x < 1.0 else x
    py = y * h if 0.0 < y < 1.0 else y
    U.SetForegroundWindow(hwnd)
    time.sleep(settle)
    U.SetCursorPos(int(ox + px), int(oy + py))
    time.sleep(0.05)
    U.mouse_event(_RMOUSE_DOWN, 0, 0, 0, 0)
    U.mouse_event(_RMOUSE_UP, 0, 0, 0, 0)


def key(hwnd, name, settle=0.2):
    """按一下键。name 取 VK 里的名字。"""
    vk = VK.get(name.lower())
    if vk is None:
        raise ValueError(f"不认识的键 {name!r}，可用: {sorted(VK)}")
    U.SetForegroundWindow(hwnd)
    time.sleep(settle)
    U.keybd_event(vk, 0, 0, 0)
    U.keybd_event(vk, 0, _KEYUP, 0)


def wheel(hwnd, notches, settle=0.2):
    """滚轮。正数向上（放大）。"""
    U.SetForegroundWindow(hwnd)
    time.sleep(settle)
    U.mouse_event(_WHEEL, 0, 0, int(notches * _WHEEL_DELTA), 0)


# ------------------------------------------------------------------ CLI
def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pid", type=int)
    ap.add_argument("--title", default="Crusader Kings",
                    help="窗口标题子串（默认找 CK3）")
    ap.add_argument("-o", "--out", default="work/shot/grab.png")
    ap.add_argument("--burst", type=int, default=0, help="连拍张数，拼成一张")
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--crop", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                    help="分数坐标裁剪 0~1")
    ap.add_argument("--zoom", type=int, default=1)
    ap.add_argument("--full", action="store_true",
                    help="不缩放，保留原始分辨率（4K 屏查细节时必须给 —— "
                         "默认超过 2400 宽会缩一半，眼睛/接缝这类细节就没了）")
    ap.add_argument("--click", nargs=2, type=float, metavar=("X", "Y"))
    ap.add_argument("--rclick", nargs=2, type=float, metavar=("X", "Y"))
    ap.add_argument("--key")
    ap.add_argument("--list", action="store_true", help="列出可见窗口后退出")
    a = ap.parse_args()

    if a.list:
        for h, p, t in list_windows():
            print(f"  hwnd={h:<12} pid={p:<8} {t}")
        return

    hwnd, title = find_window(pid=a.pid, title_contains=a.title)
    print(f"目标: hwnd={hwnd} pid={_pid(hwnd)} title={title!r}")

    if a.click:
        click(hwnd, a.click[0], a.click[1])
        print(f"  已点击 {a.click}")
    if a.rclick:
        rclick(hwnd, a.rclick[0], a.rclick[1])
        print(f"  已右键点击 {a.rclick}")
    if a.key:
        key(hwnd, a.key)
        print(f"  已按键 {a.key}")
    if a.click or a.rclick or a.key:
        time.sleep(0.8)

    import pathlib
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if a.burst:
        imgs = burst(hwnd, n=a.burst, interval=a.interval, pid=a.pid,
                     max_width=0 if a.full else 2400)
        if a.crop:
            imgs = [crop(i, *a.crop, zoom=a.zoom) for i in imgs]
        d = frame_deltas(imgs)
        contact_sheet(imgs).save(out)
        print(f"  连拍 {len(imgs)} 张 → {out}")
        print(f"  相邻帧差 {[round(x, 2) for x in d]}")
        if d and max(d) < 0.5:
            print("  **全部接近 0 —— 画面没动，动作可能卡住**")
    else:
        img = grab(hwnd, pid=a.pid, max_width=0 if a.full else 2400)
        if a.crop:
            img = crop(img, *a.crop, zoom=a.zoom)
        img.save(out)
        print(f"  已存 {out}  {img.size}")


if __name__ == "__main__":
    main()
