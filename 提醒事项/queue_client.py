#!/usr/bin/env python3
"""提醒队列客户端 —— 唯一被验证可用的送达通道。

对应 `架构.md` 记录、并经 VPS 生产实现（``~/.hermes/bin/queue_push.py``）
交叉验证的 API：

    GET  /pending    查待写队列        只读，安全
    GET  /snapshot   查设备提醒快照    只读，安全
    POST /push       入队              写入（``op`` 可为 add / delete）
    POST /cancel     撤回未认领条目    写入
    GET  /claim      认领出队          ⛔️ 消费型，本模块**刻意不实现**

鉴权：``X-Queue-Token`` 头，token 读自文件 ``~/.reminder-queue/token``。

**增删改的边界**（2026-08-04 查明，见 ``架构.md``）：快捷指令只会「添加」，
所以一条提醒一旦被认领落进手机，队列这边就再也碰不到它 —— 除非快捷指令
本身增加「查找 + 移除」的一段。``delete`` / ``update`` 依赖那段新逻辑，
在快捷指令更新之前调用会入队但永远不会被执行。

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
         list_name: str = DEFAULT_LIST, replace: bool = False,
         op: str = "add") -> dict:
    """直接入队。**通常不要直接调用** —— 用 ``add`` 走完去重协议。"""
    body = {"title": title, "due": due, "remind": remind,
            "list": list_name, "notes": notes,
            "source": SOURCE, "replace": replace, "op": op}
    return _request("POST", "/push", {k: v for k, v in body.items()
                                      if v is not None}) or {}


def cancel(title: str) -> dict:
    """把还没被认领的条目撤回。只作用于队列，碰不到已落进手机的提醒。"""
    return _request("POST", "/cancel", {"title": title, "source": SOURCE}) or {}


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


# ── 定位（删/改的前置步骤） ──────────────────────────────────────────────

#: 删改要求比新增去重更有把握 —— 删错了用户手机上就少一条，且无法撤销。
MATCH_THRESHOLD = 0.85


@dataclass
class Located:
    """定位结果。``where`` ∈ pending / device / none。

    ``candidates`` 在有歧义（多条命中）时列出全部，供调用方向用户确认。
    ``exact_title`` 是**设备上的原始标题** —— 删除指令必须用它，
    因为快捷指令那端是按标题精确匹配查找的，用户的转述匹配不上。
    """
    where: str
    exact_title: Optional[str] = None
    matched: Optional[dict] = None
    candidates: list[dict] = None  # type: ignore[assignment]
    similarity: float = 0.0

    def __post_init__(self) -> None:
        if self.candidates is None:
            self.candidates = []

    @property
    def unambiguous(self) -> bool:
        return self.where != "none" and len(self.candidates) == 1


def _match_all(title: str, items: list[dict]) -> list[tuple[float, dict]]:
    hits = []
    for item in items:
        other = str(item.get("title") or "").strip()
        if not other:
            continue
        ratio = SequenceMatcher(None, title.strip(), other).ratio()
        if ratio >= MATCH_THRESHOLD:
            hits.append((ratio, item))
    return sorted(hits, key=lambda x: -x[0])


def locate(title: str) -> Located:
    """按标题找出用户指的是哪一条。**先查队列，再查设备。**

    顺序不能反：还躺在队列里的条目要用 ``cancel`` 撤回，已落进手机的才需要
    下删除指令。搞反了会出现「删除指令找不到东西、新增指令照样把它加进去」。
    """
    hits = _match_all(title, pending())
    if hits:
        return Located("pending", str(hits[0][1].get("title") or ""),
                       hits[0][1], [h[1] for h in hits], hits[0][0])

    hits = _match_all(title, snapshot())
    if hits:
        return Located("device", str(hits[0][1].get("title") or ""),
                       hits[0][1], [h[1] for h in hits], hits[0][0])

    return Located("none")


# ── 删除 / 修改 ─────────────────────────────────────────────────────────

def delete(title: str, *, force: bool = False) -> Located:
    """删除一条提醒。

    安全规则（**不要放宽**）：
      * 命中 0 条 → 什么都不做，如实回报「没找到」
      * 命中 ≥2 条 → 什么都不做，把候选列出来让用户挑
      * 命中 1 条 → 用**设备上的原始标题**下指令

    ``force=True`` 只跳过「多条命中」的保护，仍然不会凭空删不存在的条目。
    调用方**必须先把 ``exact_title`` 念给用户确认**再执行 —— 删除不可撤销。
    """
    found = locate(title)
    if found.where == "none":
        return found
    if len(found.candidates) > 1 and not force:
        return found

    assert found.exact_title
    if found.where == "pending":
        cancel(found.exact_title)      # 还没出队，直接撤回，不必惊动手机
    else:
        push(found.exact_title, op="delete")
    return found


def update(title: str, *, due: Optional[str] = None,
           remind: Optional[str] = None, notes: str = "",
           new_title: Optional[str] = None,
           list_name: str = DEFAULT_LIST, force: bool = False) -> Located:
    """修改一条提醒（改期、改标题、改备注）。

    设备上没有「原地编辑」这条路 —— 快捷指令能做的只有查找、移除、添加。
    所以改 = **先删后加**，靠删除段跑在新增段前面来保证顺序。

    还在队列里的条目走 ``replace``（原有机制），不必绕这一圈。
    """
    found = locate(title)
    if found.where == "none":
        return found
    if len(found.candidates) > 1 and not force:
        return found

    assert found.exact_title and found.matched
    final_title = new_title or found.exact_title

    if found.where == "pending":
        push(final_title, due=due, remind=remind, notes=notes,
             list_name=list_name, replace=True)
        return found

    push(found.exact_title, op="delete")
    push(final_title, due=due, remind=remind, notes=notes,
         list_name=list_name)
    return found


if __name__ == "__main__":
    import sys
    try:
        print(f"待写队列 {len(pending())} 条 / 设备快照 {len(snapshot())} 条")
    except QueueError as e:
        print(f"❌ {e}")
        sys.exit(1)
