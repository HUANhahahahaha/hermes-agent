---
name: icloud-reminders-caldav
description: "Apple Reminders without a Mac — add/list reminders via iCloud CalDAV."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  env_vars: [ICLOUD_USERNAME, ICLOUD_APP_PASSWORD]
  commands: [python3]
metadata:
  hermes:
    tags: [Reminders, tasks, todo, Apple, iCloud, CalDAV, iPhone, cross-platform]
    category: apple
    related_skills: [apple-reminders]
---

# iCloud Reminders (CalDAV) — Apple Reminders without a Mac

Write to and read from the Apple **Reminders** app over iCloud's CalDAV API.
Items sync to the user's iPhone/iPad/Mac. Unlike the `apple-reminders` skill
(which needs `remindctl` running **on a Mac**), this works from any Linux /
cloud / Windows host — only an Apple ID + app-specific password are required.

## When to use this vs `apple-reminders`

- **Use this** when there is no Mac in the loop (cloud agent, Linux box, WeChat
  bridge, cron job) but the user still wants items in their iPhone Reminders.
- **Use `apple-reminders`** when running directly on the user's Mac with
  `remindctl` installed (richer commands: complete, delete, edit).

## Setup

1. Generate an **app-specific password**: appleid.apple.com → 登录与安全 /
   Sign-In & Security → App-Specific Passwords. (A normal Apple ID password
   will **not** work for CalDAV.)
2. Set environment variables (via `hermes setup` or your env config):
   - `ICLOUD_USERNAME` — your Apple ID email
   - `ICLOUD_APP_PASSWORD` — the app-specific password above
   - `REMINDER_LIST` *(optional)* — default list name (defaults to `ifoon@me.com`)
   - `REMINDER_TZ` *(optional)* — timezone, defaults to `Asia/Shanghai`
3. Install dependencies (stdlib + two packages):
   ```bash
   pip install caldav icalendar
   ```
4. Allowlist the host if your environment restricts network egress:
   `caldav.icloud.com` and `*.caldav.icloud.com`.

## Usage

Resolve the script path, then call subcommands:

```bash
SCRIPT=$(find ~/.hermes -path '*skills/apple/icloud-reminders-caldav/scripts/icloud_reminders.py' 2>/dev/null | head -1)
# fallback when running from the repo checkout:
[ -z "$SCRIPT" ] && SCRIPT=skills/apple/icloud-reminders-caldav/scripts/icloud_reminders.py

python3 "$SCRIPT" lists                              # show writable reminder lists
python3 "$SCRIPT" show --list "ifoon@me.com"         # list open reminders
python3 "$SCRIPT" add --title "买牛奶"                # quick add to default list
python3 "$SCRIPT" add \
  --title "孙建亚老师 家前采时间确认（绿洲比华利花园931号楼）" \
  --due "2026-07-02 10:00" --alarm "2026-07-02 09:00" \
  --notes "采访前与老师确认家前采具体时间"
```

Subcommands: `lists`, `add`, `show`. Run `python3 "$SCRIPT" <cmd> --help` for flags.

### `add` flags

| Flag | Meaning |
|------|---------|
| `--title` | Reminder text (required) |
| `--list` | Target list name (default: `$REMINDER_LIST` → `ifoon@me.com`) |
| `--due` | Due date/time: `YYYY-MM-DD` or `YYYY-MM-DD HH:mm`, or `today`/`tomorrow`/`今天`/`明天` |
| `--alarm` | Early notification time, e.g. `2026-07-02 09:00` |
| `--notes` | Optional body text |

## Date formats

`today` / `tomorrow` / `今天` / `明天`, `YYYY-MM-DD`, `YYYY-MM-DD HH:mm`.
Times are interpreted in `REMINDER_TZ` (default `Asia/Shanghai`).

## iCloud quirks this skill already handles

These are real CalDAV gotchas the script works around — don't reintroduce them:

1. **VALARM triggers must be relative.** iCloud rejects an absolute `DATE-TIME`
   alarm trigger with `403 Forbidden`. The script converts `--alarm` into a
   `DURATION` offset relative to `--due` automatically.
2. **`cal.todos()` 500s on iCloud.** The python-caldav `todos()` helper emits a
   time-range `REPORT` that iCloud answers with `500`. `show` uses
   `search(todo=True)` and filters completed / empty items client-side instead.
3. **Some lists are read-only.** `lists` only prints VTODO-capable lists; a
   write to a shared/read-only list returns `403`. Default writes go to
   `ifoon@me.com` (a confirmed writable list on this account).

## Security

Credentials are read **only** from environment variables — never hardcode the
app-specific password or commit it. The app password grants calendar/reminders
access only and can be revoked anytime from appleid.apple.com.

## Rules

1. When the user says "提醒我 / remind me", confirm whether they want an Apple
   Reminder (syncs to phone) vs an agent cronjob alert.
2. Confirm title + due time before writing.
3. Run `lists` first if unsure of the exact list name on the account.
