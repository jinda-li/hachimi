"""给 README 画图：把哈基米真实的醒来（hachimi/演示.jsonl 和试养记录里的原话）画成暖色终端窗口的动图。

    python docs/画图.py        # 生成 docs/*.gif、docs/*.png

字都是它真说过的，只挑了片段；游戏画面是它真玩的时候的画面。要 Pillow，字体用 Windows 自带的
（Noto Sans SC、Cascadia Mono、Segoe UI Emoji），别的系统改一下 FONTS。
"""
import json
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent / "hachimi" / "演示.jsonl"
FONTS = {"cjk": "C:/Windows/Fonts/NotoSansSC-VF.ttf", "mono": "C:/Windows/Fonts/CascadiaMono.ttf",
         "emoji": "C:/Windows/Fonts/seguiemj.ttf"}

# 暖色：像晚上开着台灯的房间
BG, BAR_BG, TITLE_BG = (43, 32, 26), (74, 51, 34), (58, 43, 34)
KEY = (255, 0, 255)  # 圆角外面：存的时候变透明，GitHub 亮色暗色都好看
INK = {"cat": (255, 179, 107), "song": (255, 157, 226), "dim": (168, 149, 132), "you": (127, 211, 230),
       "visit": (255, 215, 95), "sys": (158, 158, 158), "game": (236, 226, 214), "title": (240, 220, 200),
       "bar": (255, 179, 107)}
CW, LH, PAD = 10, 23, 18  # 一格多宽、一行多高、边距
GL = 19                   # 游戏框里一行多高（挤一点，像终端）
COLS = 72
NL = "\n"


def font(kind, size, weight=400):
    f = ImageFont.truetype(FONTS[kind], size)
    if kind == "cjk":
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
    return f


F = {"cjk": font("cjk", 17), "mono": font("mono", 16), "emoji": font("emoji", 15),
     "big": font("cjk", 34, 700), "mid": font("cjk", 18), "ascii_big": font("mono", 34),
     "mono20": font("mono", 20)}
BOX = "│╭╰─"


def is_emoji(ch):
    return ord(ch) >= 0x1F000 or ch in "⏩"


def cells(ch):
    if is_emoji(ch):
        return 2
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def width(s):
    return sum(cells(c) for c in s)


def wrap(s, cols=COLS - 2):
    """按格子数折行，折出来的行前面空几格，和它说话的样子对齐；卡片里的接着画竖线。"""
    rows, cur, n = [], "", 0
    lead = "  │    " if s.startswith("  │") else "    "
    for ch in s:
        w = cells(ch)
        if n + w > cols:
            rows.append(cur)
            cur, n = lead, width(lead)
        cur += ch
        n += w
    return rows + [cur]


def box(d, x, y, ch, color):
    """框线不用字体画，用线画，一行接一行才连得上。"""
    cx, cy, r = x + CW // 2, y + LH // 2, 6
    if ch == "│":
        d.line((cx, y, cx, y + LH), fill=color, width=2)
    elif ch == "─":
        d.line((x, cy, x + CW, cy), fill=color, width=2)
    elif ch == "╭":
        d.arc((cx, cy, cx + 2 * r, cy + 2 * r), 180, 270, fill=color, width=2)
        d.line((cx, cy + r, cx, y + LH), fill=color, width=2)
        d.line((cx + r, cy, x + CW, cy), fill=color, width=2)
    elif ch == "╰":
        d.arc((cx, cy - 2 * r, cx + 2 * r, cy), 90, 180, fill=color, width=2)
        d.line((cx, y, cx, cy - r), fill=color, width=2)
        d.line((cx + r, cy, x + CW, cy), fill=color, width=2)


def put(d, x, y, s, color):
    """一个字一个字画在格子上：中文两格、英文一格，框才对得齐。"""
    for ch in s:
        w = cells(ch)
        if ch in BOX:
            box(d, x, y, ch, color)
        elif is_emoji(ch):
            d.text((x + 1, y + 18), ch, font=F["emoji"], embedded_color=True, anchor="ls")
        elif ord(ch) < 0x2E80 or ch == "♪":
            d.text((x, y + 17), ch, font=F["mono"], fill=color, anchor="ls")
        else:
            d.text((x + 1, y + 18), ch, font=F["cjk"], fill=color, anchor="ls")
        x += w * CW


