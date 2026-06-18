# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Hermes Agent is a self-improving AI agent (Python 3.11–3.13) with a terminal UI, a
multi-platform messaging gateway, a tool/toolset system, a skills system, cron
scheduling, and pluggable model/memory/context providers. It runs across six terminal
backends (local, Docker, SSH, Singularity, Modal, Daytona) and many chat platforms.

**`AGENTS.md` is the canonical, deep development guide** — it documents the agent loop,
CLI/TUI/desktop architecture, plugins, skills, toolsets, delegation, curator, cron,
kanban, profiles, and a long list of known pitfalls. Read it before non-trivial work.
`CONTRIBUTING.md` covers the PR process and the "skill vs tool" decision. This file is
the orientation layer; defer to those two for specifics.

## Commands

Setup (one time):

```bash
./setup-hermes.sh           # installs uv, creates .venv, installs .[all], symlinks hermes
# or manually:
uv venv .venv --python 3.11 && source .venv/bin/activate && uv pip install -e ".[all,dev]"
```

Tests — **always use the wrapper, never bare `pytest`.** It enforces CI parity
(unset credential env vars, TZ=UTC, LANG=C.UTF-8, per-file subprocess isolation):

```bash
scripts/run_tests.sh                                   # full suite
scripts/run_tests.sh tests/agent/                      # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x   # one test
scripts/run_tests.sh tests/foo.py -- --tb=long         # args after `--` pass through to pytest
scripts/run_tests.sh --no-isolate tests/foo/           # disable per-test isolation (faster debugging)
```

Integration tests are excluded by default (`-m 'not integration'` in `pyproject.toml`).
Each test runs in a freshly-spawned `spawn` subprocess, so module-level state can't leak
between tests; there is no global state-reset fixture.

Lint / typecheck (CI gates — `ruff check .` is blocking and merge-gating):

```bash
ruff check .                # blocking lint (only PLW1514 is enabled — see below)
ty check                    # type checker (advisory diff in CI)
python scripts/check-windows-footguns.py   # blocking: flags Windows-unsafe primitives
```

Run the agent / CLI locally:

```bash
./hermes                    # auto-detects the venv; interactive CLI
python run_agent.py --help  # direct agent entry point
hermes doctor               # diagnose environment issues
```

TUI (Node/Ink frontend, lives in `ui-tui/`):

```bash
cd ui-tui
npm install
npm run dev        # watch mode      npm run build      # full build
npm run type-check # tsc --noEmit     npm run lint       # eslint
npm test           # vitest
```

## Architecture (big picture)

Two entry points, one agent core. `hermes` (CLI/subcommands, `hermes_cli/`) and the
messaging gateway (`gateway/`) both drive the same `AIAgent` in `run_agent.py`.

**Core loop — `run_agent.py` (`AIAgent`, ~12k LOC).** `run_conversation()` runs a
synchronous tool-calling loop: call the model → dispatch each tool call via
`handle_function_call()` → append results → repeat until no tool calls or the iteration
budget runs out. Messages are OpenAI-format dicts; reasoning lives in
`assistant_msg["reasoning"]`. Agent-level tools (todo, memory) are intercepted in
`run_agent.py` before `handle_function_call()`.

**Tool system — import-time registration.** Any `tools/*.py` with a top-level
`registry.register(...)` call (`tools/registry.py`) is auto-discovered. But a registered
tool is only *exposed to an agent* when its name appears in a toolset in `toolsets.py`
(`_HERMES_CORE_TOOLS` is the default bundle). All tool handlers MUST return a JSON
string. Dependency chain: `tools/registry.py` ← `tools/*.py` ← `model_tools.py` ←
`run_agent.py`/`cli.py`/`batch_runner.py`. **Prefer the plugin route over editing core
tools** — most new capabilities should be skills or plugins, not built-in tools.

