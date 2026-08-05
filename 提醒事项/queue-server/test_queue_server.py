#!/usr/bin/env python3
"""提醒队列的隔离回归测试；只使用临时目录和本机随机端口。"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen


SOURCE = Path(__file__).with_name("queue_server.py")


class QueueServerTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.tempdir.name
        try:
            spec = importlib.util.spec_from_file_location("queue_server_under_test", SOURCE)
            assert spec and spec.loader
            self.queue_server = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.queue_server)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

        os.makedirs(self.queue_server.BASE, exist_ok=True)
        Path(self.queue_server.TOKEN_FILE).write_text("test-token\n", encoding="utf-8")
        self.server = self.queue_server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self.queue_server.Handler
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, path, *, method="GET", body=None):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=payload,
            method=method,
            headers={"X-Queue-Token": "test-token", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            return response.status, json.load(response)

    def test_claim_delete_alias_only_consumes_delete_queue(self):
        self.request("/push", method="POST", body={"title": "新增提醒"})
        self.request(
            "/push", method="POST", body={"title": "待删提醒一", "op": "delete"}
        )

        status, claimed = self.request("/claim-delete")
        self.assertEqual(status, 200)
        self.assertEqual(claimed, [{"title": "待删提醒一", "list": "收集桶"}])

        _, pending = self.request("/pending")
        self.assertEqual([item["title"] for item in pending], ["新增提醒"])
        self.assertEqual(self.request("/claim-delete")[1], [])

    def test_query_parameter_route_remains_compatible(self):
        self.request(
            "/push", method="POST", body={"title": "待删提醒二", "op": "delete"}
        )
        _, claimed = self.request("/claim?op=delete")
        self.assertEqual(claimed, [{"title": "待删提醒二", "list": "收集桶"}])


if __name__ == "__main__":
    unittest.main()
