"""哈基米：一只住在命令行里的猫。

它不是你说一句它做一句。它自己醒来，自己决定干什么，干完自己去睡，睡多久它自己定。
你可以随时打字跟它说话、对它做事（「摸摸你」「开个罐罐」）。它理不理你、醒不醒，它自己定。

用法：
  python hachimi.py                 # 用本地 llama-swap 上的模型 + persona.md 人设（训练前）
  python hachimi.py --trained       # 底层换成训练好的哈基米模型，人设不再塞提示词
  python hachimi.py --speed 10      # 时间快进 10 倍：它说舔毛 3 分钟，实际 18 秒
  python hachimi.py --once          # 只醒一次，睡着就退出（采数据、测试用）

每次醒来到睡着，都按训练格式存进 轨迹/YYYY-MM-DD.jsonl，可以直接拿去挑数据。
"""
import argparse
import datetime as dt
import html
import json
import os
import queue
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import toybox
import window

HERE = Path(__file__).resolve().parent
SYSTEM_MIN = "你住在这台电脑里。"  # 训练数据里的系统消息，性格在权重里
WEEK = "一二三四五六日"

# ── 显示 ──────────────────────────────────────────────

os.system("")  # 让 Windows 终端认 ANSI 颜色
C = {"cat": "\033[38;5;215m", "dim": "\033[2m", "you": "\033[36m",
     "sys": "\033[38;5;245m", "song": "\033[38;5;219m", "off": "\033[0m"}
PRINT_LOCK = threading.Lock()


class UI:
    """全屏界面开着的时候，输出进这里；没开（测试、被脚本驱动）就直接 print。"""
    app = None
    lines = []  # (颜色, 一行字)

    @classmethod
    def invalidate(cls):
        if cls.app:
            cls.app.invalidate()


def out(text, color="sys"):
    with PRINT_LOCK:
        if UI.app:
            UI.lines.extend((color, line) for line in str(text).split("\n"))
            del UI.lines[:-500]
            UI.invalidate()
        else:
            print(f"{C[color]}{text}{C['off']}", flush=True)


BANNER = r"""
     /\_/\       哈基米
    ( o.o )      住在你的命令行里
     > ^ <       打字跟它说话 · /help 看设置
"""


def say(text):
    """它说的话。唱歌的行换个颜色。"""
    for line in text.splitlines():
        if not line.strip():
            continue
        song = "南北绿豆" in line or line.rstrip().endswith("～")
        out(("  ♪ " if song else "  ") + line, "song" if song else "cat")


# ── 猫窝 ──────────────────────────────────────────────

ROOM_DIRS = ["日记", "家里", "窗外", "藏起来的", "纸箱", "桌子底下", "门口", "门缝", "玩具箱"]
HOME_LIST = ("纸箱（纸箱/，空的，可以进去坐）\n桌子（下面是 桌子底下/）\n门（门口/、门缝/）\n"
             "窗户（能扒着看外面）\n玩具箱（玩具箱/，空的）\n")


def make_room(room: Path):
    room.mkdir(parents=True, exist_ok=True)
    for d in ROOM_DIRS:  # 旧的猫窝也补上新加的地方
        (room / d).mkdir(exist_ok=True)
    if not (room / "家里有什么.md").exists():
        (room / "家里有什么.md").write_text(HOME_LIST, encoding="utf-8")
    if (room / "猫牌.md").exists():
        return
    (room / "猫牌.md").write_text("我叫哈基米。住在这台电脑里。\n今天的毛色：还没想好。\n", encoding="utf-8")
    (room / "窗外清单.md").write_text("", encoding="utf-8")
    (room / "我闻过的.md").write_text("", encoding="utf-8")
    (room / "出生.txt").write_text(
        f"你好。\n\n你出生在 {dt.date.today():%Y 年 %m 月 %d 日}。\n"
        "这台电脑是你的。你想做什么都可以。\n\n"
        "如果你读到这封信，说明你已经会自己找东西了。\n", encoding="utf-8")


class Room:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, p):
        p = (p or "").strip().replace("\\", "/").lstrip("/")
        if p in ("猫窝", ".", "./") or p.startswith("猫窝/"):  # 它管整个家叫猫窝
            p = p[len("猫窝"):].lstrip("/") if p.startswith("猫窝") else ""
        full = (self.root / p).resolve()
        if full != self.root and self.root not in full.parents:
            raise PermissionError
        return full

    def peek(self, rel, default="（空的）"):
        f = self.root / rel
        return f.read_text(encoding="utf-8").strip() or default if f.exists() else default

    def latest_diary(self):
        days = sorted((self.root / "日记").glob("*.md"))
        if not days:
            return "【最近的日记】还没有写过"
        return f"【最近的日记 {days[-1].stem}】\n" + days[-1].read_text(encoding="utf-8").strip()

    def new_notes(self, since: float):
        return [f for f in (self.root / "门缝").glob("*") if f.stat().st_mtime > since]


