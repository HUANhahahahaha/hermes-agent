#!/usr/bin/env python3
"""提醒事项 → iCloud 提醒事项 同步桥（CalDAV）

通过 iCloud CalDAV 直接把提醒写进苹果设备的「提醒事项 App」，跨设备同步，
无需 Mac 开机。凭据从环境变量读取，绝不写进代码或仓库。

环境变量：
  ICLOUD_USERNAME       你的 Apple ID 邮箱
  ICLOUD_APP_PASSWORD   App 专用密码（appleid.apple.com → 登录与安全 → App 专用密码）
  REMINDER_TZ           时区，默认 Asia/Shanghai

用法：
  python3 sync.py lists
  python3 sync.py add --title "孙建亚老师 家前采时间确认（绿洲比华利花园931号楼）" \
      --list "工作-采访" --due "2026-07-02 10:00" --alarm "2026-07-02 09:00"
  python3 sync.py show --list "工作-采访"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

ICLOUD_URL = "https://caldav.icloud.com"


def _tz():
    name = os.environ.get("REMINDER_TZ", "Asia/Shanghai")
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def parse_when(s: str):
    """解析 'today'/'tomorrow'/'YYYY-MM-DD'/'YYYY-MM-DD HH:mm' → datetime 或 date。"""
    s = s.strip()
    tz = _tz()
    low = s.lower()
    if low in ("today", "今天"):
        return datetime.now(tz).replace(microsecond=0)
    if low in ("tomorrow", "明天"):
        return (datetime.now(tz) + timedelta(days=1)).replace(microsecond=0)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=tz)
        except ValueError:
            pass
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"无法解析日期时间：{s!r}（用 YYYY-MM-DD 或 YYYY-MM-DD HH:mm）")


def _client():
    user = os.environ.get("ICLOUD_USERNAME")
    pw = os.environ.get("ICLOUD_APP_PASSWORD")
    if not user or not pw:
        raise SystemExit(
            "缺少凭据：请设置 ICLOUD_USERNAME 和 ICLOUD_APP_PASSWORD 环境变量。\n"
            "App 专用密码生成：appleid.apple.com → 登录与安全 → App 专用密码。"
        )
    import caldav

    return caldav.DAVClient(url=ICLOUD_URL, username=user, password=pw)


def _todo_calendars(principal):
    """返回支持 VTODO（提醒事项）的列表。"""
    out = []
    for cal in principal.calendars():
        try:
            comps = cal.get_supported_components()
        except Exception:
            comps = []
        if not comps or "VTODO" in comps:
            out.append(cal)
    return out


def _pick_list(principal, name: str | None):
    cals = _todo_calendars(principal)
    if not cals:
        raise SystemExit("账号下没有可写入的提醒事项列表。")
    if name:
        for cal in cals:
            if (cal.name or "").strip() == name.strip():
                return cal
        avail = ", ".join((c.name or "?") for c in cals)
        raise SystemExit(f"找不到列表 {name!r}。现有列表：{avail}")
    return cals[0]


def cmd_lists(_args):
    principal = _client().principal()
    cals = _todo_calendars(principal)
    print("可用提醒事项列表：")
    for c in cals:
        print(f"  • {c.name}")


def cmd_add(args):
    from icalendar import Alarm, Todo
    from icalendar import Calendar as ICal
    import uuid

    principal = _client().principal()
    cal = _pick_list(principal, args.list)

    todo = Todo()
    todo.add("uid", str(uuid.uuid4()))
    todo.add("summary", args.title)
    todo.add("dtstamp", datetime.now(_tz()))
    if args.due:
        due = parse_when(args.due)
        todo.add("due", due)
    if args.notes:
        todo.add("description", args.notes)
    if args.alarm:
        when = parse_when(args.alarm)
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", args.title)
        if isinstance(when, datetime):
            alarm.add("trigger", when)
        todo.add_component(alarm)

    container = ICal()
    container.add("prodid", "-//Hermes//提醒事项 sync//CN")
    container.add("version", "2.0")
    container.add_component(todo)

    cal.save_todo(ical=container.to_ical().decode())
    print(f"✅ 已写入「{cal.name}」：{args.title}")
    if args.due:
        print(f"   到期：{args.due}" + (f"  提醒：{args.alarm}" if args.alarm else ""))


def cmd_show(args):
    principal = _client().principal()
    cal = _pick_list(principal, args.list)
    todos = cal.todos()
    print(f"「{cal.name}」未完成提醒（{len(todos)}）：")
    for t in todos:
        inst = t.icalendar_instance.subcomponents[0]
        summary = inst.get("summary", "")
        due = inst.get("due")
        due_s = f"  @ {due.dt}" if due else ""
        print(f"  • {summary}{due_s}")


def main(argv=None):
    p = argparse.ArgumentParser(description="iCloud 提醒事项 同步桥")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("lists", help="列出所有提醒事项列表").set_defaults(func=cmd_lists)

    pa = sub.add_parser("add", help="新增一条提醒")
    pa.add_argument("--title", required=True)
    pa.add_argument("--list", default=None, help="目标列表名（默认第一个）")
    pa.add_argument("--due", default=None, help="到期：YYYY-MM-DD[ HH:mm]")
    pa.add_argument("--alarm", default=None, help="提前提醒：YYYY-MM-DD HH:mm")
    pa.add_argument("--notes", default=None, help="备注")
    pa.set_defaults(func=cmd_add)

    ps = sub.add_parser("show", help="查看某列表未完成提醒")
    ps.add_argument("--list", default=None)
    ps.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
