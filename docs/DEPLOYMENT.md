<p align="center">
  <b>English</b> · <a href="DEPLOYMENT_zh.md">简体中文</a> · <a href="DEPLOYMENT_docker.md">Docker →</a>
</p>

# Deployment Guide (Host)

Get a ClaudeTeam crew running — **just follow the 4 steps below, top to bottom**.
Config, model-backend, and troubleshooting reference live further down. Deploying
on Docker / a server → see [Docker deploy](DEPLOYMENT_docker.md).

> **Driving this with a coding agent?** Tell it: *read this doc, walk me through
> it; when there's a choice (which agent CLIs? do I already have a Feishu app?)
> **ask me, don't guess**.*

---

## Before you begin

Install these (the bits `pip` can't):

- **Python 3.9+** — macOS's built-in `/usr/bin/python3` (3.9) is fine, nothing
  extra to install. Debian/Ubuntu also needs `sudo apt install -y python3-venv`.
- **tmux** — one window per agent.
- **node + npx (18+)** — runs `lark-cli` (sending) + the Feishu sidecar (bot
  registration + event ingress).
- **≥ 1 agent CLI** — `claude` alone is enough (the default team uses only it);
  mixing in `codex` / `gemini` / `qwen` / … is **optional** (see the
  [adapter table](../README.md#multi-cli-adapter)).
- A **Feishu / Lark account** — `--quick` scan-registers anywhere (you @ the bot in
  groups); for "un-@'d in groups", drop `--quick` and let the browser automation build an
  enterprise self-built app (needs a desktop browser).

> 💡 Agents **reuse your existing local login**: if `claude` is logged in on this
> machine, the claude agents use it directly — **no separate login**. Same for any
> other CLI — logged in locally is enough.

---

## Step 1 · Install

```bash
# Code + the claudeteam command (-e = editable install: always tracks your
# checkout, never stuck on a stale version)
git clone https://github.com/zylMozart/ClaudeTeam.git && cd ClaudeTeam
python3 -m venv .venv && source .venv/bin/activate    # macOS's built-in 3.9 is fine
pip install -e .

# External tools pip can't install:
#   macOS:  brew install tmux node && npm i -g @larksuite/cli @anthropic-ai/claude-code
#   Debian: sudo apt install -y tmux nodejs npm && npm i -g @larksuite/cli @anthropic-ai/claude-code
```

> Install only the agent CLIs you'll use. The default team is all `claude-code`,
> so `claude` alone runs it; add `codex` etc. only if you want them.

## Step 2 · Configure your team

```bash
claudeteam init --no-connect      # writes claudeteam.toml (default: manager + 1 claude worker)
$EDITOR claudeteam.toml           # adjust agents to the CLIs you have / are logged into (below)
```

Open `claudeteam.toml`; `[team.agents.*]` is your roster. The default is two
`claude-code` agents — **install claude and it just works**. To add a worker on
another CLI (only if you've **installed + logged into** it), uncomment the example
init wrote and edit it:

```toml
[team.agents.worker_codex]
cli   = "codex-cli"     # add only if you have codex installed; otherwise leave it out
model = "gpt-5.5"
role  = "Codex worker"
```

> Don't want to configure from scratch? [`templates/`](../templates/) has ready
> domain teams (software-dev / research / marketing / data / content) — copy one
> and tweak. `claudeteam reidentify <agent> --print` previews an agent's rendered
> identity before `up`.

## Step 3 · Connect Feishu (scan once → bot + group built)

```bash
claudeteam feishu connect --quick     # one scan, runs anywhere (you @ the bot in groups)
```

**`--quick` is the easy path**: scan one QR, zero console, **runs on any machine** (incl.
headless servers). It creates the bot app + team group (invites you) + creds + `chat_id`
(written back to `claudeteam.toml`). The one catch: a **PersonalAgent** app can't get
`im:message.group_msg`, so **in groups you @ the bot** to get a reply — DMs are unaffected,
and it's fine to start here.

**Want the bot to reply in groups _without_ an @?** Drop `--quick`:

```bash
claudeteam feishu connect             # browser-builds an enterprise app that needs no @
```

With no flag it opens a **real (headed) browser** and drives the Feishu console to create +
scope + subscribe + **publish** an enterprise self-built app holding `im:message.group_msg`
— then the bot **replies to un-@'d group messages**. Scan the login QR **once**; the 7
console stages auto-run; on any console-UI change it **falls back to `--manual`**. Needs a
**desktop browser** (see the headless note below). There's also **`--manual`** — the
step-by-step guided console flow (paste App ID/Secret, click the one-click permission
deep-link, publish) — the robust fallback when the browser automation can't run.

> ⚠️ **Headless servers:** the no-flag browser automation needs a desktop browser, so it
> **can't run on a headless host** — either use `--quick` (you @ the bot in groups), or run
> `claudeteam feishu connect` on a **desktop machine** to build the app + group, then copy
> the saved creds (`state/feishu_app.json`) + `chat_id` into the server's config. (Headless +
> terminal-QR is planned.)

<details>
<summary><b><code>--manual</code> guided console flow</b> (if the browser automation can't run or you'd rather click it yourself)</summary>

`claudeteam feishu connect --manual` walks you through the console:

1. **Create the app** — open <https://open.feishu.cn/app> → 创建企业自建应用 → add
   the **机器人 (bot)** capability → copy the **App ID + App Secret**, paste when prompted.
2. **One-click scopes** — click the deep-link it prints (all 7 scopes incl. the
   sensitive `im:message.group_msg` pre-selected) → 确认.
3. **Event** — 事件与回调 → 订阅方式 = **使用长连接** → add the **接收消息** event.
4. **Publish** — 应用发布 → 创建版本 → 申请发布 → **批准** (tenant admins approve their own version instantly; personal-edition apps skip review).
5. Press **Enter** — the command verifies the scope, creates the group, saves creds → `state/feishu_app.json` (0600) + writes `chat_id`.

</details>

> Want one command for Steps 2+3 (default team, no agent edits)?
> `claudeteam init --quick` — writes the default config and scan-connects Feishu in one go
> (use `claudeteam init` for the no-@ browser flow).

## Step 4 · Launch + verify

```bash
claudeteam install-hooks      # install slash-command hooks (MUST run before up)
claudeteam up                 # start the tmux crew + router + watchdog
claudeteam health             # infra self-check: binaries / env / tmux / router / watchdog
```

**The real signal is your Feishu group**: on a fresh `up` the manager **posts a
roll-call** and each worker reports in. See that = you're up. Then `@manager 你好`
→ reply in ~30 s.

> ⚠️ **Green `health` ≠ a working team** — it checks infrastructure (processes /
> tmux / daemons), not whether each agent's CLI is actually authenticated. **Go by
> the group roll-call.** No response? Usually an agent CLI isn't logged in on this
> machine (run `claude` to log in) or that CLI isn't installed. Optional manual
> probes (type in the group): `/health` (per-agent + router + watchdog card),
> `/team` (each agent's ♥ heartbeat < 30 s).

**Tear down:** `claudeteam down` (stop, keep state) · `claudeteam reset` (also wipe state).

Optional: if you want to add ECC to ClaudeTeam, prefer installing it
into ClaudeTeam's isolated per-agent homes rather than the operator's
real global CLI homes. See
[`docs/ecc-minimal-integration.md`](ecc-minimal-integration.md).

---

## Configuration: `claudeteam.toml`

Single TOML file (Cargo-style, comment-friendly) — `claudeteam init` writes it,
documented in-place. App creds are **not** here (they live in
`state/feishu_app.json`); only `chat_id` + the team layout.

```toml
chat_id      = "oc_..."                       # Feishu group chat_id (written by `feishu connect`)
lark_profile = ""                             # lark-cli profile name; "" = default
default_model = "opus"                        # fallback when an agent doesn't pin one

[team]
session = "ClaudeTeam"                        # tmux session name

[team.agents.manager]
cli = "claude-code"                           # claude-code | codex-cli | gemini-cli | kimi-code | qwen-code
                                              #   | minimax | opencode | codewhale | openclaw | trae | hermes | pi
role = "团队主管"                             # rendered into identity.md
model = "opus"
provider_preset = "cheap-translate"          # optional — per-agent provider preset name
specialty  = ["调度", "审阅"]                 # optional — manager sees this in dispatch prompt
tone       = "稳重克制"                       # optional — biases LLM tone
notes      = "always answer in Chinese"       # optional — free-form prompt addendum
playbook   = "manager.md"                     # optional — a role-instruction .md (→ its CLAUDE.md/AGENTS.md)
card_color = "blue"
publish_overrides = { worker_to_user = false } # per-agent override of [chat.publish]

[chat.publish]                                # who-talks-to-whom group filter
user_to_manager   = "always"                  # boss → manager (always lands)
manager_to_user   = "always"                  # manager → boss (always lands)
manager_to_worker = true                      # show dispatch cards in group
worker_to_manager = true                      # show worker progress in group
worker_to_user    = true                      # show worker completions in group
worker_to_worker  = true                      # show inter-worker pings in group
```

Treat `[chat.publish]` as a **3-class gate**:

- `"always"` = **must publish**. Use for channels that can never be
  muted, such as boss → manager and manager → boss.
- `true` = **optional publish**. Normal progress cards may enter the
  group, but this lane is allowed to become quieter later.
- `false` = **do not publish**. The message should stay in local
  `send`/`inbox`/audit flow and not surface to the group.

Operator rule of thumb: start noisy, then trim. Keep boss-facing lanes
on `"always"`, leave routine worker chatter on `true` during early
rollout, and only flip a lane to `false` after you are sure that lane
is pure noise.

Boss-facing cadence should stay event-driven, not loop-driven:

- **Send immediately** when the boss assigns work, so the group has a
  visible "received / taking it" acknowledgement.
- **Send on real new progress**: a new artifact, a new blocker, a stage
  change, or a decision that changes what happens next.
- **Send one follow-up nudge** when the boss has been silent for a long
  time and a reply/decision is needed to unblock the team.
- **Do not spam repeated status checks**. If you already sent "checking",
  "still looking", or a similar status card, do not re-send the same
  update every 5 minutes with no new fact.

**Forced-broadcast exception:** mainline is expected to treat worker
completion receipts carrying `artifact` or `--done` as **must
publish**, even if the ordinary progress lane for that direction is
configured `false`. In other words, "done with evidence" belongs to the
"always lands" class, not the "optional progress update" class.

For worker progress that flows through `claudeteam send manager ...`,
you can further tune the **content-level** auto-broadcast gate:

```toml
[chat.publish.worker_progress]
must_send_contains = ["根因", "修复", "artifact", "receipt", "blocker", "交付"]
optional_contains = ["已接手", "排查中", "复现中", "处理中"]
forbidden_exact = ["收到", "对齐", "待命", "继续监控"]
forbidden_contains = ["无新事实"]
broadcast_first_optional = true
```

Treat these lists as another 3-class gate:

- `must_send_contains` = **must publish**. If the worker progress text
  contains any of these markers, ClaudeTeam auto-broadcasts it to the group.
- `optional_contains` = **optional publish**. These updates are allowed
  but do not fully free-run. With `broadcast_first_optional = true`,
  the first optional progress for one worker/task pair can surface once,
  so the boss hears the基层开始干活; later same-class echoes stay quiet
  unless they turn into must-send evidence.
- `forbidden_exact` / `forbidden_contains` = **do not publish**. These
  are routine acknowledgements that should stay in the internal
  `send`/`inbox`/audit chain.

Precedence is: `artifact` / `--done` forced publish → forbidden lists →
must-send list → first optional heartbeat (if enabled) → optional /
unmatched text.

For explicit `claudeteam say manager ... --to user` progress cards, you
can tune a parallel gate:

```toml
[chat.publish.manager_progress]
must_send_contains = ["已派发", "已分派", "已回报", "自然窗口", "receipt", "sync", "watchdog", "心跳", "blocker", "阻塞", "卡点"]
optional_contains = ["处理中", "继续跟进", "等待回报", "收集中", "汇总中", "核对中", "观察中", "等待中"]
forbidden_exact = ["收到", "对齐", "待命", "继续监控"]
forbidden_contains = ["无新事实"]
```

Use this to keep two promises at once:

- the boss should still hear real manager progress cards;
- the boss should not see meaningless "收到 / 对齐 / 无新事实" noise.

Structured manager progress cards like `结论 / 证据 / 下一步 / 需要老板`
should be treated as **must-send** progress, not as final delivery
cards. They may carry task IDs or worker names, but path-only delivery,
CLI flags, and heavy internal gate jargon should still be blocked.

### 20 operator scenarios

1. **Boss assigns a task in group** → **must publish**. Reply once right
   away with "received / taking it / dispatching".
2. **Boss asks who is handling the task** → **must publish**. State the
   current owner and the next step.
3. **Manager already started internal dispatch** → **must publish** one
   boss-facing acknowledgement anyway; internal dispatch does not replace
   the receipt.
4. **Worker sends a new artifact path plus a useful summary** → **must
   publish**. This is real progress with evidence.
5. **Worker sends `--done` with `artifact`** → **must publish** even if
   ordinary worker progress is otherwise muted.
6. **Manager reviews and accepts the artifact** → **must publish**. Boss
   should see accepted completion, not only raw worker output.
7. **A new blocker appears and changes the plan** → **must publish**.
   Real blocker updates belong in the boss feed.
8. **The blocker needs boss approval, login, money, or credentials** →
   **must publish** and ask clearly for that action.
9. **The team moves from investigation to active fix** → **may
   publish** if it helps the boss understand risk and momentum.
10. **The team moves from fix-in-progress to waiting-review** → **may
    publish**; if paired with artifact or `--done`, treat it as **must
    publish**.
11. **Boss has been silent for a long time and the team is blocked on a
    decision** → **must publish** one follow-up nudge.
12. **Boss has been silent but the team is not blocked** → **do not
    publish** a reminder just to keep the thread warm.
13. **Manager sends the first "checking now" update after task intake** →
    **may publish** once if reassurance is useful.
14. **Five minutes later there is still no new fact, only "still
    checking"** → **do not publish**. Repeating the same status is spam.
15. **Ten minutes later there is still no new artifact, blocker, or
    conclusion** → **do not publish**. Silence is better than looped
    noise.
16. **Worker pings another worker for help** → **may publish** during
    setup/debug, but usually becomes **do not publish** after noise
    trimming.
17. **Manager sends a normal internal dispatch card to a worker** →
    **may publish** during early rollout, but often becomes **do not
    publish** in production.
18. **A watcher wants to repeat a status-check card on a timer** → **do
    not publish** unless there is a new fact, artifact, blocker, or ask.
19. **A message only says "received", "aligned", "standing by", "still
    working", or "no new update"** → **do not publish**. Keep it in
    local audit.
20. **Any message changes the boss's understanding of reality**: new
    evidence, new conclusion, new ETA, new risk, new ask, or accepted
    delivery → **must publish**.

**Team templates** — instead of hand-writing the roster, start from a domain
template in [`templates/`](../templates/) (software-dev, automated-research,
marketing-growth, data-analysis, content-ops): a ready `claudeteam.toml` plus a
per-role **playbook** `.md` per agent. An agent's `playbook` file becomes the bulk
of its identity — its native `CLAUDE.md` / `AGENTS.md` — layered on top of the team
protocol, so each shows up knowing its job, not just a one-line title. Copy a
folder's contents next to your `claudeteam.toml` (the `playbook` paths resolve
relative to it) and adapt. Write your own for any domain — it's just a `.md`.
Preview what an agent will get with `claudeteam reidentify <agent> --print` — it
renders that agent's identity (role + playbook + team protocol) to stdout, no live
team needed, so you can check a config or playbook edit before `up`.

---

## Agent CLIs

Each agent runs a coding CLI — install the ones you'll use (ClaudeTeam just needs it on PATH).
The default team is all `claude-code`, so `claude` alone runs it.

| Adapter | `cli` | Install |
| ------- | ----- | ------- |
| Claude Code | `claude-code` | `npm i -g @anthropic-ai/claude-code` |
| Codex CLI | `codex-cli` | `npm i -g @openai/codex` |
| Kimi Code | `kimi-code` | `uv tool install kimi-cli` |
| Gemini CLI | `gemini-cli` | `npm i -g @google/gemini-cli` |
| Qwen Code | `qwen-code` | `npm i -g qwen-code` |
| MiniMax Mini-Agent | `minimax` | `uv tool install "git+https://github.com/MiniMax-AI/Mini-Agent.git"` |
| opencode | `opencode` | `npm i -g opencode-ai` |
| CodeWhale | `codewhale` | `npm i -g codewhale` |
| OpenClaw | `openclaw` | `npm i -g openclaw` · needs Node ≥ 22 |
| Trae | `trae` | `uv tool install --with docker --with pexpect "git+https://github.com/bytedance/trae-agent.git"` |
| Hermes | `hermes` | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash -s -- --skip-setup` |
| Pi | `pi` | `npm i -g @mariozechner/pi-coding-agent` |

The last seven are **OpenAI-compatible** (BYOK) — credentials + endpoint below.

---

## Model backend per agent (credentials + endpoint)

**A first boot needs none of this** — the 2 default agents run on your Claude
Code OAuth (reusing your local login). Come here only when you swap an agent onto
a non-Anthropic backend.

The adapters are **provider-agnostic** — nothing about DeepSeek/OpenAI/etc. is
baked in. You choose the backend through env + config:

- **Credential** — resolved by `runtime/agent_auth`, priority **token > login >
  api_key** (higher present overrides lower). Secrets live in a gitignored env
  file (`$CLAUDETEAM_SECRETS_FILE`, default `<state_dir>/.env`) or the process
  env — never in `claudeteam.toml`. Per-agent override: `<AGENT>_<VAR>` (e.g.
  `WORKER_PI_OPENAI_API_KEY`).
  - **claude-code / codex / kimi** — their own token/login/api_key vars.
  - **all other CLIs** (minimax, opencode, codewhale, openclaw, trae, hermes, pi)
    — the **api_key** tier: set `OPENAI_API_KEY`.
- **Endpoint** — `OPENAI_BASE_URL` (e.g. `https://api.openai.com/v1` or a
  self-hosted vLLM/Ollama URL — any OpenAI-compatible API). **Model** — the
  `model` field in each `[team.agents.<name>]`.
- **Provider name** (only where a CLI needs one selecting an OpenAI-compatible
  *chat/completions* client): `CLAUDETEAM_TRAE_PROVIDER` (default `openrouter`),
  `CLAUDETEAM_PI_PROVIDER` / `CLAUDETEAM_CODEWHALE_PROVIDER` (default `openai`).
- A **claude-code manager on a non-Anthropic backend** uses the
  Anthropic-compatible vars: `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`
  (+ `ANTHROPIC_MODEL` / `ANTHROPIC_DEFAULT_*_MODEL`).

Example — point the OpenAI-compatible workers (and, optionally, a claude-code
manager on a non-Anthropic backend) at **any** provider, via `docker -e` or the
host shell. Swap the host for whatever you use (a hosted API or a local server):

```bash
OPENAI_BASE_URL=https://your-provider.example/v1   # the provider's base URL
OPENAI_API_KEY=sk-...                              # your key for it
# a claude-code manager on a non-Anthropic backend uses the Anthropic-compatible vars:
ANTHROPIC_BASE_URL=https://your-provider.example/anthropic
ANTHROPIC_AUTH_TOKEN=sk-...
```

The per-CLI `CLAUDETEAM_<CLI>_PROVIDER` vars (see above) pick the
chat/completions client — leave them at their defaults unless your provider needs
a specific one. See each CLI's `tests/scenarios/<cli>.md` for concrete, per-provider specifics.

---

## Agents talking to each other: `send` vs `say`

| Command | What it does | Reaches the worker's pane? |
| --- | --- | --- |
| `claudeteam send <to> <from> <msg>` | Inbox row **+** tmux pane inject | **Yes** — wakes the recipient directly |
| `claudeteam say <agent> "<msg>" --to <role>` | Post into Feishu chat (subject to `[chat.publish]`) | Only if the router relays it back |
| Feishu group → router → `deliver.apply` | Inbound chat → inbox row + pane inject | **Yes** — wakes a worker on boss/manager input |

**Always pass `--to`** on `say`: `--to user` = answering the boss; `--to manager`
= internal progress; `--to worker_<name>` = peer ping. Omitting it falls back to
`user` and defeats the publish filter.

---

## Multi-team isolation

State lives in a `state/` dir **beside each team's `claudeteam.toml`** — the
config's location *is* the team's identity. Running a second team needs no
special env; just keep each team in its own directory:

```bash
cd /path/to/team-a && claudeteam up
# different shell:
cd /path/to/team-b && claudeteam up
```

Each team keeps its own `team-a/state/` and `team-b/state/`, so their agents,
status, and inboxes never bleed together. Override the location with
`CLAUDETEAM_STATE_DIR` if you want state elsewhere (e.g. a Docker volume).

Each team still needs its **own Feishu app** (independent app_id/secret) —
sharing one across teams causes credential leakage + event-routing conflicts.

---

## Commands

**Operator CLI** — `claudeteam --help` lists everything grouped by section (it's
self-maintaining; trust it over any table here). The everyday ones: `up` / `down`
/ `health` / `team` / `peek <agent>` / `usage` / `reidentify` / `remember` /
`recall` / `switch`.

**Chat-side slash commands** (after `install-hooks`, recognised in the manager
pane; the boss can also send them — they zero-LLM dispatch through the router):

| Slash | What it does |
| --- | --- |
| `/help` | List all slash commands (card) |
| `/team` | All agents' live pane state |
| `/health` | Server CPU / memory / disk card |
| `/usage` | Token/credit usage (ccusage / codex / kimi) |
| `/tmux [agent] [N]` | Capture last N lines of a pane |
| `/send <agent> <msg>` | Inject a message into a pane |
| `/compact [agent]` | Compact the CLI's context + scheduled re-identify |
| `/stop [agent]` | Interrupt the agent (Esc; pane stays alive) |
| `/clear <agent>` | `/clear` the CLI + re-inject identity |
| `/task [all]` | Read-only task kanban |
| `/shutdown [confirm]` | Panes offline, keep router/watchdog for `/restart` |
| `/restart` | Restart the whole team (≈ down→up) |
| `/login <cli> [agent]` | Trigger a CLI re-auth; surfaces the verification URL/code |

---

## Common failures

### `claudeteam feishu connect` hangs / says "cancelled"

A non-interactive terminal (piped / non-TTY) or a Ctrl-C gives "cancelled (no
input / non-interactive terminal)" — re-run it in an **interactive** terminal.
The no-flag (browser-automated) mode needs a **desktop browser**, so it can't run
on a headless server — there, run it on a desktop machine and copy
`state/feishu_app.json` + `chat_id` over (or use `--quick` / `--manual`, which need
no browser). If the console UI changed under it, it falls back to `--manual`; you
can also force `--manual` to click through it yourself. `--quick` prints its QR
before waiting for your scan.

### Group messages get no response after `up` / router keeps restarting

Usually the **sidecar's WebSocket (long connection) never came up** — the router
spawns the sidecar to receive events; if it can't connect it errors out and exits,
the router exits with it, the watchdog respawns it, and round it goes. The router
log shows `⚠️ subscribe child exited` followed by a **`↳ sidecar 最后输出` + `↳ 诊断`**
block; the two fixes it points to:
1. **The app has no long-connection subscription** → Feishu console → Events &
   callbacks → subscription mode → switch to "Receive events via long connection"
   (NOT Webhook URL), save. (`--quick` usually sets this up; check it on a
   hand-built app.)
2. **An HTTPS_PROXY is blocking the WebSocket** → `export LARK_CLI_NO_PROXY=1`
   before launch, or set it in `$CLAUDETEAM_SECRETS_FILE` (default `<state>/.env`)
   / your shell profile.

Ingress works the moment the sidecar connects; if it IS connected but the group is
still silent, check the manager's `claude` login (entries below).

### `claude: not found` / `codex: not found` in a pane

Panes inherit the launching shell's `$PATH`. If you opened a fresh terminal and
forgot `source .venv/bin/activate`, the pane has no project venv. Re-`up` from a
shell where the agent CLIs resolve.

### "Not logged in" in a claude pane (macOS host)

Each pane has its own `~/.claude/.credentials.json` snapshot (seeded from your
local login, per-agent home isolation), which can go stale vs the keychain. Fix:
`claudeteam down && up` re-materialises it.

### `router.log` shows "no live events … rotating subscribe" every ~120 s

**Usually NORMAL, not a fault — especially on macOS.** On an idle chat the
WebSocket goes quiet; the router self-SIGTERMs (`_watch_subscribe_health`),
watchdog respawns it, and catchup refetches anything missed from Feishu's REST
API — the recovery loop *is* the design. The platform-aware idle threshold is
Darwin 120 s / Linux 600 s (override `router.stale_event_threshold_s` in the toml
or `CLAUDETEAM_ROUTER_STALE_S`). Two shapes:

- `ℹ️ no live events for Ns — rotating subscribe (none inbound yet …)` — idle, expected.
- `⚠️ live events stopped after Ns idle …` — events WERE flowing and stopped (notable, esp. on Linux).

The log never prints "I received your message" — trust `claudeteam health`'s
`inbound:` line + one real group message instead. If the `⚠️` is *constant*,
look for a second sidecar stealing events:
`ps -ef | grep -E "feishu_channel/sidecar\.js run" | grep -v grep`.

### Manager loops on the same anchored message after `up`

Catchup replays everything newer than the cursor (with a `state/router.seen`
dedup set, auto-trimmed at 5000). Still duplicating? Delete `state/router.seen`
and bump `state/router.cursor` forward to "now" so the next catchup skips older.

### `say` from a pane fails HTTP 400 "Bot/User can NOT be out of the chat"

`say` from your launching shell works, but the same call from inside a pane
fails. Cause: a pre-existing tmux **server** (from an earlier `up`, different
checkout) holds its original global env, and `tmux new-session` inherits *that*,
not your shell's. The lifecycle prefix now embeds the creds per spawn-cmd, so a
clean state shouldn't trigger it. If it still does:

```bash
tmux ls 2>/dev/null
ps -ef | grep -E "claudeteam (router|watchdog)|feishu_channel/sidecar\.js" | grep -v grep
claudeteam down
tmux kill-session -t ClaudeTeam        # or `tmux kill-server` if no other tmux work
claudeteam up
```

### `say` / sidecar can't find App credentials

Outbound cards fail, or the sidecar exits complaining it has no app id/secret.
Creds resolve from one source: `state/feishu_app.json` (written by
`feishu connect`, 0600), which `feishu/lark.py:subprocess_env()` reads to inject
`FEISHU_APP_ID`/`SECRET` + a tenant token into both the sidecar (ingress) and
lark-cli (egress). `ls -l state/feishu_app.json` (expect `-rw-------`); if
missing, re-run `claudeteam feishu connect`.

### `worker_codex` (or any codex agent) shows "pane up but CLI not ready yet"

Codex sometimes opens with an "update available" prompt blocking the ready marker:

```bash
tmux send-keys -t ClaudeTeam:worker_codex 3 Enter   # "Skip until next version"
claudeteam reidentify worker_codex
```

---

## Where things live

```
src/claudeteam/
├── cli.py             single console-scripts entry; dispatch only
├── commands/          one module per subcommand (~30-300 LOC each)
├── store/             local file-backed state (inbox, status, logs, tasks, memory)
├── agents/            CliAdapter base + per-CLI adapters + identity renderer
├── runtime/           config / paths / tmux / watchdog / pidlock / wake / lifecycle / tunables
└── feishu/            lark-cli wrapper + chat + router + slash + deliver + subscribe + catchup
scripts/feishu_channel/  the @larksuite/channel sidecar (registration + ingress)
tests/                 unit/ + integration/ (stdlib runner) + scenarios/ (operator playbooks)
```

`CLAUDE.md` (project root) holds the building rules + active work order — read it
before changing code.

---

## Stuck? Found a bug?

Under active development — we **respond within 12 hours**.

- 🐛 **GitHub issue** — [open one](https://github.com/zylMozart/ClaudeTeam/issues/new/choose).
  Include OS, deploy mode (host vs Docker), and the failing command's output (for
  `feishu connect` issues, the sidecar's stderr).
- 💬 **WeChat community group** — scan the QR in the [README](../README.md#need-help--found-a-bug).

If you're an AI agent driving a deploy and a step fails after a real recovery
attempt, surface this section to the user — there's a real maintainer reachable.