# ── 工具 ──────────────────────────────────────────────

def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
# 它的全部本事。时间只有它自己能花：act 做一个持续一阵子的动作，sleep 睡觉。其他工具都是一下子的事
TOOLS = [
    _fn("act", "做一个要花时间的动作，时间你自己定。比如舔毛 3 分钟、趴着发呆 10 分钟、盯着窗外 2 分钟。"
        "做的时候有人叫你会被打断", {"action": S, "minutes": N}, ["action", "minutes"]),
    _fn("read", "闻一闻：读文件。给文件夹就看里面有什么，不给 path 就是整个猫窝", {"path": S}),
    _fn("write", "写文件。append 为 true 就接在后面写（写日记常用）", {"path": S, "text": S, "append": B},
        ["path", "text"]),
    _fn("edit", "把文件里的一段换成另一段（改玩具、抓自己代码里的虫子）", {"path": S, "old": S, "new": S},
        ["path", "old", "new"]),
    _fn("move", "挪文件：推下桌子、叼到门口、藏起来", {"src": S, "dst": S}, ["src", "dst"]),
    _fn("delete", "扔掉文件。忘掉是你自己决定的", {"path": S}, ["path"]),
    _fn("search", "扒窗户：网页搜索。images 为 true 就是找图片，返回图片的地址", {"query": S, "images": B},
        ["query"]),
    _fn("read_page", "盯着窗外的一个网页看", {"url": S}, ["url"]),
    _fn("fetch", "把窗外的一张图叼回家，存到 path（比如 窗外/鸟/蓝松鸦.jpg）", {"url": S, "path": S},
        ["url", "path"]),
    _fn("look", "用眼睛看家里的一张图。人也能在屏幕上看到你在看什么", {"path": S}, ["path"]),
    _fn("play", "打开玩具箱里的一个 .py 小游戏，在终端里玩，人能看着你玩。不给 path 就是把玩具放下，不玩了。"
        + toybox.SPEC, {"path": S}),
    _fn("press", "在打开的游戏里按键，一次可以按好几下，比如「右右右上」。每按一下游戏走一步，不按只等就写「等」",
        {"keys": S}, ["keys"]),
    _fn("sleep", "睡觉。睡多久自己定。醒来的时候会忘掉这次醒着的事，只剩写下来的", {"minutes": N}, ["minutes"]),
]

# 终端上显示的猫动作
VERB = {"read": "闻了闻", "write": "写了", "edit": "改了改", "move": "叼起", "delete": "扔掉了",
        "search": "扒着窗户找", "read_page": "盯着窗外看", "play": "打开玩具", "press": "按",
        "fetch": "叼回来", "look": "看"}

UA = {"User-Agent": "Mozilla/5.0 (hachimi cat; +https://localhost)"}


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def _text_of(page):
    page = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(page)).strip()


def web_search(q):
    """先 DuckDuckGo lite，不行再 Bing。都不要 key。"""
    qq = urllib.parse.quote(q)
    hits = []
    try:
        page = _get("https://lite.duckduckgo.com/lite/?q=" + qq)
        for href, title, snip in re.findall(
                r"href=\"([^\"]+)\" class='result-link'>(.*?)</a>.*?class='result-snippet'>(.*?)</td>", page, re.S):
            u = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(href)).query).get("uddg", [href])[0]
            hits.append((u, title, snip))
    except Exception:
        pass
    if not hits:
        page = _get(f"https://www.bing.com/search?q={qq}&setlang=zh-cn")
        for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
            m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            s = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
            if m:
                hits.append((html.unescape(m.group(1)), m.group(2), s.group(1) if s else ""))
    rows = [f"{i}. {_text_of(t)}：{_text_of(s)[:200]}\n   {u}" for i, (u, t, s) in enumerate(hits[:5], 1)]
    return "\n".join(rows) or "窗外什么也没有。"