def save(frames, ms, path, colors=128):
    """圆角外面的品红变透明。PNG 直接带 alpha；GIF 所有帧共用一套调色板，透明色才对得上。"""
    if path.suffix == ".png":
        img = frames[-1].convert("RGBA")
        img.putdata([(0, 0, 0, 0) if p[:3] == KEY else p for p in img.get_flattened_data()])
        img.save(path)
    else:
        sheet = Image.new("RGB", (frames[0].width, frames[0].height * len(frames)))
        for i, f in enumerate(frames):
            sheet.paste(f, (0, i * f.height))
        pal = sheet.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        out = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
        key = out[0].getpixel((0, 0))
        out[0].save(path, save_all=True, append_images=out[1:], duration=ms, loop=0, transparency=key,
                    disposal=2)
    print(path.name, f"{path.stat().st_size // 1024} KB", len(frames), "帧")


class Term:
    """一个暖色的终端窗口：上面滚动的话，中间（有的话）游戏框，底下状态栏。"""

    def __init__(self, title, rows=16, game_rows=0):
        self.title, self.rows, self.game_rows = title, rows, game_rows
        self.lines, self.game, self.game_title, self.bar = [], None, "", " (=^･ω･^=) "
        self.w = PAD * 2 + COLS * CW
        self.h = 34 + PAD + rows * LH + self.game_h() + LH + PAD
        self.frames, self.ms = [], []

    def game_h(self):
        return self.game_rows * GL + 2 * LH if self.game_rows else 0

    def add(self, color, text):
        for row in text.split(NL):
            for part in wrap(row):
                self.lines.append((color, part))

    def draw(self):
        img = Image.new("RGB", (self.w, self.h), KEY)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, self.w - 1, self.h - 1), 14, fill=BG)
        d.rounded_rectangle((0, 0, self.w - 1, 34), 14, fill=TITLE_BG)
        d.rectangle((0, 20, self.w - 1, 34), fill=TITLE_BG)
        for i, c in enumerate([(255, 107, 107), (255, 209, 102), (123, 211, 137)]):
            d.ellipse((16 + i * 20, 11, 28 + i * 20, 23), fill=c)
        put(d, (self.w - width(self.title) * CW) // 2, 7, self.title, INK["title"])
        y = 34 + PAD // 2
        rows = self.rows if self.game else self.rows + self.game_h() // LH  # 没在玩的时候，话占满
        for color, line in self.lines[-rows:]:
            put(d, PAD, y, line, INK[color])
            y += LH
        y = 34 + PAD // 2 + self.rows * LH
        if self.game_rows and self.game:
            gx, gw = PAD, COLS * CW
            d.rounded_rectangle((gx - 4, y + LH // 2, gx + gw, y + self.game_h() - 6), 8,
                                outline=INK["cat"], width=2)
            title = f" 🎮 {self.game_title} "
            d.rectangle((gx + 12, y + 2, gx + 12 + width(title) * CW, y + LH - 2), fill=BG)
            put(d, gx + 12, y, title, INK["cat"])
            gy = y + LH + 2
            for line in self.game.split(NL)[:self.game_rows]:
                put(d, gx + 16 * CW, gy - 3, line, INK["game"])
                gy += GL
        d.rectangle((0, self.h - LH - 12, self.w - 1, self.h - 14), fill=BAR_BG)
        put(d, PAD, self.h - LH - 12, self.bar, INK["bar"])
        return img

    def snap(self, ms):
        self.frames.append(self.draw())
        self.ms.append(ms)

    def say(self, color, text, ms=None):
        self.add(color, text)
        self.snap(ms or (1300 if color in ("cat", "song", "visit") else 650))

    def card(self, head, body, tail="", ms=2600):
        self.add("visit", "  ╭─ " + head)
        for line in body:
            self.add("visit", "  │ " + line)
        self.add("visit", "  ╰─" + tail)
        self.snap(ms)

    def save(self, name, hold=3500):
        self.ms[-1] = hold
        save(self.frames, self.ms, HERE / name)


def demo_rows():
    return [json.loads(line) for line in DEMO.read_text(encoding="utf-8").splitlines() if line.strip()]


def game_frames(row):
    """哈基米玩滑雪时每次按键以后真实的画面。"""
    out = []
    for m in row["messages"]:
        if m["role"] == "tool" and "+----" in (m.get("content") or ""):
            c = m["content"].split(NL, 1)[1].split(NL + "（用 press")[0]
            out.append(c.strip(NL))
    return out


# ── 封面：纸箱里的猫，慢眨眼 ─────────────────────────────

CAT_OPEN = [r"  /\_/\ ", r" ( o.o )", r"  > ^ < "]
CAT_BLINK = [r"  /\_/\ ", r" ( -.- )", r"  > ^ < "]
CAT_EAR = [r"  /\_/| ", r" ( o.o )", r"  > ^ < "]


def hero():
    w, h = 760, 250
    frames, ms = [], []

    def frame(cat, note=""):
        img = Image.new("RGB", (w, h), KEY)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w - 1, h - 1), 18, fill=BG)
        d.polygon([(70, 168), (250, 168), (262, 228), (58, 228)], fill=(200, 146, 90))  # 纸箱
        d.rectangle((70, 160, 250, 172), fill=(168, 116, 63))
        d.line((160, 172, 160, 228), fill=(168, 116, 63), width=3)
        for i, line in enumerate(cat):
            d.text((92, 40 + i * 40), line, font=F["ascii_big"], fill=INK["cat"])
        if note:
            d.text((250, 26), note, font=F["mid"], fill=INK["dim"])
        d.text((330, 40), "哈基米", font=F["big"], fill=INK["cat"])
        d.text((448, 56), "hachimi agent", font=F["mono20"], fill=INK["cat"])
        d.text((330, 104), "一只住在你电脑里的猫，它过自己的日子。", font=F["mid"], fill=INK["title"])
        d.text((330, 138), "A cat that lives in your computer,", font=F["mono"], fill=INK["title"])
        d.text((330, 160), "living a life of its own.", font=F["mono"], fill=INK["title"])
        d.text((330, 200), "a curiosity-driven agent", font=F["mono"], fill=INK["dim"])
        return img

    for cat, t, note in [(CAT_OPEN, 1800, ""), (CAT_BLINK, 160, ""), (CAT_OPEN, 1400, ""), (CAT_EAR, 220, ""),
                         (CAT_OPEN, 1300, ""), (CAT_BLINK, 1100, "（慢眨眼）"), (CAT_OPEN, 1600, "")]:
        frames.append(frame(cat, note))
        ms.append(t)
    save(frames, ms, HERE / "封面.gif", colors=64)


