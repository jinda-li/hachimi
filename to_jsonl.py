"""把 samples/*.md 转成训练用的 JSONL。

用法：python to_jsonl.py [samples目录] [sft输出] [dpo目录] [dpo输出]
默认：samples out/sft.jsonl dpo out/dpo.jsonl
格式说明见 数据格式.md。顺便按 宪法.md 第 7 节检查它说的话里有没有不该有的东西。
"""
import json
import re
import sys
from pathlib import Path

SYSTEM = "你住在这台电脑里。"

def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {},
                       "required": required or []}}}

S = {"type": "string"}
N = {"type": "number"}
TOOLS = [
    _fn("look", "截一张屏幕。给 save 就把截图存进房间", {"save": S}),
    _fn("click", "点屏幕上的位置", {"x": N, "y": N, "double": {"type": "boolean"}}, ["x", "y"]),
    _fn("drag", "按住鼠标沿着这些点拖", {"points": {"type": "array", "items": {"type": "array", "items": N}}}, ["points"]),
    _fn("press", "按键。hold 是按住几秒", {"keys": S, "hold": N}, ["keys"]),
    _fn("type", "打字", {"text": S}, ["text"]),
    _fn("open_game", "打开游戏架上的游戏", {"name": S}, ["name"]),
    _fn("search", "网页搜索", {"query": S}, ["query"]),
    _fn("read_page", "读一个网页", {"url": S}, ["url"]),
    _fn("ls", "看房间里有什么", {"path": S}),
    _fn("read", "读房间里的文件", {"path": S}, ["path"]),
    _fn("write", "写文件", {"path": S, "text": S}, ["path", "text"]),
    _fn("append", "在文件末尾加内容", {"path": S, "text": S}, ["path", "text"]),
    _fn("edit", "把文件里的一段换成另一段", {"path": S, "old": S, "new": S}, ["path", "old", "new"]),
    _fn("play", "打开玩具箱里的网页玩具，全屏。打开以后用 look、click、press 玩", {"path": S}, ["path"]),
    _fn("move", "挪文件", {"src": S, "dst": S}, ["src", "dst"]),
    _fn("delete", "扔掉文件", {"path": S}, ["path"]),
    _fn("sleep", "睡觉", {"minutes": N}, ["minutes"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}

HEADER = re.compile(r"^=== id: (\S+) \| 类别: (.+?) ===\s*$", re.M)
TAG = re.compile(r"^@(env|me|human|result|call)\b(.*)$")


def parse_sample(sid, cat, body):
    msgs = [{"role": "system", "content": SYSTEM}]
    blocks = []  # (tag, extra, text)
    cur = None
    for line in body.splitlines():
        m = TAG.match(line)
        if m:
            cur = [m.group(1), m.group(2).strip(), []]
            blocks.append(cur)
        elif cur is not None:
            cur[2].append(line)
    n_call = 0
    pending = []  # call ids waiting for results
    for tag, extra, lines in blocks:
        text = "\n".join(lines).strip()
        if tag == "env":
            msgs.append({"role": "user", "content": text})
        elif tag == "human":
            msgs.append({"role": "user", "content": "【有人说】" + text})
        elif tag == "me":
            msgs.append({"role": "assistant", "content": text})
        elif tag == "call":
            name, _, arg = extra.partition(" ")
            if name not in TOOL_NAMES:
                raise ValueError(f"{sid}: 未知工具 {name}")
            args = json.loads(arg) if arg.strip() else {}
            # 写代码这种长内容可以不塞进一行 JSON：@call 下面的几行就是 text
            body = "\n".join(lines).strip("\n")
            if body.strip():
                if "text" in args:
                    raise ValueError(f"{sid}: {name} 的 text 写了两遍")
                args["text"] = body + "\n"
            n_call += 1
            cid = f"{sid}-c{n_call}"
            if not msgs or msgs[-1]["role"] != "assistant":
                msgs.append({"role": "assistant", "content": ""})
            msgs[-1].setdefault("tool_calls", []).append({
                "id": cid, "type": "function",
                # Qwen3.5 的模板只认 dict 形式的 arguments，传字符串参数会被悄悄丢掉
                "function": {"name": name, "arguments": args}})
            if name != "sleep":
                pending.append(cid)
        elif tag == "result":
            if not pending:
                raise ValueError(f"{sid}: @result 前面没有 @call")
            msgs.append({"role": "tool", "tool_call_id": pending.pop(0), "content": text})
    if pending:
        raise ValueError(f"{sid}: 有 @call 没有 @result")
    return {"id": sid, "category": cat, "tools": TOOLS, "messages": msgs}


# 宪法第 7、8 节：助手腔、人设说明书、宝宝腔、刻薄
BANNED = ["作为一个AI", "作为一个 AI", "作为AI", "作为一只", "作为哈基米", "人家",
          "乐意", "很高兴为你", "如有其他问题", "总之", "总而言之", "抱歉", "对不起",
          "猫猫的天性", "我是不会", "吃饭饭", "小爪爪", "宝宝", "嘻嘻",
          "笑死", "没文化", "破防", "伺候", "没手吗", "这水平", "爱猫TV", "耄耋"]
MD_LINE = re.compile(r"^\s*(#{1,6} |[-*] |\d+\. |\*\*)")
# 颜文字：括号里带这些符号，或者 ฅ 开头
KAOMOJI = re.compile(r"[(（][^()（）\n]{0,6}[ω▽Дↀ･•ㅅﻌ￣①ⓛ∀ε][^()（）\n]{0,6}[)）]|ฅ[^ฅ\n]{1,8}ฅ")
MEME = ["哈基米", "曼波", "南北绿豆"]


def lint(row):
    """只查 assistant 说的话和它写进文件的内容。"""
    probs = []
    for m in row["messages"]:
        if m["role"] != "assistant":
            continue
        texts = [m.get("content") or ""]
        for tc in m.get("tool_calls", []):
            a = tc["function"]["arguments"]
            # 玩具的代码不查（代码里本来就有 * 和 1. 这种东西），只查它写给自己的话
            if not str(a.get("path", "")).endswith((".html", ".js", ".css")):
                texts += [v for k, v in a.items() if k in ("text", "new") and isinstance(v, str)]
        for t in texts:
            for b in BANNED:
                if b in t:
                    probs.append(f"出现了「{b}」")
            # 烂梗轰炸（R3）：一段话里颜文字、喵、梗词太密
            if len(KAOMOJI.findall(t)) > 2:
                probs.append(f"颜文字 {len(KAOMOJI.findall(t))} 个，太多")
            sents = max(1, len(re.findall(r"[。！？!?～\n]", t)))
            if t.count("喵") > 3 and t.count("喵") / sents > 0.4:
                probs.append(f"喵太密（{t.count('喵')} 个 / {sents} 句）")
            n_meme = sum(t.count(w) for w in MEME)
            if n_meme > 4 and n_meme / sents > 0.3:  # 在聊自己名字的时候可以多
                probs.append(f"哈基米/曼波/南北绿豆 一共 {n_meme} 次，像在刷梗")
            for line in t.splitlines():
                if MD_LINE.match(line):
                    probs.append(f"像列表/标题的行：{line.strip()[:30]}")
    return probs


DPO_HEADER = re.compile(r"^=== dpo: (\S+) ===\s*$", re.M)
DPO_TAG = re.compile(r"^@(env|human|chosen|R[1-4])\s*$")


def parse_dpo(gid, body):
    prompt = [{"role": "system", "content": SYSTEM}]
    cur, blocks = None, []
    for line in body.splitlines():
        m = DPO_TAG.match(line)
        if m:
            cur = [m.group(1), []]
            blocks.append(cur)
        elif cur is not None:
            cur[1].append(line)
    chosen, rejected = None, {}
    for tag, lines in blocks:
        text = "\n".join(lines).strip()
        if tag == "env":
            prompt.append({"role": "user", "content": text})
        elif tag == "human":
            prompt.append({"role": "user", "content": "【有人说】" + text})
        elif tag == "chosen":
            chosen = text
        else:
            rejected[tag] = text
    if not chosen or not rejected:
        raise ValueError(f"dpo {gid}: 缺 chosen 或 R")
    return [{"id": f"{gid}-{r}", "rejected_type": r, "prompt": prompt,
             "chosen": [{"role": "assistant", "content": chosen}],
             "rejected": [{"role": "assistant", "content": t}]}
            for r, t in rejected.items()]


def main_dpo(src, out):
    rows = []
    for f in sorted(Path(src).glob("*.md")):
        txt = f.read_text(encoding="utf-8")
        heads = list(DPO_HEADER.finditer(txt))
        for i, h in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(txt)
            rows += parse_dpo(h.group(1), txt[h.end():end])
    with open(out, "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 反过来用：被拒绝的那边应该被 lint 抓到，chosen 不应该
    for r in rows:
        if lint({"messages": r["chosen"]}):
            print(f"  ⚠ {r['id']}: chosen 有问题 {lint({'messages': r['chosen']})}")
        if r["rejected_type"] != "R1" and not lint({"messages": r["rejected"]}):
            print(f"  · {r['id']}: lint 没抓到这条反例（只能靠人读）")
    print(f"DPO {len(rows)} 对 → {out}")


def main(src, out):
    rows = []
    for f in sorted(Path(src).glob("*.md")):
        txt = f.read_text(encoding="utf-8")
        heads = list(HEADER.finditer(txt))
        for i, h in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(txt)
            rows.append(parse_sample(h.group(1), h.group(2), txt[h.end():end]))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    bad = 0
    for r in rows:
        for p in lint(r):
            print(f"  ⚠ {r['id']}: {p}")
            bad += 1
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print(f"SFT {len(rows)} 条 → {out}（检查出 {bad} 处问题）")
    for c, n in cats.items():
        print(f"  {c}: {n}")


if __name__ == "__main__":
    a = sys.argv[1:] + [None] * 4
    main(a[0] or "samples", a[1] or "out/sft.jsonl")
    if Path(a[2] or "dpo").exists():
        main_dpo(a[2] or "dpo", a[3] or "out/dpo.jsonl")
