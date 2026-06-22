#!/usr/bin/env python3
"""
iMac 锚节点 —— 跨设备「提醒事项共享总线」的本地读写节点。

为什么用它而不是 CalDAV：
    云端会话用的 iCloud CalDAV 桥登录到的是一个**旧数据分区**，
    那不是你 iMac/iPhone 正在同步的真身（实测：新建列表都同步不到设备）。
    本脚本跑在你**常开的 iMac** 上，通过 `remindctl`(EventKit) 直接读写
    macOS 本机那份**已经和手机同步好的**提醒库——也就是你手机上看到的
    「收集桶 / 项目 / 报销提醒」那一份原物。iCloud 再把它同步到所有设备。

依赖：
    - macOS + Reminders.app，登录 Apple ID = ifoon@me.com（与手机同一个）
    - brew install steipete/tap/remindctl
    - remindctl authorize  （首次授权 Reminders 权限）

只用 remindctl 的“已文档化”命令，纯标准库，无第三方依赖。

用法（先验证，再用总线）：
    python3 anchor_node.py verify          # ① 第一步：证明能看到真库（收集桶/项目…）
    python3 anchor_node.py bus-init        # ② 创建共享总线列表 Hermes-Bus
    python3 anchor_node.py post "标题" --body "正文" --from imac
    python3 anchor_node.py read            # 读未处理的总线消息
    python3 anchor_node.py loop --interval 90   # 常驻轮询，有新消息就打印（交给 Hermes 处理）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

BUS_LIST = "Hermes-Bus"          # 跨设备共享总线列表名（专用，别和收集桶混）
TAG = "#hermes-bus"              # 写进标题的可识别标记，方便人/机过滤


# --------------------------------------------------------------------------- #
# remindctl 薄封装
# --------------------------------------------------------------------------- #
def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """跑一条 remindctl 命令。"""
    try:
        return subprocess.run(
            ["remindctl", *args],
            capture_output=True, text=True, check=check,
        )
    except FileNotFoundError:
        sys.exit("✗ 找不到 remindctl。先装：brew install steipete/tap/remindctl")
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.stdout or "")
        sys.stderr.write(e.stderr or "")
        raise


def _run_json(args: list[str]):
    """跑命令并解析 --json 输出，失败返回 None（不同 remindctl 版本字段可能不同）。"""
    cp = _run([*args, "--json"], check=False)
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# ① verify —— 上一会话缺的那一步：在设备上真验证
# --------------------------------------------------------------------------- #
def cmd_verify(_args) -> int:
    print("== remindctl 权限状态 ==")
    print(_run(["status"], check=False).stdout.strip() or "(无输出)")

    print("\n== 本机能看到的提醒列表（应包含你手机上的：收集桶/项目/报销提醒…）==")
    print(_run(["list"], check=False).stdout.strip() or "(无输出)")

    print(
        "\n👉 人工确认：上面的列表里**有没有**你手机上那些列表"
        "（收集桶、项目、报销提醒…）？\n"
        "   有  → iMac 看到的就是真库，CalDAV 那条死路彻底弃用，往下走 bus-init。\n"
        "   没有 → 说明这台 iMac 登的不是 ifoon@me.com，或 Reminders 没开 iCloud 同步，先修这个。"
    )
    return 0


# --------------------------------------------------------------------------- #
# ② 总线：init / post / read
# --------------------------------------------------------------------------- #
def cmd_bus_init(_args) -> int:
    existing = _run(["list"], check=False).stdout
    if BUS_LIST in existing:
        print(f"✓ 总线列表「{BUS_LIST}」已存在。")
        return 0
    _run(["list", BUS_LIST, "--create"])
    print(f"✓ 已创建总线列表「{BUS_LIST}」。它会通过 iCloud 同步到你所有设备。")
    return 0


def _encode_meta(source: str, mtype: str) -> str:
    return json.dumps(
        {"v": 1, "from": source, "type": mtype,
         "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        ensure_ascii=False,
    )


def cmd_post(args) -> int:
    """往总线写一条消息。元数据放标题尾部的标记，正文放 --body。"""
    title = f"{args.title}  {TAG}[{args.source}/{args.type}]"
    add = ["add", "--title", title, "--list", BUS_LIST]
    if args.body:
        # --notes 在多数 remindctl 版本可用；不可用则降级（正文并入标题）。
        notes = f"{args.body}\n\n{_encode_meta(args.source, args.type)}"
        cp = _run([*add, "--notes", notes], check=False)
        if cp.returncode != 0:
            # 降级：该版本不支持 --notes，把正文并进标题重发一条
            _run(["add", "--title", f"{title} | {args.body}", "--list", BUS_LIST])
    else:
        _run(add)
    print(f"✓ 已写入总线「{BUS_LIST}」：{args.title}  (from {args.source})")
    print("  iCloud 同步后，你手机和其它设备的该列表里就会看到这条。")
    return 0


def _bus_items() -> list[dict]:
    """读总线列表里未完成的条目。优先 JSON，拿不到就回退纯文本。"""
    data = _run_json(["list", BUS_LIST])
    items: list[dict] = []
    if isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and not it.get("completed"):
                items.append(it)
        return items
    # 回退：纯文本逐行
    txt = _run(["list", BUS_LIST], check=False).stdout
    for line in txt.splitlines():
        line = line.strip()
        if line and TAG in line:
            items.append({"title": line})
    return items


def cmd_read(_args) -> int:
    items = _bus_items()
    if not items:
        print(f"（总线「{BUS_LIST}」暂无未处理消息）")
        return 0
    print(f"== 总线「{BUS_LIST}」未处理消息 {len(items)} 条 ==")
    for it in items:
        ident = it.get("id") or it.get("externalId") or "?"
        print(f"  [{ident}] {it.get('title','').strip()}")
        if it.get("notes"):
            print(f"        ↳ {it['notes'].splitlines()[0]}")
    print("\n处理完用 remindctl complete <id> 勾掉，当作“已读”。")
    return 0


# --------------------------------------------------------------------------- #
# ③ loop —— 常驻轮询（纯传输，把新消息交给 iMac 上的 Hermes 去处理）
# --------------------------------------------------------------------------- #
def cmd_loop(args) -> int:
    seen: set[str] = set()
    print(f"▶ 锚节点轮询启动：每 {args.interval}s 扫一次「{BUS_LIST}」。Ctrl-C 退出。")
    try:
        while True:
            for it in _bus_items():
                ident = str(it.get("id") or it.get("title"))
                if ident not in seen:
                    seen.add(ident)
                    stamp = datetime.now().strftime("%H:%M:%S")
                    print(f"[{stamp}] 新消息: {it.get('title','').strip()}")
                    # TODO(接入点): 在这里把 it 交给本机 Hermes 处理，
                    #               Hermes 产出后用 cmd_post 写回总线、用 complete 勾掉本条。
            time.sleep(max(15, args.interval))
    except KeyboardInterrupt:
        print("\n■ 已停止。")
    return 0


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="iMac 锚节点 — 提醒事项共享总线")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify", help="验证本机 remindctl 能看到真库").set_defaults(func=cmd_verify)
    sub.add_parser("bus-init", help="创建共享总线列表").set_defaults(func=cmd_bus_init)

    sp = sub.add_parser("post", help="往总线写一条消息")
    sp.add_argument("title")
    sp.add_argument("--body", default="")
    sp.add_argument("--from", dest="source", default="imac", help="来源设备标识")
    sp.add_argument("--type", default="note", help="消息类型，如 note/task/report")
    sp.set_defaults(func=cmd_post)

    sub.add_parser("read", help="读未处理的总线消息").set_defaults(func=cmd_read)

    lp = sub.add_parser("loop", help="常驻轮询总线")
    lp.add_argument("--interval", type=int, default=90, help="轮询间隔秒，最小 15")
    lp.set_defaults(func=cmd_loop)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