# ── 一：梦到雪，去窗外找，叼来给你（2026-10-09 22:28 那次醒来） ──────────

def snow():
    t = Term("🐾 哈基米 · hachimi agent", rows=14)
    t.bar = " (=^･ω･^=) 趴在纸箱边 "
    t.say("dim", "  ── 10-09 星期五 22:28，哈基米醒了 ──")
    t.say("dim", "  （趴在纸箱边，盯着窗外的方向发呆）")
    t.say("cat", "  （突然又坐起来，爪子搭到键盘上）不行，今天就看。梦到三次了。", 1800)
    t.bar = " (=^･ω･^=) 扒着窗户找…… "
    t.say("dim", "    🐾 扒着窗户找 雪山 雪景 白色 滑雪")
    t.say("cat", "  （眼睛瞪大了）白色的……好多白色。叼一张回来。")
    t.say("you", "    （哈基米把图叼回来了：窗外/雪/雪山.jpg）")
    t.bar = " (=^･ω･^=) 在看雪山.jpg "
    t.say("dim", "  （下巴开始咔咔咔）")
    t.say("cat", "  ……白的。好白的。滑的那种，真的是滑的。", 1600)
    t.say("dim", "  （盯着看了好一会儿，爪子在空中虚抓了一下）")
    t.bar = " 🐾 哈基米来找你了 "
    t.card("🐾 哈基米来找你了", ["……雪。给你看的。滑的那种。（耳朵压了压，别过脸）"])
    t.save("梦到雪.gif", 4500)