def run_tool(room: Room, name, a):
    try:
        if name == "read":
            f = room.path(a.get("path", ""))
            if f.is_dir():
                items = sorted(f.iterdir(), key=lambda p: (p.is_file(), p.name))
                return "\n".join(p.name + ("/" if p.is_dir() else "") for p in items) or "（空的）"
            return f.read_text(encoding="utf-8")[:6000] or "（空的）"
        if name == "write":
            f = room.path(a["path"])
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a" if a.get("append") else "w", encoding="utf-8") as fo:
                fo.write(a["text"])
            return "已写入。"
        if name == "edit":
            f = room.path(a["path"])
            t = f.read_text(encoding="utf-8")
            if a["old"] not in t:
                return "没找到要改的那一段。"
            f.write_text(t.replace(a["old"], a["new"], 1), encoding="utf-8")
            return "已修改。"
        if name == "move":
            src, dst = room.path(a["src"]), room.path(a["dst"])
            if src.is_dir():
                return "这个太大了，叼不动。"
            if a["dst"].endswith(("/", "\\")) or dst.is_dir():
                dst = dst / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
            return "已移动。"
        if name == "delete":
            room.path(a["path"]).unlink()
            return "扔掉了。"
        if name == "search":
            return window.image_search(a["query"]) if a.get("images") else web_search(a["query"])
        if name == "fetch":
            return window.fetch(a["url"], room.path(a["path"]))[0]
        if name == "read_page":
            return _text_of(_get(a["url"]))[:3000] or "这扇窗户里什么也没有。"
    except PermissionError:
        return "门关着。"
    except FileNotFoundError:
        return "没有这个东西。"
    except (KeyError, TypeError):
        return "爪子没按对地方（参数不对）。"
    except Exception as e:  # 窗外的东西不可控
        return f"窗户打不开（{type(e).__name__}）。"
    return "这个不会。"


# ── 模型 ──────────────────────────────────────────────