**Slash commands are registry-driven.** `hermes_cli/commands.py` holds a single
`COMMAND_REGISTRY` of `CommandDef`s; CLI dispatch, gateway dispatch, `/help`, the
Telegram/Slack menus, and autocomplete are all derived from it. Adding a command touches
the registry plus a handler in `cli.py` (and `gateway/run.py` if gateway-available);
adding an alias touches only the `aliases` tuple.

**TUI vs Desktop vs Dashboard** (all distinct — see AGENTS.md before touching):
- `ui-tui/` (Ink/React) ↔ `tui_gateway/` (Python JSON-RPC over stdio) is the modern TUI.
- `hermes dashboard` *embeds the real `hermes --tui`* over a PTY/WebSocket — do not
  reimplement the transcript/composer in React.
- `apps/desktop/` (Electron) is a *separate* chat surface with its own pipeline.

**Extensibility surfaces** (each its own discovery path, all under `plugins/`):
general plugins (`hermes_cli/plugins.py`, lifecycle hooks + tools + CLI subcommands),
model-provider plugins (`plugins/model-providers/`, lazy `providers/__init__.py`
discovery), memory-provider plugins (`plugins/memory/`, `agent/memory_manager.py` —
**closed set in-tree; new ones ship as standalone repos**), and context-engine /
image-gen providers. **Plugins must not modify core files** — extend the generic plugin
surface instead.

**Other major subsystems:** `skills/` (built-in) + `optional-skills/` (niche, installed
on demand) procedural memory with the `agent/curator.py` lifecycle manager; `cron/`
scheduled jobs; `plugins/kanban/` + `tools/kanban_tools.py` multi-agent work queue;
`tools/delegate_tool.py` synchronous subagents; `hermes_state.py` SQLite session store
with FTS5 search.

## Conventions that bite

- **Profiles: never hardcode `~/.hermes`.** Use `get_hermes_home()` (state paths) and
  `display_hermes_home()` (user-facing strings) from `hermes_constants`. Hardcoded paths
  break the multi-instance profile system. Tests must not write to real `~/.hermes/` —
  the `_isolate_hermes_home` autouse fixture redirects `HERMES_HOME`; profile tests must
  also mock `Path.home()`.
- **Don't break prompt caching.** Do not alter past context, change toolsets, or rebuild
  system prompts mid-conversation (only compression may). Cache-mutating slash commands
  default to deferred invalidation with an opt-in `--now`.
- **Dependencies are exact-pinned** in `pyproject.toml` (supply-chain hardening). Core
  `dependencies` are only packages every session needs; provider/backend-specific deps
  live in extras and lazy-install via `tools/lazy_deps.py`. Bump a pin → run `uv lock`.
  Never add a bare `>=` without a ceiling.
- **Config vs secrets.** Non-secret settings go in `config.yaml` (`DEFAULT_CONFIG` in
  `hermes_cli/config.py`); only API keys/tokens go in `.env` (`OPTIONAL_ENV_VARS`). Note
  the three config loaders (`load_cli_config`, `load_config`, raw gateway YAML) — adding
  a key to the wrong one makes it visible to CLI but not gateway, or vice versa.
- **Always specify `encoding=` on file I/O.** Bare `open()`/`read_text()`/`write_text()`
  default to locale encoding (cp1252 on Windows) and corrupt non-ASCII content. This is
  the one enabled ruff rule (`PLW1514`) and it is merge-gating. Avoid POSIX-only
  primitives flagged by `scripts/check-windows-footguns.py` (`os.kill(pid, 0)`,
  `os.setsid`, `signal.SIGKILL` without fallback, etc.).
- **No change-detector tests.** Don't assert snapshots of data expected to change (model
  catalogs, config version numbers, enumeration counts). Assert relationships/invariants
  instead. See the AGENTS.md "Testing" section for examples.
- `simple_term_menu` is legacy-only; new interactive menus use `hermes_cli/curses_ui.py`.

## Git workflow

Develop on a feature branch (never push directly to `main`). Run the full test suite
(`scripts/run_tests.sh`) before pushing. Do not open a PR unless explicitly asked.