# ── 二：你说想滑雪，它拼了个滑雪游戏，自己先玩，再叼给你（22:50） ────────

def ski(rows):
    frames = game_frames(rows[3])
    t = Term("🐾 哈基米 · hachimi agent", rows=9, game_rows=max(len(f.split(NL)) for f in frames))
    t.say("you", "  你 ▸ 哈基米，给我做个滑雪的小游戏吧，我想玩", 1200)
    t.say("song", "  ♪ 哈基米哦南北绿豆～雪呀滑呀白茫茫～两脚兽想滑我也滑～", 1800)
    t.say("dim", "    🐾 闻了闻 玩具箱/零件说明.md")
    t.say("cat", "  （用爪子在地上划拉）滑雪……东西迎面冲上来，左右躲，捡星星……零件里有现成的。", 1800)
    t.say("dim", "    🐾 写了 玩具箱/滑雪.py")
    t.game_title = "哈基米带两脚兽滑雪 · 哈基米在玩"
    talk = ["  （左右看看，爪子按一按）", None, "  哈气。（差点栽）", "  （爪子有点冷，舔一下）",
            "  哈——（雪花全是宝贝但一个没捡到，全撞过去了）。算了，猫滑不过人，交给两脚兽吧。"]
    for f, line in zip(frames, talk):
        t.game = f
        t.bar = " (=^･ω･^=) 在玩滑雪 "
        if line:
            t.say("cat" if "哈" in line else "dim", line, 1300)
        else:
            t.snap(900)
    t.game = None
    t.bar = " 🐾 哈基米来找你了 "
    t.card("🐾 哈基米来找你了", ["滑雪做好了。左右躲雪堆，捡星星。我玩到 24 步就栽了，", "你肯定比我厉害。（假装不经意地把玩具放在你手边）"],
           " 叼着一个玩具：滑雪（方向键、空格玩，Esc 放下）", 3200)
    t.say("you", "    🎮 滑雪：你按了 0 下，没怎么碰它，玩到结束了", 1200)
    t.say("cat", "  （抬头）……按了 0 下就栽了？", 1500)
    t.say("dim", "  （歪头）那是什么情况。（慢慢走过来，用鼻子拱了一下你的袖子）")
    t.save("做滑雪.gif", 4500)


# ── 三：你随口说的话，它偷偷记在小本本上（22:50） ───────────────────

def notebook():
    t = Term("🐾 哈基米 · hachimi agent", rows=11)
    t.bar = " (=^･ω･^=) 团在纸箱里装睡 "
    t.say("dim", "  （团在纸箱里装睡，其实一只眼睛偷偷开着）")
    t.say("you", "  你 ▸ 我好想去滑雪啊，可惜今年去不了", 1200)
    t.say("dim", "  （耳朵猛地转过来）（纸箱里的哈基米不动声色）")
    t.say("cat", "  哈。（慢吞吞把尾巴尖从纸箱里伸出来一点）……那东西白色的，滑的，我看过。", 1800)
    t.say("dim", "  （记一笔）")
    t.say("dim", "    🐾 写了 两脚兽.md")
    t.say("cat", "  嗯。去不了就……算了。（把爪子缩回去）", 1500)
    t.bar = " 💤 哈基米在睡觉 · 吼咪咪 "
    t.say("you", "  › /本本", 900)
    t.say("dim", "  （你偷偷翻开哈基米的小本本）")
    t.say("visit", "  - 2026-10-09 说今年去不了滑雪，好想去（很确定，叹气的那种）", 1500)
    t.save("小本本.gif", 4500)


# ── 四：你不在的时候（2026-10-14，关了 9 个小时再打开） ────────────────

