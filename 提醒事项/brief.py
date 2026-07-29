#!/usr/bin/env python3
"""每日简报：今天该做什么、什么快到期、哪些项目停滞。

数据源（均为**只读**，绝不消费队列）：
    GET /snapshot   设备上已有的提醒（Mac 端上传）← 主力
    GET /pending    还在队列里、尚未同步到设备的
    项目跟踪.md     停滞项目扫描

⛔️ 绝不调用 ``/claim``（认领即出队，会把用户待收的提醒吃掉）。
⛔️ 不再走 CalDAV —— 那是苹果升级后遗留的废仓库，读到的是幽灵数据。

用法：
    python3 提醒事项/brief.py             # 今日
    python3 提醒事项/brief.py --week      # 未来 7 天
    python3 提醒事项/brief.py --all-undated
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("REMINDER_TZ", "Asia/Shanghai"))
    except Exception:
        return timezone.utc


def _parse_due(v) -> datetime | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    if m:
        return datetime(*(int(x) for x in m.groups()))
    return None


def collect() -> tuple[list[dict], str | None]:
    """返回 (待办, 错误说明)。每条含 source 标明来自设备还是队列。"""
    try:
        import queue_client as qc
    except ImportError:
        return [], "找不到 queue_client.py"

    out: list[dict] = []
    try:
        for item in qc.snapshot():
            if str(item.get("status", "")).lower() == "completed":
                continue
            out.append({"title": item.get("title", ""),
                        "due": _parse_due(item.get("due")),
                        "list": item.get("list") or "",
                        "source": "设备"})
        for item in qc.pending():
            out.append({"title": item.get("title", ""),
                        "due": _parse_due(item.get("due")),
                        "list": item.get("list") or "",
                        "source": "队列待同步"})
    except qc.QueueError as e:
        return out, str(e)
    return [t for t in out if t["title"]], None


def stale_projects(days: int) -> list[str]:
    try:
        import scan  # type: ignore
    except Exception:
        return []
    projects, _ = scan.parse(scan.TRACKER)
    today = date.today()
    return [f"{p.name}（{p.stale_days(today)} 天无进展）"
            for p in projects
            if not any(k in p.status for k in scan.DONE_MARKS)
            and p.stale_days(today) >= days]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="每日简报")
    ap.add_argument("--week", action="store_true", help="看未来 7 天")
    ap.add_argument("--stale-days", type=int, default=7)
    ap.add_argument("--all-undated", action="store_true",
                    help="展开全部无期限待办（默认只报数量）")
    args = ap.parse_args(argv)

    now = datetime.now(_tz()).replace(tzinfo=None)
    horizon = now + timedelta(days=7 if args.week else 1)
    span = "未来 7 天" if args.week else "今天"

    print(f"📋 简报 · {span}（{now:%Y-%m-%d %H:%M}）")
    print("=" * 42)

    todos, err = collect()
    if err:
        print(f"\n⚠️ 读取提醒失败：{err}")
        print("   （本命令需在能访问队列服务的主机上运行）")
    else:
        overdue = [t for t in todos if t["due"] and t["due"] < now]
        upcoming = [t for t in todos if t["due"] and now <= t["due"] < horizon]
        undated = [t for t in todos if not t["due"]]
        queued = [t for t in todos if t["source"] == "队列待同步"]

        if overdue:
            print(f"\n🔴 已过期（{len(overdue)}）")
            for t in sorted(overdue, key=lambda x: x["due"]):
                print(f"   · {t['due']:%m-%d %H:%M} 已过  {t['title']}")
        if upcoming:
            print(f"\n⏰ {span}到期（{len(upcoming)}）")
            for t in sorted(upcoming, key=lambda x: x["due"]):
                mark = "  [待同步]" if t["source"] == "队列待同步" else ""
                print(f"   · {t['due']:%m-%d %H:%M}  {t['title']}{mark}")
        if undated:
            # 历史积压常有上百条，全列会淹没真正紧要的事。
            print(f"\n📝 无期限待办：{len(undated)} 条")
            by_list: dict[str, int] = {}
            for t in undated:
                by_list[t["list"] or "（未分列表）"] = \
                    by_list.get(t["list"] or "（未分列表）", 0) + 1
            for name, cnt in sorted(by_list.items(), key=lambda x: -x[1]):
                print(f"   · {name}：{cnt} 条")
            if args.all_undated:
                for t in undated:
                    print(f"     - {t['title']}")
            else:
                print("   （加 --all-undated 展开全部）")
        if queued:
            print(f"\n📮 队列中还有 {len(queued)} 条待同步到设备")
        if not todos:
            print("\n✅ 没有待办。")

    hits = stale_projects(args.stale_days)
    if hits:
        print(f"\n⚠️ 停滞项目（超过 {args.stale_days} 天）")
        for h in hits:
            print(f"   · {h}")
        print("   → `python3 提醒事项/scan.py --remind` 可自动排追问进队列")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
