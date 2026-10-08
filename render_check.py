"""用 Qwen3.5 的聊天模板把 out/sft.jsonl 渲染一遍，看训练时模型真正看到的是什么。

用法：python render_check.py [sft.jsonl] [模板] [输出目录]
默认：out/sft.jsonl qwen35_chat_template.jinja out/rendered
每条样本写一个 .txt，并检查：
- 每个工具调用的参数都渲染出来了（arguments 是字符串时模板会悄悄丢掉）
- 没有 <think> 里有内容的地方（它没有隐藏的思考，宪法 2.7）
- 算损失的 assistant 片段能在渲染结果里按顺序找到
不需要 transformers，只要 jinja2。
"""
import json
import sys
from pathlib import Path

from jinja2.sandbox import ImmutableSandboxedEnvironment


def _raise(msg):
    raise ValueError(msg)


def make_template(path):
    # 和 transformers 的 apply_chat_template 用一样的环境设置
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                        extensions=["jinja2.ext.loopcontrols"])
    env.filters["tojson"] = lambda x, indent=None, **kw: json.dumps(
        x, ensure_ascii=False, indent=indent)
    env.globals["raise_exception"] = _raise
    return env.from_string(Path(path).read_text(encoding="utf-8"))


def check(row, text):
    probs = []
    for m in row["messages"]:
        for tc in m.get("tool_calls", []):
            f = tc["function"]
            if not isinstance(f["arguments"], dict):
                probs.append(f"{f['name']} 的 arguments 不是 dict")
                continue
            for k in f["arguments"]:
                if f"<parameter={k}>" not in text:
                    probs.append(f"{f['name']} 的参数 {k} 没渲染出来")
    pos = 0
    while (i := text.find("<think>", pos)) != -1:
        j = text.find("</think>", i)
        if text[i + len("<think>"):j].strip():
            probs.append("<think> 里有内容")
        pos = j + 1
    # assistant 的每段文字都应该在渲染结果里按顺序出现
    pos = 0
    for m in row["messages"]:
        if m["role"] == "assistant" and (m.get("content") or "").strip():
            i = text.find(m["content"].strip(), pos)
            if i == -1:
                probs.append(f"assistant 片段找不到：{m['content'].strip()[:20]}")
            else:
                pos = i
    return probs


def main(src, tpl, out):
    t = make_template(tpl)
    Path(out).mkdir(parents=True, exist_ok=True)
    bad = 0
    for line in Path(src).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        text = t.render(messages=row["messages"], tools=row["tools"],
                        add_generation_prompt=False)
        (Path(out) / f"{row['id']}.txt").write_text(text, encoding="utf-8")
        for p in check(row, text):
            print(f"  ⚠ {row['id']}: {p}")
            bad += 1
        n_ast = sum(m["role"] == "assistant" for m in row["messages"])
        print(f"{row['id']}: {len(text)} 字符，assistant {n_ast} 段")
    print(f"渲染完 → {out}（检查出 {bad} 处问题）")


if __name__ == "__main__":
    a = sys.argv[1:] + [None] * 3
    main(a[0] or "out/sft.jsonl", a[1] or "qwen35_chat_template.jinja",
         a[2] or "out/rendered")
