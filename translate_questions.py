"""
translate_questions.py — 把评估题从中文翻译成英文（阶段 4d 实验 2）。

用法（项目目录、已激活 .venv）：
    python translate_questions.py
        开发集 eval/retrieval_questions.json → eval/retrieval_questions_en.json
    python translate_questions.py --input eval/retrieval_holdout.json --output eval/retrieval_holdout_en.json
        留出集（只在最终评估时用）

会发给 DeepSeek API 的内容：config.TRANSLATE_PROMPT + 每道题的中文问题，一题一次请求。不发送论文内容。
译文写成文件后要人工逐条检查；之后评估都用这份固定译文，不再重复调用 API。
输出文件已存在时拒绝覆盖（加 --force 才覆盖），避免检查过的译文被悄悄换掉。
代码检查：译文里出现原题没有的数字时给出警告，防止模型把答案带进检索词。
"""

import argparse
import json
import re
import sys
from pathlib import Path

from openai import OpenAIError

import config
from llm_client import build_client, describe_api_error, load_api_key

NUMBER = re.compile(r"\d+(?:\.\d+)?")   # 整数或小数，如 84、0.991


def new_numbers(source: str, translation: str) -> list:
    """译文里出现、原题里没有的数字，按在译文里出现的顺序，每个只报一次。"""
    have = set(NUMBER.findall(source))          # 原题里的所有数字
    extra = []
    for n in NUMBER.findall(translation):       # 按顺序看译文里的每个数字
        if n not in have and n not in extra:    # 原题没有、而且还没记过
            extra.append(n)
    return extra


def translate_one(client, question: str) -> str:
    """一道题一次请求。返回单行译文。"""
    response = client.chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "system", "content": config.TRANSLATE_PROMPT},
                  {"role": "user", "content": question}],
        max_tokens=config.TRANSLATE_MAX_TOKENS,
        temperature=config.TRANSLATE_TEMPERATURE,
        extra_body={"thinking": {"type": config.THINKING}},
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise ValueError(f"译文被截断（超过 {config.TRANSLATE_MAX_TOKENS} token）：{question}")
    return " ".join((choice.message.content or "").split())   # 合并换行和多余空格


def translate_all(questions: list, translate) -> list:
    """逐题翻译。返回新列表：question 换成译文，原文存进 source_question，其余字段不变。
    translate 是"中文 → 英文"的函数，由调用方传入；测试时可以换成假的，不用调用 API。"""
    out = []
    for q in questions:
        english = translate(q["question"]).strip()
        if not english:
            raise ValueError(f"{q['id']} 的译文为空")
        out.append({**q, "question": english, "source_question": q["question"]})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="把评估题翻译成英文（会把中文问题发给模型 API）")
    parser.add_argument("--input", default="eval/retrieval_questions.json")
    parser.add_argument("--output", default="eval/retrieval_questions_en.json")
    parser.add_argument("--force", action="store_true", help="输出文件已存在时也覆盖")
    args = parser.parse_args()

    out = Path(args.output)
    if out.exists() and not args.force:
        sys.exit(f"{out} 已存在。检查过的译文应保持固定；确实要重新翻译请加 --force")
    questions = json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(f"把 {len(questions)} 道题的中文问题发给 {config.MODEL} 翻译（不发送论文内容）……")

    client = build_client(load_api_key())
    try:
        translated = translate_all(questions, lambda text: translate_one(client, text))
    except OpenAIError as e:
        sys.exit(describe_api_error(e))
    except ValueError as e:
        sys.exit(f"错误：{e}")

    warned = 0
    for q in translated:
        extra = new_numbers(q["source_question"], q["question"])
        warned += bool(extra)
        flag = f"   ⚠ 原题没有的数字：{', '.join(extra)}" if extra else ""
        print(f"{q['id']}  {q['source_question']}\n      → {q['question']}{flag}")

    lines = ",\n".join("  " + json.dumps(q, ensure_ascii=False) for q in translated)
    out.write_text("[\n" + lines + "\n]\n", encoding="utf-8")
    print(f"\n已写入 {out}。{warned} 题有数字警告。请逐条检查：译文只能是原题的翻译，不能多出任何信息。")


if __name__ == "__main__":
    main()
