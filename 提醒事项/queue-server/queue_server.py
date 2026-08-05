#!/usr/bin/env python3
"""云端待写提醒队列 —— 跑在 VPS 上的极简 HTTP 服务(纯标准库,零依赖)。

hermes agent 解析出提醒后 POST /push 入队;
消费端(Mac launchd 脚本 / iPhone 快捷指令)GET /pending 拉取,写入本机提醒事项后 POST /ack 回执。

认证:所有请求带 header `X-Queue-Token: <token>`,token 在 ~/.reminder-queue/token。
存储:~/.reminder-queue/queue.json(append-only + 状态标记,单进程内存锁足够)。
     ~/.reminder-queue/delete_queue.json(独立存放待删条目,不与 add 队列混放,
     避免老版本快捷指令 GET /claim 把删除指令当成新增条目写进手机)。

Endpoints:
  GET  /health            → {"ok": true, "pending": N}
  POST /push               → body: {title, due?, remind?, list?, notes?, source?,
                                     replace?, op?} → {"id": ...}
                             op: "add"(默认) | "delete"。缺省行为与此前完全一致。
                             op="delete" 的条目进入独立的 delete_queue,不进入
                             普通 add 队列,只需要 title 字段(due/notes 不参与匹配)。
  GET  /pending            → [{id, title, due, remind, list, notes, source, created}]
                             (仅 add 队列,未认领项;行为未变)
  GET  /claim              → 认领并出队全部普通待写项(op=add,mode!=reschedule)。
                             不带参数时只返回 add 队列的条目 —— 这是历史行为,
                             必须保持,否则老版本快捷指令会把删除指令当成新提醒写入。
  GET  /claim?op=delete    → 认领并出队全部待删条目(来自独立的 delete 队列)。
  POST /cancel              → body: {title} → 撤掉 add 队列里"尚未被认领"的同名条目,
                             返回 {"ok": true, "cancelled": N}。只作用于队列,
                             碰不到已经落进手机的提醒。
  POST /ack                → body: {id, result} → {"ok": true}
  GET  /snapshot            → Mac 端上传的"手机里已有哪些提醒"快照
  POST /snapshot            → 同上,写入快照
"""
from __future__ import annotations
import json, os, threading, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.expanduser("~/.reminder-queue")
QUEUE_FILE = os.path.join(BASE, "queue.json")
DELETE_QUEUE_FILE = os.path.join(BASE, "delete_queue.json")   # op=delete 独立队列
SNAPSHOT_FILE = os.path.join(BASE, "snapshot.json")   # Mac 上传的"手机里已有哪些提醒"快照
TOKEN_FILE = os.path.join(BASE, "token")
PORT = 8787
_LOCK = threading.Lock()


