#!/usr/bin/env python3
"""每日简报：今天该做什么、什么快到期、哪些项目停滞。

用法：
    python3 提醒事项/brief.py            # 今日
    python3 提醒事项/brief.py --week     # 未来 7 天
    python3 提醒事项/brief.py --list HERMES收件

数据源：iCloud 提醒事项（实时）＋ 项目跟踪.md（停滞扫描）。
读不到 iCloud 时会降级为只报项目部分，并说明原因。
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

ICLOUD_URL = "https://caldav.icloud.com"


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("REMINDER_TZ", "Asia/Shanghai"))
    except Exception:
        return timezone.utc


def fetch_todos(list_name: str | None) -> tuple[list[dict], str | None]:
    """返回 (待办列表, 错误说明)。错误时列表为空。"""
    user = os.environ.get("ICLOUD_USERNAME")
    pw = os.environ.get("ICLOUD_APP_PASSWORD")
    if not (user and pw):
        return [], "未设置 ICLOUD_USERNAME / ICLOUD_APP_PASSWORD"
    try:
        import caldav
    except ImportError:
        return [], "缺少依赖：pip install -r 提醒事项/requirements.txt"

    try:
        client = caldav.DAVClient(url=ICLOUD_URL, username=user, password=pw)
        cals = []
        for cal in client.principal().calendars():
            try:
                comps = cal.get_supported_components()
            except Exception:
                comps = []
            if comps and "VTODO" not in comps:
                continue
            try:
                name = cal.get_display_name()
            except Exception:
                name = ""
            if list_name and name != list_name:
                continue
            cals.append((name, cal))
    except Exception as e:  # noqa: BLE001 - 网络/认证问题都要落到人话
        return [], f"连接 iCloud 失败：{type(e).__name__}: {e}"

    if not cals:
        return [], f"没找到列表 {list_name!r}"

    out: list[dict] = []
    for name, cal in cals:
        try:
            objs = cal.objects(load_objects=True)
        except Exception as e:  # noqa: BLE001
            return out, f"读取「{name}」失败：{type(e).__name__}"
        for o in objs:
            data = o.data or ""
            if "BEGIN:VTODO" not in data:
                continue
            summary = (re.search(r"^SUMMARY:(.*)$", data, re.M) or [None, ""])[1].strip()
            if not summary:
                continue
            status = (re.search(r"^STATUS:(.*)$", data, re.M) or [None, ""])[1].strip()
            if status.upper() == "COMPLETED":
                continue
            due_raw = (re.search(r"^DUE[^:\r\n]*:(.*)$", data, re.M) or [None, ""])[1].strip()
            due = None
            m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", due_raw)
            if m:
                y, mo, d, hh, mm = m.groups()
                due = datetime(int(y), int(mo), int(d),
                               int(hh or 0), int(mm or 0))
            out.append({"list": name, "summary": summary, "due": due})
    return out, None


def stale_projects(days: int) -> list[str]:
    try:
        import scan  # type: ignore
    except Exception:
        return []
    projects, _ = scan.parse(scan.TRACKER)
    today = date.today()
    hits = []
    for p in projects:
        if any(k in p.status for k in scan.DONE_MARKS):
            continue
        n = p.stale_days(today)
        if n >= days:
            hits.append(f"{p.name}（{n} 天无进展）")
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="每日简报")
    ap.add_argument("--week", action="store_true", help="看未来 7 天")
    ap.add_argument("--list", default=None, help="只看某个提醒列表")
    ap.add_argument("--stale-days", type=int, default=7)
    ap.add_argument("--all-undated", action="store_true",
                    help="展开全部无期限待办（默认只报数量）")
    args = ap.parse_args(argv)

    now = datetime.now(_tz()).replace(tzinfo=None)
    horizon = now + timedelta(days=7 if args.week else 1)
    span = "未来 7 天" if args.week else "今天"

    print(f"📋 简报 · {span}（{now:%Y-%m-%d %H:%M}）")
    print("=" * 42)

    todos, err = fetch_todos(args.list)
    if err:
        print(f"\n⚠️ 提醒事项读取不到：{err}")
    else:
        overdue = [t for t in todos if t["due"] and t["due"] < now]
        upcoming = [t for t in todos if t["due"] and now <= t["due"] < horizon]
        undated = [t for t in todos if not t["due"]]

        if overdue:
            print(f"\n🔴 已过期（{len(overdue)}）")
            for t in sorted(overdue, key=lambda x: x["due"]):
                print(f"   · {t['summary']}  —— {t['due']:%m-%d %H:%M} 已过")
        if upcoming:
            print(f"\n⏰ {span}到期（{len(upcoming)}）")
            for t in sorted(upcoming, key=lambda x: x["due"]):
                print(f"   · {t['due']:%m-%d %H:%M}  {t['summary']}")
        if undated:
            # 无期限待办往往有上百条历史积压，全列出来会淹没真正紧要的事。
            # 默认只报数量与所在列表，用 --all-undated 展开。
            print(f"\n📝 无期限待办：{len(undated)} 条")
            by_list: dict[str, int] = {}
            for t in undated:
                by_list[t["list"]] = by_list.get(t["list"], 0) + 1
            for name, cnt in sorted(by_list.items(), key=lambda x: -x[1]):
                print(f"   · {name}：{cnt} 条")
            if args.all_undated:
                for t in undated:
                    print(f"     - {t['summary']}")
            else:
                print("   （加 --all-undated 展开全部）")
        if not (overdue or upcoming or undated):
            print("\n✅ 提醒事项里没有待办。")

    hits = stale_projects(args.stale_days)
    if hits:
        print(f"\n⚠️ 停滞项目（超过 {args.stale_days} 天）")
        for h in hits:
            print(f"   · {h}")
        print("   → 跑 `python3 提醒事项/scan.py --remind` 可自动建追问提醒")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
