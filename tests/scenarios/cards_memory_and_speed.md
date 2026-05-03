# Cards / memory / watchdog alerts / speed (R79-R88 push)

Coverage for the boss-directed 10-round push (rounds 79-89, 2026-05-04).

## 场景

The push delivered five operator-visible behaviors on top of the
existing rebuild:
1. Slash replies (/help, /team, /health) now post Feishu **interactive
   cards** with health-aware header colours instead of plain text blobs.
2. Watchdog **posts to Feishu chat** when a supervised daemon enters
   cooldown (max_retries failed respawns).
3. Each agent has a **durable memory** file under `facts/<agent>/memory.jsonl`
   that survives `/clear` and pane restart. Memory auto-injects into the
   identity init prompt on next wake.
4. New top-level `claudeteam remember <agent> <kind> "<content>" [--ref X]`
   command lets agents write memory entries from inside their tmux pane.
5. **lark-cli send latency 73s → 0.6s** by bypassing `npx`'s package-lookup
   overhead in favour of the direct binary in `~/.npm/_npx/<hash>/.bin/`
   (or whatever `which lark-cli` returns when `npm i -g @larksuite/cli`).

Plus identity v2: manager body ported management discipline from old
main (角色边界 / 秒回闭环 / 巡视核实 / 集合指令必须 dispatch / 沟通格式 / 需求纪律
/ 外部系统). Worker body teaches `remember` + memory-vs-log distinction.

## 范围

- 类型：host-live (Feishu) + local
- 凭证：a working `lark-cli` profile that's a member of the chat
- 操作员：boss / 任一开发者

## Given

- `claudeteam up` started a team with at least 1 agent (manager + ≥0 workers).
- `runtime_config.json` carries `chat_id` + `lark_profile`.
- Direct `lark-cli` binary is on disk (either `which lark-cli` returns
  one, or `~/.npm/_npx/<hash>/node_modules/.bin/lark-cli` exists from
  a previous `npx @larksuite/cli ...` run).

## When — slash card replies

Send `/help` in the Feishu group from any account. Within ~1 second
(NOT ~73s anymore — see below) you should see:

- A Feishu **interactive card**, NOT a plain message
- Header in **blue**, title `🆘 ClaudeTeam 自定义斜杠命令`
- Body listing every `/<cmd>` with description (lark_md formatted)

`/team` → green card if all agents are 💤 idle / 🔄 working, yellow
if any show ⚠️/🛑/❌; body has `<emoji> **<agent>**: <brief>` lines.

`/health` → green card if `claudeteam health` output has no ❌/⚠️ glyph,
yellow otherwise. Body fenced in code-block so glyph alignment carries
through Feishu's lark_md.

## When — watchdog cooldown alert

Force-fail the router so watchdog enters cooldown (3 failed respawns by
default). The simplest reproduction:

```bash
# Make `claudeteam router` fail at startup (corrupt runtime_config)
mv runtime_config.json runtime_config.json.bak
claudeteam router  # exits non-zero immediately, no pid file

# Wait one watchdog supervise cycle (~30s) — watchdog will try to
# respawn, fail; on the 3rd consecutive failure across cycles it
# enters 600s cooldown AND posts:
#
#   🚨 watchdog: daemon router entered 600s cooldown after 3 failed
#   respawns. `claudeteam health` for current state; check daemon log
#   for root cause.
```

Restore `runtime_config.json` to recover.

## When — memory write + recall

```bash
# Manager remembers a decision, references the originating message
claudeteam remember manager decision "use bcrypt for password hashing" --ref om_xx

# Worker remembers a blocker
claudeteam remember worker_cc blocker "missing GH PAT for push" --ref T-9

# After /clear or `claudeteam reidentify <agent>`, the next init
# prompt the agent reads will include all stored memory entries
# under the "## 既往记忆（按时间）" section.
```

Verify roundtrip:
```bash
python3 -c "
from claudeteam.store import memory
print(memory.render_for_prompt('manager'))
"
```

## When — speed sanity check

```bash
# Time a small lark-cli call. Should be ~0.6s on macOS host with
# direct binary; was ~73s before R86 when we used `npx` blindly.
time lark-cli --profile <name> im +chat-search --as bot --query x

# If still 73s, the resolver fell through to `npx` because no direct
# binary exists. Fix:
npm install -g @larksuite/cli
# Or set CLAUDETEAM_LARK_CLI_BIN=/path/to/lark-cli explicitly.
```

## Then — verification table

| Behaviour | Expected | Sign of failure |
| --- | --- | --- |
| `/help` in chat | Interactive card, blue header | Plain-text reply (smoke deploy still on old src; rsync src + restart router) |
| `/team` health colour | Green if all healthy, yellow if any ⚠️/🛑/❌ | Always blue → handler returning str instead of dict (slash.py drift) |
| `/health` body | Code-fenced raw `claudeteam health` output | Empty / unfenced — _shell stderr, check claudeteam on PATH |
| Watchdog cooldown | Feishu message starting `🚨 watchdog:` | No msg → `_make_alert_fn` returned None (chat_id unset) or send failed (check `lark-cli profile list`) |
| memory.jsonl | One JSON record per `claudeteam remember` | Missing — agent dir not created (ensure facts_dir() reachable) |
| init_prompt with memory | `## 既往记忆` block visible after reidentify | Block missing → `memory.render_for_prompt` returned empty (memory.jsonl missing or all entries dropped past 200 cap) |
| /help round-trip < 2s | ~0.6-1.5s end-to-end | ≥30s → `_resolve_cli_prefix` falling through to npx (no direct binary on disk) |

## 反例

- Bot not a chat member → `/help` send fails with code 230002. Resolver picks the right binary, but Feishu rejects. Add bot to chat, retry.
- Profile token expired → send returns `need_user_authorization` for `--as user`; for `--as bot`, may still work if bot perms are intact. Set `CLAUDETEAM_LARK_SEND_AS=bot` in env.
- Router daemon predates the rsync → it's still running OLD slash.py without dict-return support. Symptom: cards arrive as plain text. Fix: `claudeteam down && up` to force daemon reload (Python doesn't hot-reload).
- `claudeteam remember` writes to `~/.claudeteam` instead of project state → `CLAUDETEAM_STATE_DIR` env not set in the calling shell. Identity init prompt's `cd` rule prevents this for spawned panes; risk is when an operator runs the command from a different cwd.

## Out of scope

- Reverse memory pruning (operator deciding to drop one entry from the middle): there's no `claudeteam forget <id>` yet; only `clear(agent)` for the whole file.
- Cards with buttons / actions: this push only adds static info cards.
  Action-buttons would require an event handler at the router level for
  `card.action.trigger` events.
- Multi-platform direct-binary auto-install: R86 picks an existing
  binary; if none is present anywhere, falls back to `npx` (works but
  slow). Operators on a clean machine should `npm i -g @larksuite/cli`.
