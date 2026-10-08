"""窗外：找图、存图、看图、把图画在终端里。

找图先问 Bing 图片（中文搜得准），找不到再问维基共享资源（Wikimedia Commons）。都不要 key。
Bing 给原图地址，原图拿不到就自动换 Bing 的大号缩略图。
存下来的图会缩到最长边 1280，只收真的图片，最大 8 MB。
给模型看的时候再缩到最长边 448，省上下文。
给人看的时候，用「▀」上下两个像素拼成一个字符，画成彩色小图。
"""
import base64
import html
import io
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

UA = {"User-Agent": "hachimi-cat/0.1 (a toy agent that looks out of the window)"}
MAX_BYTES = 8 * 1024 * 1024


def _get(url, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read(MAX_BYTES + 1), r.headers.get("Content-Type", "")


def _commons(q, n):
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search"
           f"&gsrnamespace=6&gsrlimit={n}&prop=imageinfo&iiprop=url|mime&iiurlwidth=1280"
           "&gsrsearch=" + urllib.parse.quote(q + " filetype:bitmap"))
    d = json.loads(_get(url)[0])
    pages = sorted(d.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = p["imageinfo"][0]
        title = re.sub(r"^File:|\.\w+$", "", p["title"])
        out.append((title, ii.get("thumburl") or ii["url"]))
    return out


BACKUP = {}  # 原图地址 -> Bing 的大号缩略图。原图下载不下来时用它


def _bing(q, n):
    page = _get("https://www.bing.com/images/async?q=" + urllib.parse.quote(q) + "&first=0&count=30&mmasync=1")[0]
    page = page.decode("utf-8", "replace")
    out = []
    for m in re.findall(r'\sm="(\{.*?\})"', page):
        try:
            d = json.loads(html.unescape(m))
        except ValueError:
            continue
        if d.get("murl"):
            out.append((d.get("t") or q, d["murl"]))  # 给它原图地址
            if d.get("turl"):
                BACKUP[d["murl"]] = d["turl"] + "&w=1280&h=1280"
        if len(out) >= n:
            break
    return out


def image_search(q, n=5):
    """先 Bing（中文搜得准），没有再问维基共享资源。"""
    hits = []
    try:
        hits = _bing(q, n)
    except Exception:
        pass
    if not hits:
        hits = _commons(q, n)
    rows = [f"{i}. {html.unescape(t)}\n   {u}" for i, (t, u) in enumerate(hits[:n], 1)]
    return "\n".join(rows) or "窗外没有这种图。"


def fetch(url, dest: Path):
    """把一张图存到 dest。返回（一句结果，存下来的路径或 None）。原图拿不到就换 Bing 的大号缩略图。"""
    img = None
    for u in [url] + ([BACKUP[url]] if url in BACKUP else []):
        try:
            data, _ = _get(u)
            if len(data) > MAX_BYTES:
                continue
            img = Image.open(io.BytesIO(data))
            img.load()
            break
        except Exception:
            img = None
    if img is None:
        return "叼不回来（不是图，或者太大，或者窗户打不开）。", None
    img = img.convert("RGB")
    img.thumbnail((1280, 1280))
    if dest.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        dest = dest.with_suffix(".jpg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, quality=88)
    return f"存好了：{dest.name}（{img.width}×{img.height}）", dest


def data_url(path: Path, side=448):
    img = Image.open(path).convert("RGB")
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


SIXEL_RUN = re.compile(rb"(.)\1{3,}")


def image_mode():
    """终端能画真图（Sixel）就用 Sixel，不能就用彩色方块。HACHIMI_IMAGES=sixel/blocks 可以强制。
    Mac 自带的「终端」不支持 Sixel，会用方块；iTerm2、WezTerm 支持。"""
    import os
    forced = os.environ.get("HACHIMI_IMAGES")
    if forced in ("sixel", "blocks"):
        return forced
    term, prog = os.environ.get("TERM", ""), os.environ.get("TERM_PROGRAM", "")
    if ("WT_SESSION" in os.environ or prog in ("iTerm.app", "WezTerm")
            or any(t in term for t in ("foot", "mlterm", "contour", "wezterm"))):
        return "sixel"
    return "blocks"


def cell_size():
    """一个字符格有几个像素（宽, 高）。Sixel 要按像素算图多大。
    HACHIMI_CELL=10x20 可以手动指定；Mac/Linux 会问终端；问不到就用偏小的 9x18，宁可图小也别画出框。"""
    import os
    import sys
    import time
    env = os.environ.get("HACHIMI_CELL", "")
    if "x" in env:
        w, h = env.lower().split("x")
        return int(w), int(h)
    if os.name != "nt":
        try:
            import select
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                sys.stdout.write("\x1b[16t")
                sys.stdout.flush()
                buf, t0 = "", time.time()
                while time.time() - t0 < 0.3 and select.select([fd], [], [], 0.1)[0]:
                    buf += os.read(fd, 64).decode(errors="ignore")
                    if buf.endswith("t"):
                        break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            m = re.search(r"\x1b\[6;(\d+);(\d+)t", buf)
            if m:
                return int(m.group(2)), int(m.group(1))
        except Exception:
            pass
    return 9, 18


def sixel(path: Path, max_w, max_h, colors=160):
    """编成 Sixel：支持它的终端（Windows Terminal、iTerm2、WezTerm、foot）能画出真像素的图。"""
    import numpy as np
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_w, max_h))
    q = img.quantize(colors=colors, dither=Image.Dither.FLOYDSTEINBERG)
    pal = q.getpalette()[:colors * 3]
    n = len(pal) // 3
    a = np.asarray(q, dtype=np.uint8)
    h, w = a.shape
    out = [f'\x1bP0;1;0q"1;1;{w};{h}']
    out += [f"#{i};2;{pal[3*i]*100//255};{pal[3*i+1]*100//255};{pal[3*i+2]*100//255}" for i in range(n)]
    weights = (1 << np.arange(6, dtype=np.uint8))[:, None]
    for y in range(0, h, 6):
        band = a[y:y + 6]
        if band.shape[0] < 6:  # 最后一条不够 6 行，用不存在的颜色补齐
            band = np.vstack([band, np.full((6 - band.shape[0], w), 255, np.uint8)])
        for c in np.unique(band):
            if c >= n:
                continue
            row = ((band == c) * weights).sum(axis=0).astype(np.uint8) + 63
            row = SIXEL_RUN.sub(lambda m: b"!%d%c" % (len(m.group(0)), m.group(1)[0]),
                                row.tobytes().rstrip(b"?"))
            out.append(f"#{c}" + row.decode("ascii") + "$")
        out.append("-")
    out.append("\x1b\\")
    return "".join(out), w, h


def halfblock(path: Path, cols=56, rows=18):
    """画成终端里的小图：每个字符是上下两个像素。返回 prompt_toolkit 的格式化片段。"""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = min(cols / w, rows * 2 / h)
    img = img.resize((max(1, int(w * scale)), max(2, int(h * scale) // 2 * 2)))
    px = img.load()
    frags = []
    for y in range(0, img.height, 2):
        for x in range(img.width):
            top, bot = px[x, y], px[x, y + 1]
            frags.append((f"fg:#{top[0]:02x}{top[1]:02x}{top[2]:02x} bg:#{bot[0]:02x}{bot[1]:02x}{bot[2]:02x}", "▀"))
        frags.append(("", "\n"))
    return frags
