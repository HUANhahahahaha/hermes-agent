#!/usr/bin/env python3
"""从 Anthropic 官方 Usage & Cost Admin API 取真实消费数据。

**为什么用它而不是本地估算**：本地估算要维护价格表，出了新模型就会算不出来
（2026-07 的「日报天天报 0」正是这么来的：定价表缺整个 Claude 5 系列，
未知被当成 0）。这个接口返回的是 Anthropic 自己的账单数字，与 Console
的 Cost 页面同源，不需要维护任何价格。

依赖：**Admin API key**（`sk-ant-admin01-...`，与普通 API key 不同）
  创建：https://platform.claude.com/settings/admin-keys
  ⚠️ Admin API **对个人账号不可用**，需先在
     Console → Settings → Organization 建立组织。

环境变量：
  ANTHROPIC_ADMIN_KEY   必填
  REMINDER_TZ           展示用时区，默认 Asia/Shanghai

用法：
  python3 提醒事项/cost_report.py                # 昨天
  python3 提醒事项/cost_report.py --days 7       # 最近 7 天
  python3 提醒事项/cost_report.py --days 30 --by-model
  python3 提醒事项/cost_report.py --budget 20    # 超预算告警
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

API = "https://api.anthropic.com/v1/organizations"
UA = "hermes-reminders-brain/1.0 (cost-report)"


class CostError(RuntimeError):
    """取数失败。调用方必须如实上报，不得当成 0 元。"""


def _key() -> str:
    k = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not k:
        raise CostError(
            "缺少 ANTHROPIC_ADMIN_KEY。这是 Admin API key（sk-ant-admin01-...），"
            "与普通 API key 不同，创建于 platform.claude.com/settings/admin-keys；"
            "个人账号需先建立组织才能使用。"
        )
    return k


def _get(path: str, params: list[tuple[str, str]]) -> dict:
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "x-api-key": _key(),
        "anthropic-version": "2023-06-01",
        "User-Agent": UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        hint = ""
        if e.code == 401:
            hint = " —— key 无效，或用成了普通 API key（需 sk-ant-admin01-）"
        elif e.code == 403:
            hint = " —— 权限不足；个人账号需先在 Console 建立组织"
        raise CostError(f"HTTP {e.code}{hint}\n{body}") from None
    except Exception as e:  # noqa: BLE001
        raise CostError(f"{type(e).__name__}: {e}") from None


def _paged(path: str, params: list[tuple[str, str]]) -> list[dict]:
    """跟着 has_more / next_page 取全。"""
    out, page = [], None
    while True:
        p = list(params) + ([("page", page)] if page else [])
        data = _get(path, p)
        out.extend(data.get("data", []))
        if not data.get("has_more"):
            return out
        page = data.get("next_page")
        if not page:
            return out


def fetch_cost(start: datetime, end: datetime, by_model: bool) -> list[dict]:
    params = [
        ("starting_at", start.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("ending_at", end.strftime("%Y-%m-%dT%H:%M:%SZ")),
    ]
    # 按 description 分组时响应会带上解析出的 model 字段
    params.append(("group_by[]", "description" if by_model else "workspace_id"))
    return _paged("/cost_report", params)


def _amount(item: dict) -> Decimal:
    """成本以「最小单位的十进制字符串」返回（美分），换算成美元。"""
    for k in ("amount", "cost", "value", "amount_decimal"):
        v = item.get(k)
        if v is not None:
            try:
                return Decimal(str(v)) / Decimal(100)
            except Exception:  # noqa: BLE001
                continue
    return Decimal(0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Anthropic 真实消费报表")
    ap.add_argument("--days", type=int, default=1, help="回溯天数（默认 1＝昨天）")
    ap.add_argument("--by-model", action="store_true", help="按模型拆分")
    ap.add_argument("--budget", type=float, default=None,
                    help="预算（美元）；超出则告警并以退出码 2 返回")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args(argv)

    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                             microsecond=0)
    start = end - timedelta(days=args.days)

    try:
        rows = fetch_cost(start, end, args.by_model)
    except CostError as e:
        # 关键：取不到就说取不到，绝不报 0
        print(f"❌ 取消费数据失败：{e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    total = Decimal(0)
    by_day: dict[str, Decimal] = defaultdict(Decimal)
    by_key: dict[str, Decimal] = defaultdict(Decimal)

    for bucket in rows:
        day = str(bucket.get("starting_at", ""))[:10]
        for item in bucket.get("results", []) or []:
            amt = _amount(item)
            total += amt
            by_day[day] += amt
            label = (item.get("model") or item.get("description")
                     or item.get("workspace_id") or "（未分类）")
            by_key[str(label)] += amt

    span = "昨天" if args.days == 1 else f"最近 {args.days} 天"
    print(f"💰 Anthropic 实际消费 · {span}"
          f"（{start:%Y-%m-%d} ~ {end:%Y-%m-%d} UTC）")
    print("=" * 46)

    if not rows or total == 0:
        print("\n本期无消费记录。")
        print("（若你确信有用量，请检查 Admin key 所属组织是否正确；"
              "数据通常在请求完成后 5 分钟内出现）")
        return 0

    print(f"\n总计：${total:.2f}")

    if len(by_day) > 1:
        print("\n按日：")
        for d, v in sorted(by_day.items()):
            bar = "█" * min(int(v * 4), 40)
            print(f"  {d}  ${v:6.2f} {bar}")

    print(f"\n按{'模型' if args.by_model else '工作区'}：")
    for label, v in sorted(by_key.items(), key=lambda x: -x[1]):
        pct = (v / total * 100) if total else 0
        print(f"  {label[:38]:38} ${v:6.2f}  {pct:5.1f}%")

    if args.budget is not None:
        print()
        if total > Decimal(str(args.budget)):
            over = total - Decimal(str(args.budget))
            print(f"🔴 超预算！预算 ${args.budget:.2f}，超出 ${over:.2f}")
            return 2
        left = Decimal(str(args.budget)) - total
        print(f"✅ 预算内：${args.budget:.2f}，还剩 ${left:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
