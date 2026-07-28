#!/bin/bash
# 收工检查：提醒事项/ 有改动却没推送时，出声提醒（不自动推送，推送由你确认）。
# 只读检查，永不阻塞。
set -uo pipefail

BRANCH="claude/teacher-meeting-reminder-yvslkd"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
cd "$ROOT" 2>/dev/null || exit 0

CUR=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
[ "$CUR" = "$BRANCH" ] || exit 0

dirty=0
git diff --quiet -- 提醒事项/ || dirty=1
git diff --cached --quiet -- 提醒事项/ || dirty=1
[ -n "$(git ls-files --others --exclude-standard 提醒事项/ 2>/dev/null)" ] && dirty=1

ahead=$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)

if [ "$dirty" = "1" ]; then
  echo "⚠️ 交接提醒：提醒事项/ 有改动还没保存。换机器前请说一句「保存进度」，或跑：bash 提醒事项/handoff.sh save \"这次做了啥\""
elif [ "${ahead:-0}" != "0" ]; then
  echo "⚠️ 交接提醒：有 $ahead 个提交还没推送。换机器前请说「推送进度」，或跑：git push origin $BRANCH"
fi
exit 0
