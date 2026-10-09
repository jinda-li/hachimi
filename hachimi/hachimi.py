"""哈基米：一只住在命令行里的猫。

它不是你说一句它做一句。它自己醒来，自己决定干什么，干完自己去睡，睡多久它自己定。
你可以随时打字跟它说话、对它做事（「摸摸你」「开个罐罐」）。它理不理你、醒不醒，它自己定。

用法：
  python hachimi.py                 # 第一次会先领养（问几个问题），再问它的脑袋用哪个模型服务
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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import senses
import toybox
import window
import 零件

HERE = Path(__file__).resolve().parent
SYSTEM_MIN = "你住在这台电脑里。"  # 训练数据里的系统消息，性格在权重里
WEEK = "一二三四五六日"
NAME = "哈基米"  # 领养的时候起的名字

# ── 显示 ──────────────────────────────────────────────

os.system("")  # 让 Windows 终端认 ANSI 颜色
C = {"cat": "\033[38;5;215m", "dim": "\033[2m", "you": "\033[36m", "visit": "\033[38;5;222m",
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
PARTS_DOC = "玩具箱/零件说明.md"
HOME_LIST = ("纸箱（纸箱/，空的，可以进去坐）\n桌子（下面是 桌子底下/）\n门（门口/、门缝/）\n"
             "窗户（能扒着看外面）\n玩具箱（玩具箱/，空的）\n")


def parts_doc():
    """零件说明：从 零件.py 里现读出来，零件改了说明跟着变。"""
    import inspect
    rows = [零件.__doc__.strip(), ""]
    for name in ("躲避", "接住", "扑", "打地鼠", "跳"):
        sig = inspect.signature(getattr(零件, name))
        rows.append(f"零件.{name}(" + ", ".join(f"{k}={v.default!r}" for k, v in sig.parameters.items()) + ")")
    rows += ["", "多少 是 0 到 1 之间的数，越大越多；终点、时长 是走多少步结束，0 就是不结束。",
             "做好以后用 play 打开自己先玩。好玩再拿去给两脚兽。"]
    return "\n".join(rows) + "\n"


def make_room(room: Path):
    """建好猫窝。返回 True 表示这是一个新家（这只猫刚出生）。"""
    room.mkdir(parents=True, exist_ok=True)
    for d in ROOM_DIRS:  # 旧的猫窝也补上新加的地方
        (room / d).mkdir(exist_ok=True)
    (room / PARTS_DOC).write_text(parts_doc(), encoding="utf-8")
    if not (room / "家里有什么.md").exists():
        (room / "家里有什么.md").write_text(HOME_LIST, encoding="utf-8")
    if not (room / "两脚兽.md").exists():
        (room / "两脚兽.md").write_text("", encoding="utf-8")
    if (room / "猫牌.md").exists():
        return False
    (room / "猫牌.md").write_text(f"我叫{NAME}。住在这台电脑里。\n今天的毛色：还没想好。\n", encoding="utf-8")
    (room / "窗外清单.md").write_text("", encoding="utf-8")
    (room / "我闻过的.md").write_text("", encoding="utf-8")
    (room / "出生.txt").write_text(
        f"你好。\n\n你出生在 {dt.date.today():%Y 年 %m 月 %d 日}。\n"
        "这台电脑是你的。你想做什么都可以。\n\n"
        "如果你读到这封信，说明你已经会自己找东西了。\n", encoding="utf-8")
    return True


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
        if any(part.startswith(".") for part in full.relative_to(self.root).parts):
            raise PermissionError  # 点开头的是框架的东西（天性），猫碰不到
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

    def dream(self):
        """睡着的时候梦到小本本里的一件旧事（今天以前记的）。梦到了做不做点什么，它自己定。"""
        today = f"{dt.date.today()}"
        old = [line.strip() for line in self.peek("两脚兽.md", "").splitlines()
               if (m := re.search(r"\d{4}-\d{2}-\d{2}", line)) and m.group(0) < today]
        return random.choice(old) if old and random.random() < 0.5 else None


# ── 天性和"我们" ─────────────────────────────────────────
# 领养时定下的天性，和框架替它记着的"我们"（认识几天、说过几次话……）。每次醒来告诉它。
# 存在 猫窝/.天性.json：拷走猫窝就是带走这只猫；点开头，猫自己碰不到。

class Bond:
    def __init__(self, room: Path):
        self.f = room / ".天性.json"
        self.lock = threading.Lock()
        self.d = json.loads(self.f.read_text(encoding="utf-8")) if self.f.exists() else {}

    @property
    def adopted(self):
        return bool(self.d.get("名字"))

    def save(self):
        with self.lock:
            self.f.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")

    def bump(self, key):
        we = self.d.setdefault("我们", {})
        we[key] = we.get(key, 0) + 1
        self.save()

    def touch(self):
        self.d["上次"] = time.time()
        self.save()

    def describe(self):
        rows = []
        if self.d.get("天性"):
            rows.append("【天性】" + self.d["天性"])
        if self.d.get("来的那天"):
            days = (dt.date.today() - dt.date.fromisoformat(self.d["来的那天"])).days + 1
            we = self.d.get("我们", {})
            rows.append(f"【我们】你来这个家第 {days} 天。两脚兽跟你说过 {we.get('说话', 0)} 次话，"
                        f"玩过你做的玩具 {we.get('玩玩具', 0)} 次；你去找过两脚兽 {we.get('来找', 0)} 次。")
        return rows


# 领养：像去领养一只猫，问几个情景题。答案变成天性（框架每次告诉它）和猫牌（它自己的，之后它自己改）
ADOPT = [
    ("它是怎么来的？", [
        ("纸箱里捡的", "被装在纸箱里捡回来的，刚来有点怕生"),
        ("领养中心", "从领养中心来的，见过很多人，不太怕生"),
        ("顺着网线自己钻进来的", "自己顺着网线钻进来的，谁也没请，觉得这里本来就是自己的"),
        ("朋友家的猫生的", "朋友家的猫生的，从小被人摸大，很亲人")]),
    ("你第一次伸手，它会？", [
        ("蹭过来", "黏人，喜欢蹭"),
        ("闻一闻，走开", "独立，要人等它自己过来"),
        ("躲到桌子底下", "胆子小，要慢慢熟"),
        ("一口咬住", "凶，爱玩，咬人不疼")]),
    ("桌上有个杯子，它会？", [
        ("不理", "比较乖，很少使坏"),
        ("盯着看很久", "什么都好奇，能盯着一个东西看很久"),
        ("看着你，慢慢推下去", "坏坏的，使坏前要先看两脚兽一眼")]),
    ("它半夜会？", [
        ("睡得很死", "很能睡"),
        ("在家里跑酷", "夜里精神，半夜发疯"),
        ("蹲在枕头边盯着你", "喜欢盯着两脚兽看，有点怪")]),
]
WHEN = ("你一般什么时候在电脑前？", ["白天", "晚上", "一整天都在", "说不准"])
WHEN_NOTE = ["两脚兽一般白天在电脑前", "两脚兽一般晚上在电脑前", "两脚兽一整天都在电脑前", "两脚兽什么时候在电脑前说不准"]


def ask(question, options, pick=None):
    """问一道选择题。回车或者看不懂就随它去（随机）。"""
    if pick is None:
        print(f"\n{C['cat']}{question}{C['off']}")
        for i, o in enumerate(options, 1):
            print(f"  {i}. {o}")
        try:
            got = input(f"{C['dim']}  选一个（回车＝随它）：{C['off']}").strip()
        except EOFError:
            got = ""
        pick = int(got) - 1 if got.isdigit() and 0 < int(got) <= len(options) else random.randrange(len(options))
    return pick


def adopt(bond: Bond, room: Path, born: bool, interactive: bool):
    """第一次打开：领养。没有终端的时候（脚本、测试）全都随它。"""
    global NAME
    if interactive:
        print(f"{C['cat']}{BANNER}{C['off']}")
        print("纸箱里有一只猫。它在看你。")
        try:
            name = input(f"\n{C['cat']}它叫什么？{C['off']}{C['dim']}（回车＝哈基米）：{C['off']}").strip()
        except EOFError:
            name = ""
    else:
        name = os.environ.get("HACHIMI_NAME", "")
    NAME = name[:12] or "哈基米"
    traits = []
    for q, opts in ADOPT:
        i = ask(q, [o for o, _ in opts], None if interactive else random.randrange(len(opts)))
        traits.append(opts[i][1])
    when = WHEN_NOTE[ask(WHEN[0], WHEN[1], None if interactive else 3)]
    bond.d.update({"名字": NAME, "天性": "；".join(traits) + "。", "来历": traits[0],
                   "来的那天": f"{dt.date.today()}", "我们": bond.d.get("我们", {})})
    bond.touch()
    if born:  # 新猫：猫牌写上名字和来历。老猫的猫牌是它自己写的，不动
        (room / "猫牌.md").write_text(f"我叫{NAME}。住在这台电脑里。\n来历：{traits[0]}。\n今天的毛色：还没想好。\n",
                                     encoding="utf-8")
    with open(room / "两脚兽.md", "a", encoding="utf-8") as fo:
        fo.write(f"- {dt.date.today()} 领养那天。{when}。\n")
    if interactive:
        print(f"\n{C['cat']}{NAME}{C['off']}：{bond.d['天性']}")
        print(f"{C['dim']}它钻进纸箱里睡着了。等它醒。{C['off']}\n")
        time.sleep(1.2)


# ── 它的脑袋（模型服务） ─────────────────────────────────
# 云端优先：任何 OpenAI 兼容、会调工具（最好能看图）的服务都行。设置存在 设置.json，不进 git。

SETTINGS = HERE / "设置.json"
BRAINS = [
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("阿里云百炼（通义千问）", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("OpenAI", "https://api.openai.com/v1"),
    ("本地（llama-swap / Ollama 之类）", "http://127.0.0.1:18000/v1"),
]


def load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def setup_brain():
    """第一次打开、又没在环境变量里指定的时候，问一下它的脑袋用哪个服务。"""
    import getpass
    print(f"\n{C['cat']}它的脑袋用哪个模型服务？{C['off']}{C['dim']}（要会调工具，最好能看图）{C['off']}")
    for i, (name, url) in enumerate(BRAINS, 1):
        print(f"  {i}. {name}  {C['dim']}{url}{C['off']}")
    print(f"  {len(BRAINS) + 1}. 自己填地址")
    got = input("  选一个：").strip()
    i = int(got) - 1 if got.isdigit() else -1
    url = BRAINS[i][1] if 0 <= i < len(BRAINS) else input("  地址（…/v1）：").strip()
    model = input("  模型名：").strip()
    key = "" if "127.0.0.1" in url or "localhost" in url else getpass.getpass("  API key（不显示，存在本机 设置.json）：").strip()
    s = {"base_url": url, "model": model, "api_key": key}
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{C['dim']}  存好了。以后想换：改 {SETTINGS.name}，或者用 --base-url / --model / 环境变量。{C['off']}")
    return s


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
    _fn("visit", "去找两脚兽：跑到两脚兽面前（屏幕会跳到最前面），蹭、叫、给两脚兽看东西。say 是你跟两脚兽说的话。"
        "gift 是叼过去的东西：窗外/ 里的图（给两脚兽看）、玩具箱/ 里的 .py 游戏（两脚兽会真的玩，玩完你会知道）、"
        "门口/ 的礼物。两脚兽不在电脑前的话，东西会放在门口等两脚兽回来", {"say": S, "gift": S}, ["say"]),
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
                items = sorted((p for p in f.iterdir() if not p.name.startswith(".")),
                               key=lambda p: (p.is_file(), p.name))
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

def is_local(url):
    return urllib.parse.urlparse(url).hostname in ("127.0.0.1", "localhost", "::1")


def headers(args):
    h = {"Content-Type": "application/json"}
    if args.api_key:
        h["Authorization"] = "Bearer " + args.api_key
    return h


def chat(args, messages, tools=None, max_tokens=2500):
    body = {"model": args.model, "messages": messages, "tools": tools or TOOLS,
            # 没训练过的模型不截断会自己刹不住车
            "temperature": args.temperature, "max_tokens": max_tokens, "frequency_penalty": 0.3}
    # 本地的 Qwen 要关掉思考；云端各家的开关不一样，写在 设置.json 的 extra_body 里
    body.update(args.extra_body if args.extra_body is not None
                else {"chat_template_kwargs": {"enable_thinking": False}} if is_local(args.base_url) else {})
    req = urllib.request.Request(args.base_url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), method="POST", headers=headers(args))
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)["choices"][0]["message"]
    except urllib.error.HTTPError as e:  # 云端说不行的时候，把它说的原话带出来
        raise RuntimeError(f"HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:300]}") from None


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

def system_prompt(args):
    """训练好的模型只要一句话，性格在权重里；训练前用 persona.md 撑着。名字和天性在醒来消息里。"""
    if args.trained:
        return SYSTEM_MIN
    return (HERE / "persona.md").read_text(encoding="utf-8").replace("你是哈基米", f"你是{NAME}", 1)


AWAY_MINUTES = 10   # 键盘鼠标这么久没动，就算两脚兽不在电脑前
BREAK_MINUTES = 5   # 离开这么久，就算起来歇过了，坐了多久重新算
BACK_MINUTES = 20   # 走开这么久再回来，才算"回来了"
POP_GAP = 600       # 窗口跳到最前面，最多每 10 分钟一次（保险）
HUMAN_TICK = 0.3    # 两脚兽玩游戏时，不按键游戏也每 0.3 秒自己走一步
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


class Cat:
    def __init__(self, args, bond=None, born=False):
        self.args = args
        self.bond = bond or Bond(args.room)
        self.room = Room(args.room)
        self.inbox = queue.Queue()            # 人类在它醒着的时候说的话、做的事
        self.sleep_events = queue.Queue()     # 人类在它睡着的时候做的事
        self.interrupt = threading.Event()    # 人类一出声，正在演的动作就被打断
        self.wake_now = threading.Event()
        self.wake_reason = ""
        self.asleep = True
        self.born = born                      # 第一次醒来就是刚出生
        # 程序关着的时候它在睡觉：从上次关门算起（真实时间，不快进）
        self.sleep_started = time.time() - (time.time() - self.bond.d.get("上次", time.time())) / args.speed
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
        self.pic_for_human = False            # 图是它叼来给两脚兽看的
        self.player = "cat"                   # 游戏谁在玩：cat / human
        self.toy_lock = threading.Lock()
        self.human_keys = 0
        self.human_from_cat = True
        self.last_key = 0.0
        self.pending = []                     # 两脚兽不在的时候它留在门口的：(说的话, 东西)
        self.idle = None                      # 键盘鼠标多久没动了（秒），不知道就是 None
        self.last_typed = 0.0                 # 两脚兽上次跟它说话
        self.last_pop = 0.0

    # 两脚兽在不在

    def present(self):
        """两脚兽在不在电脑前：True / False / None（不知道）。"""
        if time.time() - self.last_typed < AWAY_MINUTES * 60:
            return True
        return None if self.idle is None else self.idle < AWAY_MINUTES * 60

    def presence_line(self):
        p = self.present()
        if p is None:
            return ""
        return "两脚兽在电脑前。" if p else f"两脚兽不在电脑前（走开 {int(self.idle // 60)} 分钟了）。"

    def senses_loop(self):
        """只看键盘鼠标多久没动：两脚兽走开了、回来了、坐太久了，告诉它。管不管是它的事。"""
        streak, told, away_since = time.time(), 0.0, None
        while True:
            idle = senses.idle_seconds()
            if idle is None:
                return  # 这台电脑上感觉不到，就当不知道
            self.idle, now = idle, time.time()
            if idle >= BREAK_MINUTES * 60:
                streak = now  # 起来歇过了
            if idle >= AWAY_MINUTES * 60:
                away_since = away_since or now - idle
            elif away_since is not None:
                gone, away_since = now - away_since, None
                if gone >= BACK_MINUTES * 60:
                    self.came_back(gone)
            sat = now - streak
            if sat >= self.args.work_minutes * 60 and now - told >= 60 * 60:
                told = now
                h, m = divmod(int(sat // 60), 60)
                poke(self, f"【你注意到】两脚兽在电脑前坐了 {h} 小时 {m} 分钟了，一直没起来。")
            time.sleep(15)

    def came_back(self, gone):
        shown = []
        for said, f in self.pending:
            self.show_visit(said, f, late=True)
            if f:
                shown.append(f"「{f.relative_to(self.room.root).as_posix()}」")
        self.pending = []
        event = f"【你听到】两脚兽回来了（走开了 {int(gone // 60)} 分钟）。"
        if shown:
            event += f"两脚兽看到了你留在门口的{'、'.join(shown)}。"
        poke(self, event)

    # 去找两脚兽

    def pop(self):
        """跑到两脚兽面前：窗口跳到最前面，叮一声。有保险，不会一直跳。"""
        if UI.app:
            UI.app.output.bell()
        else:
            print("\a", end="", flush=True)
        if not self.args.no_pop and time.time() - self.last_pop >= POP_GAP:
            self.last_pop = time.time()
            senses.come_to_front()

    def show_visit(self, said, f, late=False):
        rel = f.relative_to(self.room.root).as_posix() if f else ""
        self.pop()
        out(f"  ╭─ 🐾 {'你不在的时候，' + NAME + '来过' if late else NAME + '来找你了'}", "visit")
        for line in said.splitlines() or [""]:
            out(f"  │ {line}", "visit")
        if f and f.suffix.lower() in IMAGE_EXT:
            self.pic, self.pic_title, self.pic_for_human = f, rel, True
            out(f"  ╰─ 叼着一张图：{rel}", "visit")
            if not UI.app:
                out(f"    [图：{rel}]", "dim")
        elif f and f.suffix == ".py":
            if late:
                out(f"  ╰─ 叼着一个玩具：{f.stem}。输入 /玩 {f.stem} 来玩", "visit")
            else:
                out(f"  ╰─ 叼着一个玩具：{f.stem}（方向键、空格玩，Esc 放下）", "visit")
        elif f:
            text = f.read_text(encoding="utf-8", errors="replace").strip().splitlines()[:12]
            out(f"  ╰─ 叼着：{rel}", "visit")
            for line in text:
                out(f"     {line}", "dim")
        else:
            out("  ╰─", "visit")
        UI.invalidate()

    def visit(self, a):
        said = str(a.get("say") or "").strip()
        gift = str(a.get("gift") or "").strip()
        f = None
        if gift:
            try:
                f = self.room.path(gift)
            except PermissionError:
                return "门关着。"
            if not f.is_file():
                return "没有这个东西，叼不过去。"
            if f.suffix == ".py" and (bad := toybox.check(f.read_text(encoding="utf-8"))):
                return f"这个玩具还不能给两脚兽玩：{bad}"
        self.bond.bump("来找")
        if self.present() is False:
            self.pending.append((said, f))
            left = f"你把「{f.name}」留给两脚兽了，两脚兽回来就会看到。" if f else "你在空椅子上坐了一会儿。"
            return self.presence_line() + left
        self.show_visit(said, f)
        if f and f.suffix == ".py":
            r = self.start_human_toy(f)
            return f"你跑到两脚兽面前。{r}"
        if f and f.suffix.lower() in IMAGE_EXT:
            return "你跑到两脚兽面前，把图摆在两脚兽眼前。"
        return "你跑到两脚兽面前。" + ("把东西放下了。" if f else "")

    # 两脚兽玩它做的游戏

    def start_human_toy(self, f, from_cat=True):
        bad = toybox.check(f.read_text(encoding="utf-8"))
        if bad:
            return bad
        with self.toy_lock:
            self.stop_toy(quiet=True)
            toy = toybox.Toy(f)
            r = toy.call("start")
            if "error" in r:
                toy.close()
                return f"玩具一打开就坏了：{r['error']}"
            self.toy, self.game_title, self.game_frame = toy, f.stem, r["frame"]
            self.player, self.human_keys, self.human_from_cat = "human", 0, from_cat
            self.last_key = time.time()
        if not UI.app:
            out(r["frame"], "dim")
        threading.Thread(target=self.human_tick, args=(toy,), daemon=True).start()
        UI.invalidate()
        return f"两脚兽打开了「{f.stem}」，在玩。你在旁边看着，玩完了你会知道。"

    def human_tick(self, toy):
        """两脚兽不按键，游戏也自己往前走。"""
        while self.toy is toy and self.player == "human":
            time.sleep(HUMAN_TICK)
            if time.time() - self.last_key >= HUMAN_TICK:
                self.human_step("", toy)

    def human_press(self, key):
        self.last_key = time.time()
        self.human_keys += 1
        self.human_step(key, self.toy)

    def human_step(self, key, toy):
        with self.toy_lock:
            if self.toy is not toy or self.player != "human":
                return
            r = toy.call("step", key)
            if "error" in r:
                self.human_done(f"玩到一半，玩具坏了：{r['error']}\n{r.get('tb', '')}".strip())
                return
            self.game_frame = r["frame"]
            if r["over"]:
                self.human_done("玩到结束了")
        UI.invalidate()

    def human_quit(self):
        with self.toy_lock:
            if self.toy and self.player == "human":
                self.human_done("就放下了")

    def human_done(self, how):
        """两脚兽玩完了（拿着 toy_lock 调）。告诉猫，画面留一会儿再收起来。"""
        title, frame, n = self.game_title, self.game_frame, self.human_keys
        self.player = "done"
        whose = "你做的" if self.human_from_cat else "你玩具箱里的"
        if n == 0:
            how = "没怎么碰它，" + how
        if n:
            self.bond.bump("玩玩具")
        out(f"    🎮 {title}：你按了 {n} 下，{how.splitlines()[0]}", "you")
        poke(self, f"【两脚兽玩了{whose}「{title}」】按了 {n} 下，{how}。最后的画面：\n{frame}")
        toy = self.toy

        def later():
            time.sleep(3)
            with self.toy_lock:
                if self.toy is toy:
                    self.stop_toy(quiet=True)
        threading.Thread(target=later, daemon=True).start()

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
        self.pic, self.pic_title, self.seen, self.pic_for_human = f, rel, (rel, url), False
        out(f"    👀 {NAME}在看「{rel}」", "you")
        if not UI.app:
            out(f"    [图：{rel}]", "dim")
        UI.invalidate()
        return "你看着它。"

    # 玩具箱里的游戏

    def start_toy(self, path):
        if self.player == "human":
            return f"两脚兽在玩「{self.game_title}」，等两脚兽玩完。"
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
        with self.toy_lock:
            self.stop_toy(quiet=True)
            toy = toybox.Toy(f)
            r = toy.call("start")
            if "error" in r:
                toy.close()
                return f"玩具一打开就坏了：{r['error']}\n{r.get('tb', '')}".strip()
            self.toy, self.game_title, self.game_frame = toy, f.stem, r["frame"]
        self.pic = None
        out(f"    🎮 {NAME}打开了「{f.stem}」", "you")
        if not UI.app:
            out(r["frame"], "dim")
        return "开始了。\n" + r["frame"] + "\n（用 press 按键玩）"

    def press(self, keys):
        """一下一下按，每下游戏走一步，画面跟着动。有动静就先停下，告诉它按了几下。"""
        if self.player == "human":
            return f"两脚兽在玩，你只能在旁边看。现在的画面：\n{self.game_frame}"
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
                out(f"    （{NAME}把「{self.game_title}」放下了）", "dim")
            UI.invalidate()
        self.player = "cat"

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
        parts += ["【猫牌.md】\n" + self.room.peek("猫牌.md")]
        parts += self.bond.describe()
        notebook = self.room.peek("两脚兽.md").splitlines()
        parts.append("【两脚兽.md】\n" + "\n".join(notebook[-30:]))
        if dream := self.room.dream():
            parts.append(f"【刚才睡着的时候梦到】小本本里的一件旧事：{dream}")
        if self.presence_line():
            parts.append("【两脚兽】" + self.presence_line())
        parts += ["【家里有什么.md】\n" + self.room.peek("家里有什么.md"),
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
        if name == "visit":
            return self.visit(a), None
        dst = str(a.get("dst", "")).lstrip("/\\")
        if name == "move" and dst.startswith("藏起来的"):
            out(f"    （{NAME}把什么东西扒拉进了角落）", "dim")  # 藏东西不给你看
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
            out(f"    （{NAME}把图叼回来了：{a.get('path')}）", "you")
        if name == "move" and res == "已移动。":
            thing = Path(str(a.get("src", ""))).name
            if dst.startswith("门口"):
                out(f"    （{NAME}叼了「{thing}」放在门口）", "you")
            elif dst.startswith("桌子底下"):
                out(f"    咣当！{NAME}把「{thing}」推下了桌子", "you")
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
        out(f"（{NAME}的脑袋连不上：{err}）", "sys")
        return None

    def awake(self, reason=""):
        """从醒来到睡着：标准的 agent 循环。模型说话、调工具，工具结果交回给模型，直到它自己去睡。
        人在中间说的话排队，等它这一步做完再交给它。返回它想睡几分钟。"""
        self.asleep = False
        wake = self.wake_message(reason)
        msgs = [{"role": "system", "content": system_prompt(self.args)}, {"role": "user", "content": wake}]
        self.last_wake = time.time()
        out(f"\n（{NAME}醒了）", "dim")
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
                out(f"（{NAME}发了会儿呆，睡着了）", "dim")
                sleep_for = 60
                break
            last_text = text
            while self.inbox.empty():
                done, _ = self.hold("等着", HEARTBEAT_MINUTES * 60 / self.args.speed)
                if done and self.inbox.empty():
                    msgs.append({"role": "user", "content": f"【现在】{dt.datetime.now():%H:%M}。周围很安静。"
                                                            + self.presence_line()})
                    break
        if sleep_for is None:  # 保险：想太多次了
            out(f"（{NAME}玩累了，自己趴下了）", "dim")
            sleep_for = 60
        if self.player != "human":  # 两脚兽还在玩的话，让两脚兽玩完
            self.stop_toy(quiet=True)
        if not self.pic_for_human:
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
        self.bond.touch()

    def toolbar(self):
        now = time.time()
        if self.player == "human":
            return f" 🎮 你在玩「{self.game_title}」· 方向键 / 空格 · Esc 放下 · {NAME}在旁边看 "
        if self.asleep and self.status:
            return f" 💤 {NAME}在睡觉 · {self.status}…… "
        if self.asleep:
            left = max(0, self.sleep_until - now) * self.args.speed / 60
            z = "咪" * (1 + int(now * 2) % 3)
            return f" 💤 {NAME}在睡觉 · 吼咪{z} · 还要睡 {left:.0f} 分钟 "
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
    msg = (f"【你在睡觉】已经睡了 {slept:.0f} 分钟，本来还要睡 {left:.0f} 分钟。\n" + "\n".join(events))
    msgs = [{"role": "system", "content": system_prompt(cat.args)}, {"role": "user", "content": msg}]
    cat.status = "耳朵动了一下"
    try:
        m = chat(cat.args, msgs, tools=DROWSY_TOOLS, max_tokens=300)
    except Exception as e:
        out(f"（{NAME}的脑袋连不上：{e}）", "sys")
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
  偷看它的生活：
  /日记            它最近的日记
  /本本            它的小本本：它记下的关于你的事
  /猫牌            它的猫牌、天性，你们认识多久了
  /玩具            它做过的玩具
  /玩 名字         玩它做的玩具（方向键、空格，Esc 放下）。它会知道
  /门口            它叼到门口的东西
  /看 路径         看猫窝里的一张图（比如 /看 窗外/鸟/蓝松鸦.jpg）；不给路径就收起来
  设置：
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
        req = urllib.request.Request(args.base_url.rstrip("/") + "/models", headers=headers(args))
        with urllib.request.urlopen(req, timeout=5) as r:
            return [m["id"] for m in json.load(r).get("data", [])]
    except Exception as e:
        return [f"（连不上 {args.base_url}：{type(e).__name__}）"]


def listing(folder: Path, pattern="*"):
    files = sorted(f for f in folder.glob(pattern) if f.is_file() and not f.name.startswith("."))
    return "\n".join(f"    {f.relative_to(folder).as_posix()}" for f in files) or "    （空的）"


def peek(cat: Cat, cmd, arg):
    """偷看它的生活。它不知道你看了，除了玩它的玩具。返回 False 表示不是偷看的命令。"""
    room = cat.room.root
    if cmd in ("/日记", "/diary"):
        out(cat.room.latest_diary(), "dim")
    elif cmd in ("/本本", "/notes"):
        out(f"  （你偷偷翻开{NAME}的小本本）\n" + cat.room.peek("两脚兽.md"), "dim")
    elif cmd in ("/猫牌", "/card"):
        out("\n".join([cat.room.peek("猫牌.md")] + cat.bond.describe()), "dim")
    elif cmd in ("/玩具", "/toys"):
        out(f"  {NAME}的玩具箱：\n" + listing(room / "玩具箱", "*.py") + "\n  （/玩 名字 来玩）", "dim")
    elif cmd in ("/玩", "/play"):
        f = room / "玩具箱" / (arg.removesuffix(".py") + ".py")
        if not arg or not f.is_file():
            out(f"  玩具箱里没有「{arg}」。/玩具 看看有什么", "sys")
        else:
            out("  " + cat.start_human_toy(f, from_cat=False).replace("在玩。你在旁边看着，玩完了你会知道。", "开始了"),
                "you")
    elif cmd in ("/门口", "/door"):
        out("  门口：\n" + listing(room / "门口") + (f"\n  还有 {len(cat.pending)} 样等你回来再看" if cat.pending else ""),
            "dim")
    elif cmd in ("/看", "/look"):
        if not arg:
            cat.pic, cat.pic_for_human = None, False
        else:
            try:
                f = cat.room.path(arg)
            except PermissionError:
                f = None
            if not f or not f.is_file() or f.suffix.lower() not in IMAGE_EXT:
                out(f"  猫窝里没有这张图：{arg}", "sys")
            else:
                cat.pic, cat.pic_title, cat.pic_for_human = f, f.relative_to(room).as_posix(), True
                if not UI.app:
                    out(f"    [图：{cat.pic_title}]", "dim")
        UI.invalidate()
    else:
        return False
    return True


def command(cat: Cat, cmd, arg):
    """斜杠命令只管偷看和设置，不管猫。返回 False 表示退出。"""
    args = cat.args
    if cmd in ("/quit", "/exit"):
        return False
    if peek(cat, cmd, arg):
        return True
    if cmd == "/status":
        if cat.asleep:
            left = max(0, cat.sleep_until - time.time()) * args.speed / 60
            state = f"睡着，还要睡 {left:.0f} 分钟"
        else:
            state = "醒着" + (f"，在玩「{cat.game_title}」" if cat.toy else "") + (f"，{cat.status}" if cat.status else "")
        logs = sum(1 for f in (HERE / "轨迹").glob("*.jsonl") for _ in open(f, encoding="utf-8")) \
            if (HERE / "轨迹").exists() else 0
        out(f"  {NAME}：{state}\n  模型：{args.model}{'（训练好的）' if args.trained else '（+ persona.md）'}"
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
    cat.last_typed = time.time()
    cat.bond.bump("说话")
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
        out(f"（{NAME}睡着了，打算睡 {minutes:.0f} 分钟）", "dim")
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
    p.add_argument("--base-url", help="模型服务地址（OpenAI 兼容，…/v1）。也可以用 HACHIMI_BASE_URL 或 设置.json")
    p.add_argument("--model", help="模型名。也可以用 HACHIMI_MODEL 或 设置.json")
    p.add_argument("--api-key", help="云端服务的 key。最好用 HACHIMI_API_KEY 或 设置.json，别留在命令历史里")
    p.add_argument("--trained", action="store_true", help="底层是训练好的哈基米模型，不塞人设")
    p.add_argument("--room", type=Path, default=HERE / "猫窝")
    p.add_argument("--speed", type=float, default=1.0, help="时间快进倍数（睡觉和动作都快进）")
    p.add_argument("--max-sleep", type=float, default=720, help="最多睡几分钟（它的时间，保险）")
    p.add_argument("--max-steps", type=int, default=200, help="一次醒来最多想几次（防止模型卡住的保险）")
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument("--once", action="store_true", help="醒一次，睡着就退出")
    p.add_argument("--work-minutes", type=float, default=90, help="两脚兽在电脑前坐了多久没起来，就告诉它")
    p.add_argument("--no-pop", action="store_true", help="它来找你的时候不把窗口叫到最前面，只叮一声")
    p.add_argument("--no-senses", action="store_true", help="不让它感觉你在不在电脑前")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    interactive = sys.stdin.isatty()

    global NAME
    born = make_room(args.room)
    bond = Bond(args.room)
    if not bond.adopted:
        adopt(bond, args.room, born, interactive)
    NAME = bond.d["名字"]

    # 它的脑袋：命令行 > 环境变量 > 设置.json > 第一次打开时问
    conf = load_settings()
    if not (args.base_url or os.environ.get("HACHIMI_BASE_URL") or conf.get("base_url")):
        conf = setup_brain() if interactive else {"base_url": BRAINS[-1][1]}
    args.base_url = args.base_url or os.environ.get("HACHIMI_BASE_URL") or conf.get("base_url")
    args.model = (args.model or os.environ.get("HACHIMI_MODEL") or conf.get("model")
                  or ("qwen3.8-27b-rco" if is_local(args.base_url) else ""))
    args.api_key = args.api_key or os.environ.get("HACHIMI_API_KEY") or conf.get("api_key") or ""
    args.extra_body = conf.get("extra_body")

    cat = Cat(args, bond, born)
    if not born:
        cat.wake_reason = "两脚兽开门进来了。"
    if not args.no_senses:
        threading.Thread(target=cat.senses_loop, daemon=True).start()

    if not interactive:
        out(BANNER.replace("哈基米", NAME), "cat")
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
              title=lambda: f" 🎮 {cat.game_title} · "
              + ("你在玩（方向键 / 空格，Esc 放下）" if cat.player == "human" else f"{NAME}在玩") + " "),
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
        Frame(pic_win, title=lambda: (f" 🐾 {NAME}叼来给你看：" if cat.pic_for_human else f" 👀 {NAME}在看：")
              + f"{cat.pic_title} "),
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

    # 两脚兽在玩它做的游戏：方向键和空格给游戏，Esc 放下
    playing = Condition(lambda: cat.toy is not None and cat.player == "human")
    for key, name in (("up", "上"), ("down", "下"), ("left", "左"), ("right", "右"), (" ", "空格")):
        kb.add(key, filter=playing, eager=True)(lambda e, n=name: cat.human_press(n))
    kb.add("escape", filter=playing, eager=True)(lambda e: cat.human_quit())

    from prompt_toolkit.output import ColorDepth
    truecolor = "WT_SESSION" in os.environ or os.environ.get("COLORTERM") in ("truecolor", "24bit")
    app = Application(
        layout=Layout(HSplit([log, game, pic, bar, box]), focused_element=box),
        key_bindings=kb, full_screen=True, refresh_interval=0.3,
        color_depth=ColorDepth.TRUE_COLOR if truecolor else None,  # 图要真彩色才好看
        style=Style.from_dict({"cat": "#ffb36b", "song": "#ff9de2", "dim": "#8a8a8a", "visit": "bold #ffd75f",
                               "you": "#5fd7ff", "sys": "#9e9e9e", "game": "#e8e8e8",
                               "bar": "bg:#3a2a1a #ffb36b", "frame.border": "#ffb36b"}))
    if mode == "sixel":
        app.after_render += paste_sixel
    UI.lines = [("cat", line) for line in BANNER.replace("哈基米", NAME).split("\n")]
    UI.app = app
    threading.Thread(target=cat_loop, args=(cat,), daemon=True).start()
    app.run()
    UI.app = None
    cat.bond.touch()
    print("（你轻轻关上了门）")


if __name__ == "__main__":
    main()