def _load(path: str = QUEUE_FILE) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(items: list[dict], path: str = QUEUE_FILE) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _token() -> str:
    with open(TOKEN_FILE, encoding="utf-8") as f:
        return f.read().strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "ReminderQueue/2"

    def _deny(self, code: int, msg: str) -> None:
        self._json(code, {"error": msg})

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        return self.headers.get("X-Queue-Token", "") == _token()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 65536:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return None

    def _claim(self, params: dict):
        """认领并出队。op=add(默认,含不带参数)只动 add 队列(现有行为,
        不带参数时绝不返回 delete 队列的条目);op=delete 只动独立的
        delete 队列。GET 和 POST 都路由到这里,兼容两种调用方式。"""
        op = params.get("op", "add")
        if op == "delete":
            with _LOCK:
                ditems = _load(DELETE_QUEUE_FILE)
                claimed = [i for i in ditems if i["status"] == "pending"]
                remaining = [i for i in ditems if i["status"] != "pending"]
                for it in claimed:
                    it["status"] = "done"
                    it["result"] = "[claimed by consumer op=delete]"
                    it["acked"] = datetime.now(timezone.utc).isoformat()
                if claimed:
                    _save(remaining, DELETE_QUEUE_FILE)  # 出队:已认领的直接移除
            return self._json(200, [{"title": i["title"], "list": i.get("list") or "收集桶"}
                                    for i in claimed])
        # op=add(默认,含缺省不带参数的情况):行为与此前完全一致 ——
        # 一次取走全部普通待写项并标记完成(给 iPhone 快捷指令用:免逐条回执)。
        # 改期项(mode=reschedule)不给,留给 Mac 消费器处理。
        # delete 队列独立存放,这里绝不会返回 op=delete 的条目。
        with _LOCK:
            items = _load()
            claimed = [i for i in items
                       if i["status"] == "pending" and i.get("mode") != "reschedule"]
            for it in claimed:
                it["status"] = "done"
                it["result"] = "[claimed by iphone]"
                it["acked"] = datetime.now(timezone.utc).isoformat()
            if claimed:
                _save(items)
        return self._json(200, [{"title": i["title"], "due": i["due"] or "",
                                 "notes": i["notes"] or "", "list": i["list"] or "收集桶"}
                                for i in claimed])

    def do_GET(self):
        if not self._authed():
            return self._deny(401, "bad token")
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) if "=" in p else (p, "") for p in query.split("&") if p)
        if path == "/claim":
            return self._claim(params)
        with _LOCK:
            items = _load()
        if path == "/health":
            return self._json(200, {"ok": True,
                                    "pending": sum(1 for i in items if i["status"] == "pending")})
        if path == "/snapshot":
            try:
                with open(SNAPSHOT_FILE, encoding="utf-8") as f:
                    return self._json(200, json.load(f))
            except FileNotFoundError:
                return self._json(200, {"updated": None, "items": []})
        if path == "/pending":
            pend = [i for i in items if i["status"] == "pending"]
            if "basic" in query:   # iPhone 快捷指令只做新增,改期项留给 Mac 消费
                pend = [i for i in pend if i.get("mode") != "reschedule"]
            return self._json(200, pend)
        self._deny(404, "not found")

    def do_POST(self):
        if not self._authed():
            return self._deny(401, "bad token")
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) if "=" in p else (p, "") for p in query.split("&") if p)
        if path == "/claim":
            return self._claim(params)
        data = self._body()
        if data is None:
            return self._deny(400, "bad json")
        if self.path == "/snapshot":
            # Mac 消费器上传当前设备上未来 60 天的未完成提醒清单
            items = data.get("items")
            if not isinstance(items, list) or len(items) > 2000:
                return self._deny(400, "items must be a list (<=2000)")
            snap = {"updated": datetime.now(timezone.utc).isoformat(),
                    "items": [{"title": str(i.get("title") or "")[:200],
                               "due": i.get("due") or None,
                               "list": str(i.get("list") or "")[:50]} for i in items]}
            with _LOCK:
                tmp = SNAPSHOT_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(snap, f, ensure_ascii=False)
                os.replace(tmp, SNAPSHOT_FILE)
            return self._json(200, {"ok": True, "count": len(snap["items"])})
        if self.path == "/push":
            title = str(data.get("title") or "").strip()
            if not title:
                return self._deny(400, "title required")
            op = str(data.get("op") or "add").strip().lower()
            if op not in ("add", "delete"):
                return self._deny(400, "op must be 'add' or 'delete'")
            if op == "delete":
                # 删除条目只需要 title,进独立的 delete 队列,不进 add 队列。
                item = {
                    "id": uuid.uuid4().hex[:12],
                    "created": datetime.now(timezone.utc).isoformat(),
                    "status": "pending",
                    "op": "delete",
                    "title": title,
                    "list": data.get("list") or None,
                    "source": data.get("source") or "unknown",
                    "result": None,
                }
                with _LOCK:
                    ditems = _load(DELETE_QUEUE_FILE)
                    ditems.append(item)
                    _save(ditems, DELETE_QUEUE_FILE)
                return self._json(200, {"id": item["id"]})
            # op == "add"(默认):行为与此前完全一致。
            item = {
                "id": uuid.uuid4().hex[:12],
                "created": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
                "mode": "reschedule" if data.get("replace") else "normal",
                "op": "add",
                "title": title,
                "due": data.get("due") or None,
                "remind": data.get("remind") or None,
                "list": data.get("list") or None,
                "notes": data.get("notes") or None,
                "source": data.get("source") or "unknown",
                "result": None,
            }
            with _LOCK:
                items = _load()
                items.append(item)
                _save(items)
            return self._json(200, {"id": item["id"]})
        if self.path == "/cancel":
            # 把 add 队列里"尚未被认领"的同名条目撤掉。只作用于队列,
            # 碰不到已经落进手机的提醒。返回撤掉的条数。
            title = str(data.get("title") or "").strip()
            if not title:
                return self._deny(400, "title required")
            with _LOCK:
                items = _load()
                keep = [i for i in items if not (i["status"] == "pending" and i["title"] == title)]
                cancelled = len(items) - len(keep)
                if cancelled:
                    _save(keep)
            return self._json(200, {"ok": True, "cancelled": cancelled})
        if self.path == "/ack":
            iid = str(data.get("id") or "")
            with _LOCK:
                items = _load()
                for it in items:
                    if it["id"] == iid and it["status"] == "pending":
                        it["status"] = "done"
                        it["result"] = str(data.get("result") or "")[:500]
                        it["acked"] = datetime.now(timezone.utc).isoformat()
                        _save(items)
                        return self._json(200, {"ok": True})
            return self._deny(404, "id not pending")
        self._deny(404, "not found")

    def log_message(self, fmt, *args):  # 安静点,只留错误
        pass


if __name__ == "__main__":
    os.makedirs(BASE, exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        raise SystemExit(f"缺 token 文件: {TOKEN_FILE}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
