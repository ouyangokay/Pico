"""pico 运行调试工具。

用法：
    python debug_run.py                        # 自动找最新的 run 目录
    python debug_run.py .pico/runs/run_xxx     # 指定某个 run 目录
    python debug_run.py --json                 # 额外打印每条原始 JSON 事件
    python debug_run.py --events tool_executed # 只看指定事件类型

把一次 ask() 的 report 摘要 + trace 事件链按时间顺序格式化打印出来，
帮你快速定位"模型在哪一步出了问题"。
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

DEFAULT_RUNS_ROOT = ".pico/runs"

# 每个事件类型里，按这个顺序挑出来展示的字段
EVENT_FIELDS = {
    "run_started": ("task_id", "user_request"),
    "prompt_built": ("prompt_metadata", "duration_ms"),
    "model_requested": ("attempts", "tool_steps", "prompt_cache_key"),
    "model_parsed": ("kind", "completion_metadata", "duration_ms"),
    "tool_executed": ("name", "args", "tool_status", "tool_error_code", "result"),
    "checkpoint_created": ("checkpoint_id", "trigger"),
    "run_finished": ("status", "stop_reason", "final_answer", "run_duration_ms"),
}

# prompt_metadata 里值得看的子字段
PROMPT_META_KEYS = (
    "tool_count",
    "prefix_chars",
    "workspace_chars",
    "memory_chars",
    "history_chars",
    "resume_status",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cache_hit",
)


def clip(text, limit=400):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"...(共{len(text)}字)"


def find_latest_run(runs_root):
    dirs = [d for d in glob.glob(os.path.join(runs_root, "run_*")) if os.path.isdir(d)]
    if not dirs:
        return None
    return max(dirs, key=os.path.getmtime)


def print_report(report_path):
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    print("=" * 72)
    print("REPORT 摘要")
    print("=" * 72)
    rows = [
        ("run_id", report.get("run_id")),
        ("task_id", report.get("task_id")),
        ("status", report.get("status")),
        ("stop_reason", report.get("stop_reason")),
        ("tool_steps", report.get("tool_steps")),
        ("attempts", report.get("attempts")),
        ("resume_status", report.get("resume_status")),
    ]
    for label, value in rows:
        print(f"  {label:<16} {value}")

    final = report.get("final_answer", "")
    if final:
        print(f"\n  final_answer:")
        for line in str(final).splitlines():
            print(f"    {line}")

    pm = report.get("prompt_metadata") or {}
    if pm:
        print(f"\n  prompt_metadata:")
        for key in PROMPT_META_KEYS:
            if key in pm:
                print(f"    {key:<16} {pm[key]}")
    print()


def print_trace(trace_path, show_json, only_event):
    with open(trace_path, encoding="utf-8") as f:
        lines = f.readlines()

    print("=" * 72)
    print("TRACE 事件链（按时间顺序）")
    print("=" * 72)

    for idx, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"[{idx}] (无法解析的行) {line[:80]}")
            continue

        name = event.get("event", "?")
        if only_event and name != only_event:
            continue

        print(f"\n[{idx}] {name}  @{event.get('created_at', '')}")

        if name == "tool_executed":
            # 工具执行是重点，单独展开
            print(f"      name        {event.get('name')}")
            print(f"      tool_status {event.get('tool_status')}   error={event.get('tool_error_code') or '-'}")
            print(f"      args        {clip(json.dumps(event.get('args'), ensure_ascii=False))}")
            print(f"      result      {clip(event.get('result', ''))}")
        elif name == "prompt_built":
            pm = event.get("prompt_metadata") or {}
            for key in PROMPT_META_KEYS:
                if key in pm:
                    print(f"      {key:<16} {pm[key]}")
            if event.get("duration_ms") is not None:
                print(f"      {'duration_ms':<16} {event['duration_ms']}")
        else:
            for key in EVENT_FIELDS.get(name, ()):
                if key in event:
                    value = event[key]
                    if key == "final_answer":
                        value = clip(value)
                    elif key == "user_request":
                        value = clip(value, 120)
                    print(f"      {key:<16} {clip(value) if key in ('final_answer', 'user_request') else value}")

        if show_json:
            print(f"      --- 原始 JSON ---")
            print("      " + json.dumps(event, ensure_ascii=False, indent=2).replace("\n", "\n      "))
    print()


def main(argv=None):
    # Windows 上 Python 的 stdout 默认可能是 GBK，而 trace/report 是 UTF-8 写的，
    # 不强制 UTF-8 会导致中文乱码。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="格式化打印 pico 一次运行的 report 和 trace")
    parser.add_argument("run_dir", nargs="?", help="run 目录路径，默认自动找最新")
    parser.add_argument("--json", action="store_true", help="额外打印每条原始 JSON 事件")
    parser.add_argument("--events", default=None, help="只看指定事件类型，如 tool_executed")
    args = parser.parse_args(argv)

    run_dir = args.run_dir
    if run_dir is None:
        run_dir = find_latest_run(DEFAULT_RUNS_ROOT)
        if run_dir is None:
            print(f"找不到任何 run 目录（在 {DEFAULT_RUNS_ROOT} 下）", file=sys.stderr)
            return 1

    run_dir = Path(run_dir)
    report_path = run_dir / "report.json"
    trace_path = run_dir / "trace.jsonl"

    if not run_dir.is_dir():
        print(f"目录不存在: {run_dir}", file=sys.stderr)
        return 1

    print(f"run 目录: {run_dir}\n")

    if report_path.exists():
        print_report(report_path)
    else:
        print("(没有 report.json —— 这次运行可能还没结束或被中断)\n")

    if trace_path.exists():
        print_trace(trace_path, args.json, args.events)
    else:
        print("(没有 trace.jsonl)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
