#!/bin/bash
# 开会话自动交接：拉最新现场 + 装依赖 + 显示状态板。
# 任何一步失败都不阻塞会话（全部 || true）。
set -uo pipefail

BRANCH="claude/teacher-meeting-reminder-yvslkd"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
cd "$ROOT" 2>/dev/null || exit 0

CUR=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ "$CUR" = "$BRANCH" ]; then
  git fetch origin "$BRANCH" >/dev/null 2>&1 || true
  if git diff --quiet && git diff --cached --quiet; then
    git pull --rebase origin "$BRANCH" >/dev/null 2>&1 || true
    echo "✅ 已自动拉取另一台机器的最新进度。"
  else
    echo "⚠️ 本机有未提交改动，跳过自动 pull（避免冲突）。"
  fi
fi

# 提醒事项 CalDAV 桥依赖——只在云端装，别动你 Mac 的 Python 环境
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  pip install -q caldav icalendar >/dev/null 2>&1 || true
fi

echo "==== 提醒事项 · 当前状态板 ===="
sed -n '1,20p' "$ROOT/提醒事项/进度.md" 2>/dev/null || echo "(未找到 提醒事项/进度.md)"
exit 0