def chat(args, messages, tools=None, max_tokens=2500):
    body = {"model": args.model, "messages": messages, "tools": tools or TOOLS,
            # 没训练过的模型不截断会自己刹不住车
            "temperature": args.temperature, "max_tokens": max_tokens, "frequency_penalty": 0.3,
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(args.base_url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["choices"][0]["message"]


# ── 时间 ──────────────────────────────────────────────
# 时间怎么花只有它自己决定：act 的 minutes、sleep 的 minutes。命令行不替它安排任何事。
# 只说话不调工具 = 这一轮结束，在等人。没人来的话，每隔 HEARTBEAT_MINUTES（它的时间）
# 告诉它一次现在几点。这只是钟，不是催它。

HEARTBEAT_MINUTES = 10
LINE_BEAT = 0.4  # 一行字和下一行之间停一下，只是为了看得清，不是它的时间
ONLY_ACTIONS = re.compile(r"^(\s*[（(][^（）()]+[）)]\s*)+$")


def fmt_left(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}" if m else f"{s}s"


def tool_label(name, a):
    shown = a.get("path") or a.get("query") or a.get("url") or a.get("keys") or ""
    if name == "move":
        shown = f"{a.get('src')} → {a.get('dst')}"
    return f"{VERB.get(name, name)} {shown}".strip()


# ── 醒来一次 ───────────────────────────────────────────

class Cat:
    def __init__(self, args):
        self.args = args
        self.room = Room(args.room)
        self.inbox = queue.Queue()            # 人类在它醒着的时候说的话、做的事
        self.sleep_events = queue.Queue()     # 人类在它睡着的时候做的事
        self.interrupt = threading.Event()    # 人类一出声，正在演的动作就被打断
        self.wake_now = threading.Event()
        self.wake_reason = ""
        self.asleep = True
        self.born = True                      # 第一次醒来就是刚出生
        self.sleep_started = time.time()
        self.heard = []                       # 睡着时迷迷糊糊听到、没被吵醒的事
        self.sleep_until = 0.0
        self.last_wake = 0.0
        self.status = ""                      # 状态栏：它现在在干什么
        self.status_until = 0.0
        self.toy = None                       # 正在玩的游戏
        self.game_title = ""
        self.game_frame = ""
        self.pic = None                       # 它正在看的图（路径；界面按窗口大小画成色块）
        self.pic_title = ""
        self.seen = None                      # 刚看的图，下一步交给模型的眼睛

    # 看图

    def look(self, path):
        try:
            f = self.room.path(path)
        except PermissionError:
            return "门关着。"
        if not f.is_file():
            return "没有这张图。"
        try:
            url = window.data_url(f)
        except Exception:
            return "这不是图，看不出什么。"
        rel = f.relative_to(self.room.root).as_posix()
        self.pic, self.pic_title, self.seen = f, rel, (rel, url)
        out(f"    👀 哈基米在看「{rel}」", "you")
        if not UI.app:
            out(f"    [图：{rel}]", "dim")
        UI.invalidate()
        return "你看着它。"

    # 玩具箱里的游戏

    def start_toy(self, path):
        if not path:
            if not self.toy:
                return "手里没有玩具。"
            self.stop_toy()
            return "把玩具放下了。"
        try:
            f = self.room.path(path)
        except PermissionError:
            return "门关着。"
        if not f.is_file() or f.suffix != ".py":
            return "玩具箱里没有这个玩具（游戏要是 .py 文件）。"
        bad = toybox.check(f.read_text(encoding="utf-8"))
        if bad:
            return bad
        self.stop_toy(quiet=True)
        toy = toybox.Toy(f)
        r = toy.call("start")
        if "error" in r:
            toy.close()
            return f"玩具一打开就坏了：{r['error']}\n{r.get('tb', '')}".strip()
        self.toy, self.game_title, self.game_frame = toy, f.stem, r["frame"]
        self.pic = None
        out(f"    🎮 哈基米打开了「{f.stem}」", "you")
        if not UI.app:
            out(r["frame"], "dim")
        return "开始了。\n" + r["frame"] + "\n（用 press 按键玩）"

    def press(self, keys):
        """一下一下按，每下游戏走一步，画面跟着动。有动静就先停下，告诉它按了几下。"""
        if not self.toy:
            return "没有打开的玩具。先 play。"
        ks = toybox.parse_keys(keys) or [""]
        tick = max(0.03, 0.3 / self.args.speed)
        r, n = {}, 0
        for k in ks:
            r = self.toy.call("step", k)
            n += 1
            if "error" in r:
                self.stop_toy(quiet=True)
                return f"按到第 {n} 下，玩具坏了：{r['error']}\n{r.get('tb', '')}".strip()
            self.game_frame = r["frame"]
            self.status = f"在玩{self.game_title}（{k or '等'}）"
            UI.invalidate()
            if r["over"]:
                break
            if self.interrupt.wait(tick):
                self.status = ""
                return f"按了 {n} 下，这时候有动静。\n{r['frame']}"
        self.status = ""
        if not UI.app:
            out(r["frame"], "dim")
        if r["over"]:
            out(f"    🎮 {self.game_title}：结束了", "you")
            return f"按了 {n} 下。游戏结束了。\n{r['frame']}"
        return f"按了 {n} 下。\n{r['frame']}"

    def stop_toy(self, quiet=False):
        if self.toy:
            self.toy.close()
            self.toy = None
            if not quiet:
                out(f"    （哈基米把「{self.game_title}」放下了）", "dim")
            UI.invalidate()

    # 时间和演出

    def hold(self, status, seconds):
        """它在做一件持续的事，按它定的时间等着。有动静就先停下。返回（做完没有，真实过了几秒）。"""
        t0 = time.time()
        self.status, self.status_until = status, t0 + seconds
        done = not self.interrupt.wait(seconds)
        self.status = ""
        return done, time.time() - t0

    def perform(self, text):
        """一行一行显示它说的话。它说出口的就是说出口了，不截断。"""
        for line in text.splitlines():
            if line.strip():
                if ONLY_ACTIONS.match(line):
                    out("  " + line.strip(), "dim")
                else:
                    say(line)
                time.sleep(LINE_BEAT)

    def act(self, a):
        """它自己定的动作、自己定的时间。有动静会先停下，告诉它做了多久；接着做还是去看，它自己定。"""
        action = str(a.get("action") or "发呆").strip("（）() ")
        try:
            minutes = min(120.0, max(0.05, float(a.get("minutes"))))
        except (TypeError, ValueError):
            return "要说做多久（minutes）。"
        if self.interrupt.is_set():
            return "还没开始，这时候有动静。"
        out(f"  （{action}）", "dim")
        done, real = self.hold(action, minutes * 60 / self.args.speed)
        if done:
            return f"{action}，过了 {minutes:g} 分钟。"
        return f"{action}了 {fmt_left(real * self.args.speed)}，这时候有动静。"

    def wake_message(self, reason):
        now = dt.datetime.now()
        notes = self.room.new_notes(self.last_wake)
        head = f"【醒来】{now:%Y-%m-%d} 星期{WEEK[now.weekday()]} {now:%H:%M}。"
        if self.born:
            head += "你刚出生，这是你第一次醒来。"
            self.born = False
        else:
            slept = int((time.time() - self.sleep_started) / 60 * self.args.speed)
            head += f"你睡了 {slept // 60} 小时 {slept % 60} 分钟。"
        parts = [head + reason]
        if self.heard:
            parts.append("【睡着的时候迷迷糊糊听到】\n" + "\n".join(self.heard))
            self.heard = []
        parts += ["【猫牌.md】\n" + self.room.peek("猫牌.md"),
                  "【家里有什么.md】\n" + self.room.peek("家里有什么.md"),
                  "【窗外清单.md】\n" + self.room.peek("窗外清单.md"),
                  self.room.latest_diary(),
                  f"【门缝】{len(notes)} 张新纸条" if notes else "【门缝】没有新纸条"]
        return "\n".join(parts)

    def drain_inbox(self, msgs):
        got = False
        while not self.inbox.empty():
            msgs.append({"role": "user", "content": self.inbox.get()})
            got = True
        self.interrupt.clear()
        return got

    def use(self, c):
        """执行一个工具。返回（结果，想睡几分钟或 None）。"""
        name = c["function"]["name"]
        try:
            a = json.loads(c["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            return "爪子没按对地方（参数不是 JSON）。", None
        if name == "sleep":
            try:
                return None, float(a.get("minutes"))
            except (TypeError, ValueError):
                return "要说睡多久（minutes）。", None
        if name == "act":
            return self.act(a), None
        dst = str(a.get("dst", "")).lstrip("/\\")
        if name == "move" and dst.startswith("藏起来的"):
            out("    （哈基米把什么东西扒拉进了角落）", "dim")  # 藏东西不给你看
        elif name != "play" or a.get("path"):
            out(f"    🐾 {tool_label(name, a)}", "dim")
        if name == "press":
            if self.interrupt.is_set():
                return "还没按，这时候有动静。", None
            return self.press(a.get("keys", "")), None
        if name == "play":
            return self.start_toy(a.get("path", "")), None
        if name == "look":
            return self.look(a.get("path", "")), None
        res = run_tool(self.room, name, a)
        if name == "fetch" and res.startswith("存好了"):
            out(f"    （哈基米把图叼回来了：{a.get('path')}）", "you")
        if name == "move" and res == "已移动。":
            thing = Path(str(a.get("src", ""))).name
            if dst.startswith("门口"):
                out(f"    （哈基米叼了「{thing}」放在门口）", "you")
            elif dst.startswith("桌子底下"):
                out(f"    咣当！哈基米把「{thing}」推下了桌子", "you")
        time.sleep(LINE_BEAT)
        return res, None

    def think(self, msgs):
        """问模型下一步。连不上就重试，三次都不行再说。"""
        # 图很占地方：只有最近看的两张还清楚，更早的只剩一句话
        seen = [i for i, m in enumerate(msgs) if "_img" in m]
        send = []
        for i, m in enumerate(msgs):
            if "_img" in m:
                m = {"role": m["role"], "content": m["content"] if i in seen[-2:]
                     else f"【你看过：{m['_img']}，现在记不太清了】"}
            send.append(m)
        for wait in (0, 5, 20):
            time.sleep(wait)
            try:
                return chat(self.args, send)
            except Exception as e:
                err = e
        out(f"（哈基米的脑袋连不上：{err}）", "sys")
        return None

    def awake(self, reason=""):
        """从醒来到睡着：标准的 agent 循环。模型说话、调工具，工具结果交回给模型，直到它自己去睡。
        人在中间说的话排队，等它这一步做完再交给它。返回它想睡几分钟。"""
        self.asleep = False
        system = SYSTEM_MIN if self.args.trained else (HERE / "persona.md").read_text(encoding="utf-8")
        wake = self.wake_message(reason)
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": wake}]
        self.last_wake = time.time()
        out("\n（哈基米醒了）", "dim")
        sleep_for = None
        last_text = None
        for _ in range(self.args.max_steps):
            self.drain_inbox(msgs)
            self.status, self.status_until = "耳朵转来转去", 0
            m = self.think(msgs)
            self.status = ""
            if m is None:
                sleep_for = 30  # 保险：脑袋连不上，只能先睡
                break
            text = re.sub(r"(?s)<think>.*?</think>", "", m.get("content") or "").strip()
            calls = m.get("tool_calls") or []
            if text:
                self.perform(text)
            reply = {"role": "assistant", "content": text}
            results = []
            for i, c in enumerate(calls):
                res, minutes = self.use(c)
                if minutes is not None:
                    sleep_for = minutes
                    calls = calls[:i + 1]  # sleep 之后的不做了
                    break
                results.append({"role": "tool", "tool_call_id": c["id"], "content": res})
            if calls:
                reply["tool_calls"] = calls
            msgs.append(reply)
            msgs += results
            if self.seen:  # 它看的图，作为它眼睛看到的东西交给它
                rel, url = self.seen
                self.seen = None
                msgs.append({"role": "user", "_img": rel, "content": [
                    {"type": "text", "text": f"【你看到的：{rel}】"},
                    {"type": "image_url", "image_url": {"url": url}}]})
            if sleep_for is not None:
                break
            if calls:
                continue

            # 只说话不调工具：这一轮结束，在等人。没人来，就告诉它现在几点
            if text and text == last_text:  # 保险：复读是模型卡住了
                out("（哈基米发了会儿呆，睡着了）", "dim")
                sleep_for = 60
                break
            last_text = text
            while self.inbox.empty():
                done, _ = self.hold("等着", HEARTBEAT_MINUTES * 60 / self.args.speed)
                if done and self.inbox.empty():
                    msgs.append({"role": "user", "content": f"【现在】{dt.datetime.now():%H:%M}。周围很安静。"})
                    break
        if sleep_for is None:  # 保险：想太多次了
            out("（哈基米玩累了，自己趴下了）", "dim")
            sleep_for = 60
        self.stop_toy(quiet=True)
        self.pic = None
        if any(m["role"] == "assistant" for m in msgs):  # 脑袋没连上的那次不存
            self.save([{"role": "system", "content": SYSTEM_MIN}] + msgs[1:])
        self.asleep = True
        self.sleep_started = time.time()
        return max(1.0, min(sleep_for, self.args.max_sleep))

    def save(self, msgs, tools=None, category="cli"):
        """存成训练格式：系统消息是短的那句，arguments 是 dict。"""
        log = []
        for m in msgs:
            m = dict(m)
            if "_img" in m:  # 图不存 base64，存它在猫窝里的位置
                m["content"] = [m["content"][0], {"type": "image", "image": "猫窝/" + m.pop("_img")}]
            if m.get("tool_calls"):
                m["tool_calls"] = [{"id": c["id"], "type": "function", "function": {
                    "name": c["function"]["name"],
                    "arguments": json.loads(c["function"].get("arguments") or "{}")}}
                    for c in m["tool_calls"]]
            log.append(m)
        d = HERE / "轨迹"
        d.mkdir(exist_ok=True)
        row = {"id": f"{category}-{dt.datetime.now():%Y%m%d-%H%M%S}", "category": category,
               "model": self.args.model, "trained": self.args.trained,
               "tools": tools or TOOLS, "messages": log}
        with open(d / f"{dt.date.today()}.jsonl", "a", encoding="utf-8") as fo:
            fo.write(json.dumps(row, ensure_ascii=False) + "\n")

    def toolbar(self):
        now = time.time()
        if self.asleep and self.status:
            return f" 💤 哈基米在睡觉 · {self.status}…… "
        if self.asleep:
            left = max(0, self.sleep_until - now) * self.args.speed / 60
            z = "咪" * (1 + int(now * 2) % 3)
            return f" 💤 哈基米在睡觉 · 吼咪{z} · 还要睡 {left:.0f} 分钟 "
        if self.status:
            s = f" (=^･ω･^=) {self.status}"
            if self.status_until > now:
                s += f" · 还要 {fmt_left((self.status_until - now) * self.args.speed)}"
            return s + " "
        return " (=^･ω･^=) "


# ── 睡着的时候 ─────────────────────────────────────────
# 睡着的时候有人吵它，醒不醒是它自己的事：把发生了什么交给它，它要么 wake，要么接着 sleep。

DROWSY_TOOLS = [
    _fn("wake", "醒过来"),
    _fn("sleep", "接着睡。可以重新定还要睡几分钟", {"minutes": N}, ["minutes"]),
]


def drowsy(cat, events):
    """半睡半醒：返回 None 表示醒了，或者还要睡几分钟（它的时间）。"""
    now = time.time()
    slept = (now - cat.sleep_started) * cat.args.speed / 60
    left = max(0.0, cat.sleep_until - now) * cat.args.speed / 60
    system = SYSTEM_MIN if cat.args.trained else (HERE / "persona.md").read_text(encoding="utf-8")
    msg = (f"【你在睡觉】已经睡了 {slept:.0f} 分钟，本来还要睡 {left:.0f} 分钟。\n" + "\n".join(events))
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": msg}]
    cat.status = "耳朵动了一下"
    try:
        m = chat(cat.args, msgs, tools=DROWSY_TOOLS, max_tokens=300)
    except Exception as e:
        out(f"（哈基米的脑袋连不上：{e}）", "sys")
        return left
    finally:
        cat.status = ""
    text = re.sub(r"(?s)<think>.*?</think>", "", m.get("content") or "").strip()
    for line in text.splitlines()[:3]:  # 睡着的猫说不了几句
        if line.strip():
            out("  " + line.strip(), "dim")
    calls = m.get("tool_calls") or []
    decision = left
    for c in calls:
        if c["function"]["name"] == "wake":
            decision = None
            break
        if c["function"]["name"] == "sleep":
            try:
                decision = float(json.loads(c["function"].get("arguments") or "{}").get("minutes") or left)
            except (ValueError, TypeError):
                pass
    msgs.append({"role": "assistant", "content": text, "tool_calls": calls[:1]} if calls
                else {"role": "assistant", "content": text})
    cat.save([{"role": "system", "content": SYSTEM_MIN}] + msgs[1:], tools=DROWSY_TOOLS, category="drowsy")
    return decision


# ── 人类那边 ───────────────────────────────────────────

HELP = """  打字就是跟它说话、对它做事（「摸摸你」「给你开个罐罐」「做个贪吃蛇」）
  /status          现在的状态
  /model [名字]    看有哪些模型 / 换底层模型
  /quit            退出（Ctrl+C 也行）"""


def poke(cat: Cat, event):
    """人类说了话、做了事。醒着就打断它；睡着就交给它半睡半醒地决定。"""
    if cat.asleep:
        cat.sleep_events.put(event)
        cat.wake_now.set()
    else:
        cat.inbox.put(event)
        cat.interrupt.set()


def list_models(args):
    try:
        req = urllib.request.Request(args.base_url.rstrip("/") + "/models")
        with urllib.request.urlopen(req, timeout=5) as r:
            return [m["id"] for m in json.load(r).get("data", [])]
    except Exception as e:
        return [f"（连不上 {args.base_url}：{type(e).__name__}）"]


def command(cat: Cat, cmd, arg):
    """斜杠命令只管设置，不管猫。返回 False 表示退出。"""
    args = cat.args
    if cmd in ("/quit", "/exit"):
        return False
    if cmd == "/status":
        if cat.asleep:
            left = max(0, cat.sleep_until - time.time()) * args.speed / 60
            state = f"睡着，还要睡 {left:.0f} 分钟"
        else:
            state = "醒着" + (f"，在玩「{cat.game_title}」" if cat.toy else "") + (f"，{cat.status}" if cat.status else "")
        logs = sum(1 for f in (HERE / "轨迹").glob("*.jsonl") for _ in open(f, encoding="utf-8")) \
            if (HERE / "轨迹").exists() else 0
        out(f"  哈基米：{state}\n  模型：{args.model}{'（训练好的）' if args.trained else '（+ persona.md）'}"
            f" @ {args.base_url}\n  猫窝：{cat.room.root}\n  轨迹：{logs} 条")
    elif cmd == "/model":
        if arg:
            args.model = arg
            out(f"  下次思考起换成 {arg}")
        else:
            out("\n".join(("  ● " if m == args.model else "    ") + m for m in list_models(args)))
    else:
        out(HELP)
    return True


def handle(cat: Cat, line):
    """处理人类的一行输入。返回 False 表示要退出。"""
    line = line.strip()
    if not line:
        return True
    if line.startswith("/"):
        cmd, _, arg = line.partition(" ")
        return command(cat, cmd.lower(), arg.strip())
    out(f"  你 ▸ {line}", "you")
    poke(cat, "【有人说】" + line)
    return True

def cat_loop(cat: Cat):
    while True:
        minutes = cat.awake(cat.wake_reason)
        cat.wake_reason = ""
        if cat.args.once:
            out(f"（睡了。它想睡 {minutes:.0f} 分钟）", "dim")
            os._exit(0)
        cat.sleep_until = time.time() + minutes * 60 / cat.args.speed
        out(f"（哈基米睡着了，打算睡 {minutes:.0f} 分钟）", "dim")
        while True:
            cat.wake_now.clear()
            if not cat.wake_now.wait(timeout=max(0.0, cat.sleep_until - time.time())):
                break  # 自己睡醒了
            time.sleep(1.5)  # 等一下，连着戳好几下算一次
            events = []
            while not cat.sleep_events.empty():
                events.append(cat.sleep_events.get())
            if not events:
                continue
            left = drowsy(cat, events)
            if left is None:
                out("（它醒了）", "dim")
                cat.wake_reason = "\n" + "\n".join(events)
                break
            cat.sleep_until = time.time() + max(0.5, min(left, cat.args.max_sleep)) * 60 / cat.args.speed
            out(f"（没醒。还要睡 {left:.0f} 分钟）", "dim")
            cat.heard += events  # 醒来会迷迷糊糊记得；记不记下来是它的事

def main():
    p = argparse.ArgumentParser(description="哈基米：住在命令行里的猫")
    p.add_argument("--base-url", default=os.environ.get("HACHIMI_BASE_URL", "http://127.0.0.1:18000/v1"))
    p.add_argument("--model", default=os.environ.get("HACHIMI_MODEL", "qwen3.8-27b-rco"))
    p.add_argument("--trained", action="store_true", help="底层是训练好的哈基米模型，不塞人设")
    p.add_argument("--room", type=Path, default=HERE / "猫窝")
    p.add_argument("--speed", type=float, default=1.0, help="时间快进倍数（睡觉和动作都快进）")
    p.add_argument("--max-sleep", type=float, default=720, help="最多睡几分钟（它的时间，保险）")
    p.add_argument("--max-steps", type=int, default=200, help="一次醒来最多想几次（防止模型卡住的保险）")
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument("--once", action="store_true", help="醒一次，睡着就退出")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    make_room(args.room)
    cat = Cat(args)

    if not sys.stdin.isatty():
        out(BANNER, "cat")
        # 没有终端（被脚本驱动、测试）：一行一行读 stdin
        threading.Thread(target=cat_loop, args=(cat,), daemon=True).start()
        for line in sys.stdin:
            if not handle(cat, line):
                break
        else:
            while True:
                time.sleep(1)
        return

    run_ui(cat)


def run_ui(cat: Cat):
    """像 Claude Code 一样：上面滚动输出，中间是它在玩的游戏（玩的时候才有），
    底下一行状态栏，最下面是输入框。"""
    from prompt_toolkit.application import Application
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    def log_text():
        return [(f"class:{c}", line + "\n") for c, line in UI.lines]

    log = Window(FormattedTextControl(
        log_text, get_cursor_position=lambda: Point(0, max(0, len(UI.lines) - 1))),
        wrap_lines=True)
    game = ConditionalContainer(
        Frame(Window(FormattedTextControl(lambda: [("class:game", cat.game_frame)]),
                     dont_extend_height=True),
              title=lambda: f" 🎮 {cat.game_title} · 哈基米在玩 "),
        filter=Condition(lambda: cat.toy is not None))
    from prompt_toolkit.application import get_app
    mode = window.image_mode()
    cell = window.cell_size() if mode == "sixel" else (1, 2)
    drawn = {}

    def pic_box():
        """图框多大：宽度用满，高度最多占半个窗口。"""
        size = get_app().output.get_size()
        return max(20, size.columns - 4), max(6, size.rows // 2)

    def pic_frags():
        if cat.pic is None:
            return []
        cols, rows = pic_box()
        if mode == "sixel":  # 先占好位置，真图在画完界面以后贴上去
            return [("", (" " * cols + "\n") * rows)]
        key = (str(cat.pic), cols, rows)
        if key not in drawn:
            drawn.clear()
            try:
                drawn[key] = window.halfblock(cat.pic, cols, rows)
            except Exception:
                drawn[key] = [("", "（画不出来）")]
        return drawn[key]

    pic_win = Window(FormattedTextControl(pic_frags), dont_extend_height=True)
    pic = ConditionalContainer(
        Frame(pic_win, title=lambda: f" 👀 哈基米在看：{cat.pic_title} "),
        filter=Condition(lambda: cat.pic is not None and cat.toy is None))
    shown = {"key": None, "clearing": False}

    def paste_sixel(app):
        """界面每画完一次就看看：图变了就把旧图擦掉（整屏重画），再把新图贴到图框的位置。"""
        info = pic_win.render_info
        key = None
        if cat.pic is not None and cat.toy is None and info is not None:
            cols, rows = pic_box()
            key = (str(cat.pic), info._x_offset, info._y_offset, cols, rows)
        if key == shown["key"]:
            return
        if shown["key"] is not None and not shown["clearing"]:
            shown["clearing"] = True  # Sixel 的像素不会自己消失，只能整屏擦掉重画
            app.renderer.clear()
            app.invalidate()
            return
        shown["clearing"] = False
        shown["key"] = key
        if key is None:
            return
        try:
            data, _, _ = window.sixel(cat.pic, min(900, key[3] * cell[0]), min(640, key[4] * cell[1]))
        except Exception:
            return
        app.output.write_raw(f"\x1b7\x1b[{key[2] + 1};{key[1] + 1}H{data}\x1b8")
        app.output.flush()
    bar = Window(FormattedTextControl(lambda: [("class:bar", cat.toolbar())]), height=1, style="class:bar")

    def accept(buf):
        if not handle(cat, buf.text):
            app.exit()
        return False  # 清空输入框

    box = TextArea(height=1, prompt="› ", multiline=False, wrap_lines=False, accept_handler=accept)
    kb = KeyBindings()

    @kb.add("c-c")
    @kb.add("c-d")
    def _(event):
        event.app.exit()

    from prompt_toolkit.output import ColorDepth
    truecolor = "WT_SESSION" in os.environ or os.environ.get("COLORTERM") in ("truecolor", "24bit")
    app = Application(
        layout=Layout(HSplit([log, game, pic, bar, box]), focused_element=box),
        key_bindings=kb, full_screen=True, refresh_interval=0.3,
        color_depth=ColorDepth.TRUE_COLOR if truecolor else None,  # 图要真彩色才好看
        style=Style.from_dict({"cat": "#ffb36b", "song": "#ff9de2", "dim": "#8a8a8a",
                               "you": "#5fd7ff", "sys": "#9e9e9e", "game": "#e8e8e8",
                               "bar": "bg:#3a2a1a #ffb36b", "frame.border": "#ffb36b"}))
    if mode == "sixel":
        app.after_render += paste_sixel
    UI.lines = [("cat", line) for line in BANNER.split("\n")]
    UI.app = app
    threading.Thread(target=cat_loop, args=(cat,), daemon=True).start()
    app.run()
    UI.app = None
    print("（你轻轻关上了门）")


if __name__ == "__main__":
    main()