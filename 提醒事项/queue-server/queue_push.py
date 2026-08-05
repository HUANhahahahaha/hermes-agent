#!/usr/bin/env python3
"""VPS 上 hermes agent 的提醒写入工具:推队列前先对照「设备提醒快照」做查重/冲突判断。

快照由 Mac 消费器每 10 分钟上传(设备上未来 60 天的未完成提醒),
让云端 agent 在微信对话当场就能发现重复/改期/撞时段,恢复实时智能反馈。

用法(参数与 Mac 版 reminders.py add 一致):
  queue_push.py --title "交稿" [--due "YYYY-MM-DD HH:MM"] [--remind ...] [--list ...] [--notes ...]
                [--replace]  # 改期模式:按标题改已写入那条的时间
                [--force]    # 跳过查重强制入队(确认是另一件事时用)
"""
from __future__ import annotations
import argparse, difflib, json, os, re, urllib.request
from datetime import datetime, timezone

PORT = 8787
TOKEN_FILE = os.path.expanduser("~/.reminder-queue/token")
CONFLICT_WINDOW_MIN = 25


def _req(path: str, data: dict | None = None):
    with open(TOKEN_FILE, encoding="utf-8") as f:
        token = f.read().strip()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(data, ensure_ascii=False).encode() if data is not None else None,
        headers={"X-Queue-Token": token, "Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s).lower()


def _similar(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if (len(na) >= 4 or len(nb) >= 4) and (na in nb or nb in na):
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.75


def _parse(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def _fmt(it) -> str:
    return f'『{it["title"]}』({it.get("due") or "无时间"}, 列表:{it.get("list") or "?"})'


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--title", required=True)
    p.add_argument("--due", default=None)
    p.add_argument("--remind", default=None)
    p.add_argument("--list", dest="list_name", default=None)
    p.add_argument("--notes", default=None)
    p.add_argument("--replace", action="store_true")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()

    new_due = _parse(a.due)

    # ── 1. 队列内防抖(待处理里已有同款) ──
    if not a.replace:
        for it in _req("/pending"):
            if it["title"].strip() == a.title.strip() and (it.get("due") or None) == (a.due or None):
                print(f"⚠️ 队列里已有同样的『{a.title}』,未重复入队。告诉用户已在处理中即可。")
                return 0

    # ── 2. 对照设备快照查重/冲突(--force / --replace 跳过) ──
    warnings = []
    if not a.force and not a.replace:
        snap = _req("/snapshot")
        items = snap.get("items") or []
        age_min = None
        if snap.get("updated"):
            upd = datetime.fromisoformat(snap["updated"])
            age_min = int((datetime.now(timezone.utc) - upd).total_seconds() // 60)
        age_note = (f"(快照{age_min}分钟前更新)" if age_min is not None and age_min > 30
                    else "")

        dups = [it for it in items if _similar(it["title"], a.title)]
        exact = [it for it in dups
                 if (it.get("due") is None and new_due is None)
                 or (it.get("due") and new_due and abs((_parse(it["due"]) - new_due).total_seconds()) < 60)]
        if exact:
            print(f"⚠️ 重复,未入队:设备上已有 {_fmt(exact[0])} {age_note}。"
                  f"告诉用户已经记过这条了。")
            return 0
        if dups:
            it = dups[0]
            print(f"⚠️ 疑似同一件事但时间不同,未入队:设备上已有 {_fmt(it)},新时间是 {a.due or '无时间'} {age_note}。\n"
                  f"你要判断用户意图:\n"
                  f"- 是改期 → 重跑本命令加 --replace(会把已有那条改到新时间)\n"
                  f"- 确实是另一件事 → 重跑本命令加 --force\n"
                  f"- 拿不准 → 问用户「你是想把X从A时间改到B时间,还是另一件事?」\n"
                  f"绝不能不处理就结束。")
            return 0
        if new_due:
            for it in items:
                d = _parse(it.get("due"))
                if d and abs((d - new_due).total_seconds()) <= CONFLICT_WINDOW_MIN * 60 \
                        and not _similar(it["title"], a.title):
                    warnings.append(f"⚠️ 时间冲突:和设备上已有的 {_fmt(it)} 很接近,"
                                    f"必须转告用户,问要不要取舍。")

    # ── 3. 入队 ──
    _req("/push", {"title": a.title, "due": a.due, "remind": a.remind,
                   "list": a.list_name, "notes": a.notes, "source": "hermes-vps",
                   "replace": a.replace})
    if a.replace:
        print(f"✅ 改期已提交: {a.title} → {a.due or '待定'}。已写入设备的那条会被自动改掉。")
    else:
        extra = f"(到期 {a.due})" if a.due else ""
        print(f"✅ 已加入待写队列: {a.title} {extra}。iPhone/Mac 会自动写入,最迟几小时内同步到手机。")
    for w in warnings:
        print(w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