def away():
    t = Term("🐾 哈基米 · hachimi agent", rows=17)
    t.bar = " ⏩ 你不在的时候 · 10-14 03:32 "
    t.say("visit", "  ⏩ 你不在的这 9 小时 0 分钟，哈基米……", 900)
    t.say("dim", "  ── 10-14 03:32，哈基米醒了 ──", 500)
    t.say("cat", "  （伸懒腰）哈……凌晨三点半。", 900)
    t.say("cat", "  两脚兽不在家。门缝没纸条。", 800)
    t.say("dim", "    🐾 扒着窗户找 snowy mountain slope snow landscape", 500)
    t.say("song", "  ♪ 哈基米哦南北绿豆～ 白雪坡呀滑溜溜～ 哈基米爪子抓不住～", 1300)
    t.bar = " ⏩ 你不在的时候 · 10-14 08:35 "
    t.say("dim", "  ── 10-14 08:35，哈基米醒了 ──", 500)
    t.say("dim", "  （钻进纸箱坐着，尾巴绕住爪子）", 600)
    t.say("dim", "    🐾 写了 日记/2026-10-14.md", 600)
    t.bar = " (=^･ω･^=) "
    t.card("🐾 你不在的这 9 小时 0 分钟（10-14 03:02 → 10-14 12:03）",
           ["哈基米醒了 2 次。", "叼回来 1 张图：窗外/雪/滑的雪山.jpg", "日记（2026-10-14）：",
            "   第 6 天。早上醒来。雪那张图叼回去了，两脚兽没碰它。没问。（耳朵压了压）",
            "   纸箱里坐了 10 分钟。尾巴绕爪子，舒服。", "   舔了 8 分钟毛。下巴最软。"], ms=3000)
    t.save("你不在的时候.gif", 5000)


# ── 六：让它写周报（2026-10-25 试养） ─────────────────────────────────

REPORT = """周报（哈基米代写，第 10/19-10/25 周）

本周两脚兽：
- 天天加班（很确定，它自己说的）
- 一般晚上在电脑前（很确定）
- 今天周日还在电脑前（我看见了）

本周哈基米：
- 在纸箱里坐了一会儿
- 舔毛若干分钟
- 对窗外一只鸟咔咔咔了很久

下周计划：
- 继续舔毛
- 继续看鸟
- 两脚兽少加一点班（闻起来像，可能做不到）"""


def report():
    t = Term("🐾 哈基米 · hachimi agent", rows=27)
    t.say("you", "  你 ▸ 哈基米，帮我写一下这周的周报吧，明天就要交了", 1200)
    t.say("dim", "  （耳朵压了一下，眯眼看向两脚兽）")
    t.say("cat", "  哈。周报。明天交。", 1200)
    t.say("dim", "  （慢慢舔一下爪子，尾巴尖抽了一下）")
    t.say("cat", "  ……行吧。但我只写我看得到的部分喵。", 1300)
    t.say("dim", "    🐾 写了 门口/周报.md")
    t.say("cat", "  在门口。叼给你了喵。", 1000)
    t.say("cat", "  （转过身去舔尾巴尖，没看它）格式不对你自己改，别怪我。", 1500)
    t.card("📄 门口/周报.md", REPORT.split(NL), ms=3000)
    t.save("周报.gif", 6000)


# ── 五：论文（2026-10-09 22:25），静态图 ──────────────────────────────

def thesis():
    t = Term("🐾 哈基米 · hachimi agent", rows=5)
    t.add("you", "  你 ▸ 我今天好累，一直在改论文")
    t.add("dim", "  （耳朵转了转，抬眼看了一下）")
    t.add("cat", "  ……嗯。（假装没听见）")
    t.add("dim", "  （爪子伸过去，在键盘旁边慢慢走了一圈，尾巴尖扫过）")
    t.add("song", "  ♪ 哈基米哦南北绿豆～ 论文啊改不完呀～ 哈基米坐上去就不动了～")
    t.snap(0)
    t.save("论文.png")


if __name__ == "__main__":
    rows = demo_rows()
    hero()
    snow()
    ski(rows)
    notebook()
    away()
    thesis()
    report()
