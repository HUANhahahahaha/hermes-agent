#!/usr/bin/env python3
"""扫描 项目跟踪.md，找出停滞项目，可选地把追问写成 iCloud 提醒。

用法：
    python3 提醒事项/scan.py                # 只打印
    python3 提醒事项/scan.py --days 14      # 自定义停滞阈值（默认 7 天）
    python3 提醒事项/scan.py --remind       # 为每个停滞项目建一条提醒

表格约定（项目跟踪.md）：
    | 项目 | 状态 | 最近进展 | 上次更新 | 下一步 / 里程碑 |
「上次更新」列写 YYYY-MM-DD；解析不出日期的行会被跳过并提示。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRACKER = ROOT / "项目跟踪.md"
DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
DONE_MARKS = ("✅", "已完成", "完成", "归档")


@dataclass
class Project:
    name: str
    status: str
    progress: str
    updated: date
    next_step: str

    def stale_days(self, today: date) -> int:
        return (today - self.updated).days


def parse(path: Path) -> tuple[list[Project], list[str]]:
    """返回 (可解析的项目, 跳过的行说明)。"""
    projects: list[Project] = []
    skipped: list[str] = []
    if not path.exists():
        return projects, [f"未找到 {path}"]

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        name = cells[0]
        # 跳过表头与示例行
        if name in ("项目", "") or name.startswith("_"):
            continue
        m = DATE_RE.search(cells[3])
        if not m:
            skipped.append(f"{name}（「上次更新」缺日期）")
            continue
        y, mo, d = (int(x) for x in m.groups())
        try:
            updated = date(y, mo, d)
        except ValueError:
            skipped.append(f"{name}（日期不合法：{cells[3]}）")
            continue
        projects.append(Project(
            name=name, status=cells[1], progress=cells[2],
            updated=updated, next_step=cells[4] if len(cells) > 4 else "",
        ))
    return projects, skipped


def add_reminder(title: str, notes: str, list_name: str) -> bool:
    cmd = [sys.executable, str(ROOT / "sync.py"), "add",
           "--title", title, "--list", list_name, "--notes", notes]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"   ⚠️ 写入提醒失败：{proc.stderr.strip().splitlines()[-1:] or proc.stdout}")
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="扫描停滞项目")
    ap.add_argument("--days", type=int, default=7, help="停滞阈值天数（默认 7）")
    ap.add_argument("--remind", action="store_true", help="为停滞项目建提醒")
    ap.add_argument("--list", default="HERMES收件", help="提醒写入的列表名")
    args = ap.parse_args(argv)

    today = date.today()
    projects, skipped = parse(TRACKER)

    if not projects:
        print("项目跟踪.md 里还没有可解析的项目。")
        for s in skipped:
            print(f"  · 跳过：{s}")
        print("\n把你在做的项目按表格填进去，我就能替你盯进度了。")
        return 0

    active = [p for p in projects
              if not any(k in p.status for k in DONE_MARKS)]
    stale = sorted((p for p in active if p.stale_days(today) >= args.days),
                   key=lambda p: -p.stale_days(today))

    print(f"扫描 {len(projects)} 个项目（进行中 {len(active)}），"
          f"停滞阈值 {args.days} 天，今天 {today}\n")

    if not stale:
        print("✅ 没有停滞项目，都在推进中。")
    else:
        print(f"⚠️ {len(stale)} 个项目停滞：\n")
        for p in stale:
            n = p.stale_days(today)
            print(f"  • {p.name} —— 已 {n} 天无进展（上次 {p.updated}）")
            if p.progress:
                print(f"      最近进展：{p.progress}")
            if p.next_step:
                print(f"      原定下一步：{p.next_step}")
            if args.remind:
                title = f"「{p.name}」已 {n} 天没动静了，卡在哪？"
                notes = (f"上次更新：{p.updated}｜最近进展：{p.progress}"
                         f"｜原定下一步：{p.next_step}")
                if add_reminder(title, notes, args.list):
                    print(f"      → 已建提醒")
            print()

    for s in skipped:
        print(f"· 跳过：{s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
