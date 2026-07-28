#!/usr/bin/env bash
# 两机交接一键脚本：pull 拉现场 / save 存现场
# 用法：
#   bash 提醒事项/handoff.sh pull            # 开工：拉另一台的最新进度并显示状态板
#   bash 提醒事项/handoff.sh save "做了啥"    # 收工：提交并推送
#   bash 提醒事项/handoff.sh status          # 只看两边同步状态
set -euo pipefail

BRANCH="claude/teacher-meeting-reminder-yvslkd"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

case "${1:-}" in
  pull)
    echo "== 拉取另一台的最新进度 =="
    git fetch origin
    git pull --rebase origin "$BRANCH"
    echo
    echo "== 当前状态板（提醒事项/进度.md）=="
    sed -n '1,40p' 提醒事项/进度.md
    ;;
  save)
    MSG="${2:-handoff: update progress}"
    echo "== 提交并推送本机进度 =="
    git add -A
    if git diff --cached --quiet; then
      echo "没有可提交的改动。"
    else
      git commit -m "chore(提醒事项): ${MSG}"
    fi
    git push origin "$BRANCH"
    echo "✅ 已推送。另一台机器执行 'handoff.sh pull' 即可无缝接上。"
    ;;
  status)
    git fetch origin
    LOCAL=$(git rev-parse @)
    REMOTE=$(git rev-parse "origin/$BRANCH")
    if [ "$LOCAL" = "$REMOTE" ]; then
      echo "✅ 两边同步：本地与远端一致。"
    else
      echo "⚠️ 不一致 —— 先 pull 再干活。"
      git status -sb | head -3
    fi
    ;;
  *)
    echo "用法: bash 提醒事项/handoff.sh {pull|save \"消息\"|status}"
    exit 1
    ;;
esac
