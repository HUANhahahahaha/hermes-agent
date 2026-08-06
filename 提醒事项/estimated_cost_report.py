#!/usr/bin/env python3
"""从 Hermes state.db 生成可靠的本地预估日报；未知价格绝不按零上报。"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

# Direct execution sets sys.path to this file's subdirectory.  Add the repo
# root so the sibling ``agent`` package resolves regardless of cron's cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

DEFAULT_DB = Path.home() / ".hermes" / "state.db"
DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_RATE = Decimal("7.20")
DEFAULT_BUDGET = Decimal("50")
CENT = Decimal("0.01")
PROVIDER_PLACEHOLDERS = {"", "unknown", "unset", "none", "n/a"}


def _decimal(raw: str, label: str) -> Decimal:
    try:
        value = Decimal(raw)
    except Exception as exc:
        raise ValueError(f"{label} 必须是数字") from exc
    if value <= 0:
        raise ValueError(f"{label} 必须大于零")
    return value


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _new_total() -> dict:
    return {
        "usd": Decimal("0"),
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
        "priced": 0,
        "included": 0,
        "unknown": 0,
        "unknown_routes": set(),
    }


def _price(rows: list[sqlite3.Row]) -> dict:
    total = _new_total()
    for row in rows:
        usage = CanonicalUsage(
            input_tokens=max(0, int(row["input_tokens"] or 0)),
            output_tokens=max(0, int(row["output_tokens"] or 0)),
            cache_read_tokens=max(0, int(row["cache_read_tokens"] or 0)),
            cache_write_tokens=max(0, int(row["cache_write_tokens"] or 0)),
        )
        total["input"] += usage.input_tokens
        total["output"] += usage.output_tokens
        total["cache_read"] += usage.cache_read_tokens
        total["cache_write"] += usage.cache_write_tokens
        if usage.total_tokens == 0:
            continue

        if row["cost_status"] == "actual" and row["actual_cost_usd"] is not None:
            total["usd"] += Decimal(str(row["actual_cost_usd"]))
            total["priced"] += 1
            continue

        provider = (row["billing_provider"] or "").strip()
        # Keep the report independently correct even before the long-running
        # gateway is restarted onto the matching core fix.
        if provider.lower() in PROVIDER_PLACEHOLDERS:
            provider = None
        result = estimate_usage_cost(
            row["model"] or "",
            usage,
            provider=provider,
            base_url=row["billing_base_url"],
        )
        if result.amount_usd is None or result.status == "unknown":
            total["unknown"] += 1
            total["unknown_routes"].add(
                f"{row['billing_provider'] or 'missing'}/{row['model'] or 'unknown'}"
            )
        elif result.status == "included":
            total["included"] += 1
        else:
            total["usd"] += result.amount_usd
            total["priced"] += 1
    return total


def _public(total: dict, rate: Decimal) -> dict:
    return {
        "usd": str(total["usd"]),
        "cny": str(total["usd"] * rate),
        "tokens": {
            "input": total["input"],
            "output": total["output"],
            "cache_read": total["cache_read"],
            "cache_write": total["cache_write"],
        },
        "priced_sessions": total["priced"],
        "included_sessions": total["included"],
        "unknown_sessions": total["unknown"],
        "unknown_routes": sorted(total["unknown_routes"]),
        "incomplete": total["unknown"] > 0,
    }


def build_report(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    timezone_name: str = DEFAULT_TZ,
    rate: Decimal = DEFAULT_RATE,
    budget: Decimal = DEFAULT_BUDGET,
    source: str | None = None,
) -> tuple[str, dict, int]:
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)
    columns = """
      id, source, model, started_at, input_tokens, output_tokens,
      cache_read_tokens, cache_write_tokens, billing_provider,
      billing_base_url, actual_cost_usd, cost_status
    """
    if source:
        rows = list(conn.execute(
            f"SELECT {columns} FROM sessions WHERE started_at >= ? AND source = ?",
            (week_start.timestamp(), source),
        ))
    else:
        rows = list(conn.execute(
            f"SELECT {columns} FROM sessions WHERE started_at >= ?",
            (week_start.timestamp(),),
        ))
    today_rows = [r for r in rows if float(r["started_at"] or 0) >= today_start.timestamp()]
    today, week = _price(today_rows), _price(rows)
    today_cny, week_cny = today["usd"] * rate, week["usd"] * rate
    incomplete = today["unknown"] > 0 or week["unknown"] > 0

    lines = ["📊 clawbot 预估日账单"]
    if incomplete:
        lines.append(
            f"⚠️ 金额不完整：今日已计价 ≈¥{_money(today_cny)} | "
            f"近7天已计价 ≈¥{_money(week_cny)}"
        )
    else:
        lines.append(
            f"今日 ≈¥{_money(today_cny)}（USD {_money(today['usd'])}） | "
            f"近7天 ≈¥{_money(week_cny)}（止损线 ¥{_money(budget)}）"
        )
    lines.append(
        f"今日tokens: 输入{today['input']} 输出{today['output']} "
        f"缓存读{today['cache_read']} 缓存写{today['cache_write']}"
    )
    lines.append(
        f"口径: Anthropic公开价本地预估；固定汇率 USD 1=¥{rate}；"
        "不是Console实际账单"
    )

    exit_code = 0
    if incomplete:
        lines.append(
            f"❌ 今日{today['unknown']}个、近7天{week['unknown']}个会话无法定价；"
            "绝不按 ¥0 上报"
        )
        routes = sorted(today["unknown_routes"] | week["unknown_routes"])
        if routes:
            lines.append("无法定价路由: " + "；".join(routes[:5]))
        exit_code = 2
    elif (
        today["input"] + today["output"] + today["cache_read"] + today["cache_write"] > 0
        and today["usd"] == 0
        and today["included"] == 0
    ):
        lines.append("❌ 检测到非零 token 但金额为零，日报已判定失败")
        exit_code = 2
    elif week_cny >= budget:
        lines.append(f"🔴 近7天达到止损线，超出 ¥{_money(week_cny - budget)}")

    payload = {
        "generated_at": local_now.isoformat(),
        "timezone": timezone_name,
        "source": source,
        "usd_cny": str(rate),
        "budget_cny": str(budget),
        "today": _public(today, rate),
        "seven_days": _public(week, rate),
    }
    return "\n".join(lines), payload, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes 本地 API 消费预估日报")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--timezone", default=os.environ.get("REMINDER_TZ", DEFAULT_TZ))
    parser.add_argument("--usd-cny", default=os.environ.get("USD_CNY_RATE", str(DEFAULT_RATE)))
    parser.add_argument("--budget-cny", default=os.environ.get("COST_BUDGET_CNY", str(DEFAULT_BUDGET)))
    parser.add_argument("--source")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.db.is_file():
            raise FileNotFoundError(f"数据库不存在：{args.db}")
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            text, payload, code = build_report(
                conn,
                now=datetime.now(tz=ZoneInfo(args.timezone)),
                timezone_name=args.timezone,
                rate=_decimal(args.usd_cny, "USD/CNY 汇率"),
                budget=_decimal(args.budget_cny, "预算"),
                source=args.source,
            )
        finally:
            conn.close()
    except Exception as exc:
        print(f"❌ 花费日报生成失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
