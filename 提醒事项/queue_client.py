#!/usr/bin/env python3
"""提醒队列客户端 —— 唯一被验证可用的送达通道。

对应 `架构.md` 记录、并经 VPS 生产实现（``~/.hermes/bin/queue_push.py``）
交叉验证的 API：

    GET  /pending    查待写队列        只读，安全
    GET  /snapshot   查设备提醒快照    只读，安全
    POST /push       入队              写入
    GET  /claim      认领出队          ⛔️ 消费型，本模块**刻意不实现**

鉴权：``X-Queue-Token`` 头，token 读自文件 ``~/.reminder-queue/token``。

只能在能访问队列服务的主机上运行（VPS 本机 ``127.0.0.1:8787``）。
云端会话连不上，调用会明确报错而不是假装成功。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

BASE_URL = os.environ.get("REMINDER_QUEUE_URL", "http://127.0.0.1:8787")
TOKEN_FILE = Path(os.environ.get("REMINDER_QUEUE_TOKEN_FILE",
                                 "~/.reminder-queue/token")).expanduser()
DEFAULT_LIST = "收集桶"
SOURCE = "hermes-vps"
#: 标题相似度达到该值即视为同一件事（与生产实现一致）。
SIMILARITY_THRESHOLD = 0.75


class QueueError(RuntimeError):
    """队列不可用或返回错误。调用方应如实上报，不得假装写入成功。"""


def _token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise QueueError(
            f"找不到 token 文件 {TOKEN_FILE} —— 本命令只能在队列服务所在主机上运行"
        ) from None
    except OSError as e:
        raise QueueError(f"读取 token 失败：{e}") from None


def _request(method: str, path: str, body: Optional[dict] = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, method=method,
        headers={"X-Queue-Token": _token(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise QueueError(f"{method} {path} → HTTP {e.code}: "
                         f"{e.read()[:200].decode(errors='replace')}") from None
    except Exception as e:  # noqa: BLE001 - 网络问题也要落到人话
        raise QueueError(f"{method} {path} 失败：{type(e).__name__}: {e}") from None


# ── 只读 ────────────────────────────────────────────────────────────────

def pending() -> list[dict]:
    """待写队列。**不消费**。"""
    return _request("GET", "/pending") or []


def snapshot() -> list[dict]:
    """设备上已有提醒的快照（Mac 端上传）。**不消费**。"""
    return _request("GET", "/snapshot") or []


# ── 去重判定 ────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    """去重比对结果。``action`` ∈ new / duplicate / reschedule / conflict。"""
    action: str
    matched: Optional[dict] = None
    similarity: float = 0.0
    reason: str = ""

    @property
    def should_push(self) -> bool:
        return self.action in ("new", "reschedule")

    @property
    def replace(self) -> bool:
        return self.action == "reschedule"


def _norm_due(v: Any) -> Optional[str]:
    """把 due 归一成 'YYYY-MM-DD HH:MM' 以便比较；解析不了就原样返回。"""
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return s


def classify(title: str, due: Optional[str],
             existing: list[dict]) -> Verdict:
    """按生产协议判定：与 *existing* 比对标题相似度与时间。

    相似度 ≥ SIMILARITY_THRESHOLD 视为同一件事，再看时间：
      时间相同 → duplicate（放弃）
      时间不同 → reschedule（带 replace 覆盖）
      一方无时间 → conflict（交给人判断，不擅自写）
    """
    best: tuple[float, Optional[dict]] = (0.0, None)
    for item in existing:
        other = str(item.get("title") or "").strip()
        if not other:
            continue
        ratio = SequenceMatcher(None, title.strip(), other).ratio()
        if ratio > best[0]:
            best = (ratio, item)

    ratio, match = best
    if ratio < SIMILARITY_THRESHOLD or match is None:
        return Verdict("new", similarity=ratio)

    a, b = _norm_due(due), _norm_due(match.get("due"))
    if a == b:
        return Verdict("duplicate", match, ratio,
                       f"标题相似度 {ratio:.2f} 且时间相同（{a}）")
    if a and b:
        return Verdict("reschedule", match, ratio,
                       f"标题相似度 {ratio:.2f}，时间 {b} → {a}")
    return Verdict("conflict", match, ratio,
                   f"标题相似度 {ratio:.2f}，但一方无时间（{b} vs {a}）")


# ── 写入 ────────────────────────────────────────────────────────────────

def push(title: str, *, due: Optional[str] = None,
         remind: Optional[str] = None, notes: str = "",
         list_name: str = DEFAULT_LIST, replace: bool = False) -> dict:
    """直接入队。**通常不要直接调用** —— 用 ``add`` 走完去重协议。"""
    body = {"title": title, "due": due, "remind": remind,
            "list": list_name, "notes": notes,
            "source": SOURCE, "replace": replace}
    return _request("POST", "/push", {k: v for k, v in body.items()
                                      if v is not None}) or {}


def add(title: str, *, due: Optional[str] = None,
        remind: Optional[str] = None, notes: str = "",
        list_name: str = DEFAULT_LIST,
        force: bool = False) -> Verdict:
    """完整流程：查 pending → 比 snapshot → 决定是否 push。

    返回判定结果；``duplicate`` / ``conflict`` 时**不会写入**，
    由调用方决定是询问用户还是放弃。``force=True`` 跳过判定强制写入。
    """
    if force:
        push(title, due=due, remind=remind, notes=notes, list_name=list_name)
        return Verdict("new", reason="force")

    verdict = classify(title, due, pending())
    if verdict.action != "new":
        verdict.reason = "队列中已有：" + verdict.reason
        return verdict

    verdict = classify(title, due, snapshot())
    if verdict.should_push:
        push(title, due=due, remind=remind, notes=notes,
             list_name=list_name, replace=verdict.replace)
    return verdict


if __name__ == "__main__":
    import sys
    try:
        print(f"待写队列 {len(pending())} 条 / 设备快照 {len(snapshot())} 条")
    except QueueError as e:
        print(f"❌ {e}")
        sys.exit(1)
