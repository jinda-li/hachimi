"""玩具箱：哈基米自己写的命令行小游戏，怎么检查、怎么跑。

游戏是 玩具箱/ 里的一个 .py 文件，写四个函数：
    start()            -> 状态（随便什么，一般是 dict）
    step(状态, 键)      -> 新状态。键是 "上" "下" "左" "右" "空格"，或者 ""（这一下没按，游戏自己走一步）
    draw(状态)          -> 字符串，就是屏幕。最多 20 行、40 个字宽
    over(状态)          -> True 就是游戏结束了
只能 import random、math，和现成的玩具零件（零件.py）：
    import 零件
    start, step, draw, over = 零件.躲避(标题="滑雪", 主角="A", 障碍="^", 宝贝="*")

代码是模型写的，所以跑在单独的进程里：先查语法树，不许碰文件、网络、双下划线；
每次调用超过 3 秒就当它卡死了，掐掉。
"""
import ast
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent

SPEC = ("玩具箱/ 里的 .py 游戏要写四个函数：start() 返回状态；step(状态, 键) 返回新状态，"
        "键是 上/下/左/右/空格，或者空字符串（没按，游戏自己走一步）；draw(状态) 返回屏幕字符串，"
        "最多 20 行 40 字宽；over(状态) 返回是否结束。只能 import random 和 math。"
        "也可以用现成的玩具零件，几行就拼一个：import 零件 然后 "
        "start, step, draw, over = 零件.躲避(标题=\"滑雪\", 主角=\"A\", 障碍=\"^\", 宝贝=\"*\")。"
        "零件有：躲避（东西迎面冲来，左右躲）、接住（东西往下掉，左右接）、扑（追乱跑的猎物，空格扑）、"
        "打地鼠（洞里冒东西，挪过去空格拍）、跳（往前跑，空格跳过障碍）。"
        "每个零件能改哪些参数，read 零件说明.md 就知道。")

ALLOWED_IMPORTS = {"random", "math", "零件"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals",
                "vars", "getattr", "setattr", "delattr", "breakpoint", "help", "exit", "quit",
                "memoryview", "__builtins__"}
NEEDED = ("start", "step", "draw", "over")


def check(src: str):
    """不合格返回一句猫能看懂的话，合格返回 None。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return f"玩具写坏了（第 {e.lineno} 行：{e.msg}）"
    for n in ast.walk(tree):
        mods = None
        if isinstance(n, ast.Import):
            mods = [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods = [(n.module or "").split(".")[0]]
        if mods and any(m not in ALLOWED_IMPORTS for m in mods):
            return f"玩具里只能用 random、math 和 零件，不能用 {', '.join(mods)}"
        if isinstance(n, ast.Name) and n.id in BANNED_NAMES:
            return f"玩具里不能用 {n.id}"
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            return "玩具里不能碰带双下划线的东西"
    defined = set()  # 自己写的 def，或者从零件拿来的 start, step, draw, over = ...
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            defined.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                for e in (t.elts if isinstance(t, ast.Tuple) else [t]):
                    if isinstance(e, ast.Name):
                        defined.add(e.id)
    missing = [f for f in NEEDED if f not in defined]
    if missing:
        return f"玩具还缺函数：{', '.join(missing)}"
    return None


# 子进程里跑的东西：载入游戏，一行 JSON 进，一行 JSON 出
CHILD = r'''
import io, json, sys, traceback, importlib.util
sys.stdin.reconfigure(encoding="utf-8"); real = sys.stdout; real.reconfigure(encoding="utf-8")
sys.path.insert(0, sys.argv[2])  # 零件.py 在这里
spec = importlib.util.spec_from_file_location("toy", sys.argv[1])
m = importlib.util.module_from_spec(spec)
state = None
def reply(d): real.write(json.dumps(d, ensure_ascii=False) + "\n"); real.flush()
try:
    sys.stdout = io.StringIO(); spec.loader.exec_module(m)
except Exception as e:
    reply({"error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc(limit=2)}); sys.exit()
for line in sys.stdin:
    req = json.loads(line)
    try:
        sys.stdout = io.StringIO()
        state = m.start() if req["op"] == "start" else m.step(state, req.get("key", ""))
        reply({"frame": str(m.draw(state)), "over": bool(m.over(state))})
    except Exception as e:
        reply({"error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc(limit=2)})
'''


def clip(frame: str):
    return "\n".join(line[:40] for line in frame.splitlines()[:20])


class Toy:
    def __init__(self, path: Path):
        self.path = path
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.p = subprocess.Popen([sys.executable, "-I", "-c", CHILD, str(path), str(HERE)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, cwd=path.parent,
                                  text=True, encoding="utf-8", creationflags=flags)
        self.lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def call(self, op, key=""):
        try:
            self.p.stdin.write(json.dumps({"op": op, "key": key}, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
            line = self.lines.get(timeout=3)
        except (queue.Empty, OSError):
            self.close()
            return {"error": "玩具卡住了，不动了（是不是有一个停不下来的循环？）"}
        if line is None:
            return {"error": "玩具自己碎掉了"}
        r = json.loads(line)
        if "frame" in r:
            r["frame"] = clip(r["frame"])
        return r

    def close(self):
        try:
            self.p.kill()
        except OSError:
            pass


KEYS = {"上": "上", "↑": "上", "下": "下", "↓": "下", "左": "左", "←": "左", "右": "右", "→": "右",
        "␣": "空格", "等": "", "·": "", ".": ""}


def parse_keys(s: str, limit=30):
    """「右右右上」「→ → ↑」「空格 等 等」都认。"""
    s = s.replace("空格", "␣")
    for w, k in (("up", "↑"), ("down", "↓"), ("left", "←"), ("right", "→"), ("space", "␣")):
        s = s.replace(w, k)
    return [KEYS[ch] for ch in s if ch in KEYS][:limit]
