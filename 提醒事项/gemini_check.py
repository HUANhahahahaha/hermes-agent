#!/usr/bin/env python3
"""Gemini 接入自检：一条命令确认 key、网络、模型全部就绪。

用法：
    python3 提醒事项/gemini_check.py

依次检查：
  1. GEMINI_API_KEY / GOOGLE_API_KEY 是否注入
  2. generativelanguage.googleapis.com 是否在网络白名单里
  3. 真实调用一次 gemini-3.1-pro-preview，打印回复与 token 用量
每步失败都会明确告诉你是哪一环、怎么修。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

HOST = "generativelanguage.googleapis.com"
MODEL = os.environ.get("GEMINI_CHECK_MODEL", "gemini-3.1-pro-preview")


def fail(step: str, msg: str, fix: str) -> None:
    print(f"\n❌ {step} 失败：{msg}\n   → 怎么修：{fix}")
    sys.exit(1)


def main() -> None:
    # ── 1. 凭据 ────────────────────────────────────────────────────────
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        fail(
            "1/3 凭据",
            "没读到 GEMINI_API_KEY（也没有 GOOGLE_API_KEY）",
            "在 Update cloud environment → Environment variables 里加一行 "
            "GEMINI_API_KEY=你的key，保存后**开新会话**（旧会话读不到）。",
        )
    print(f"✅ 1/3 凭据就绪（key 长度 {len(key)}，来源 "
          f"{'GEMINI_API_KEY' if os.environ.get('GEMINI_API_KEY') else 'GOOGLE_API_KEY'}）")

    # ── 2 & 3. 网络 + 真实调用 ────────────────────────────────────────
    url = (f"https://{HOST}/v1beta/models/{MODEL}:generateContent"
           f"?key={key}")
    body = json.dumps({
        "contents": [{"parts": [{"text": "回复两个字：就绪"}]}]
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        if "not in allowlist" in detail.lower():
            fail("2/3 网络", f"{HOST} 未放行",
                 "Network access 选 Custom，Allowed domains 加一行 "
                 f"{HOST}，保存后**开新会话**。")
        if e.code in (400, 403):
            fail("3/3 调用", f"HTTP {e.code} — {detail}",
                 "多半是 key 无效/未启用 Gemini API，或该模型你的账号还没开通。"
                 " 去 https://aistudio.google.com/apikey 确认，"
                 " 或换 GEMINI_CHECK_MODEL=gemini-3.6-flash 再试。")
        fail("3/3 调用", f"HTTP {e.code} — {detail}", "见上方错误详情。")
    except Exception as e:  # noqa: BLE001 - 自检脚本，任何异常都要给出人话
        fail("2/3 网络", f"{type(e).__name__}: {e}",
             f"确认 Allowed domains 含 {HOST} 且已开新会话。")

    print(f"✅ 2/3 网络已放行 {HOST}")

    text = ""
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            text += part.get("text", "")
    usage = data.get("usageMetadata", {})
    print(f"✅ 3/3 调用成功（模型 {MODEL}）")
    print(f"   回复：{text.strip()!r}")
    if usage:
        print(f"   token：输入 {usage.get('promptTokenCount')} / "
              f"输出 {usage.get('candidatesTokenCount')} / "
              f"合计 {usage.get('totalTokenCount')}")
    print("\n🎉 Gemini 全链路打通，可以开始用了。")


if __name__ == "__main__":
    main()
