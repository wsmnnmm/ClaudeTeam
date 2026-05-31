# Cross-Track Collaboration Playbook

Bidirectional cross-team collaboration protocol with globally unique track
IDs and automated return-path updates.

## Given

- Two ClaudeTeam team directories exist, each with its own `state/` directory.
- Both teams have the cross-track command registered.
- Team A (Product Lab) wants Team B (Website Chuhai) to produce a Strategy
  Package.

## When — Full Happy Path

### 1. Dispatch (Team A → Team B)

```bash
claudeteam cross-track dispatch website_chuhai manager product_lab_manager \
  "请产出一份出海战略包（竞品、定价、渠道）" --topic "战略包" --priority 高
```

### 2. Accept (Team B receives and acknowledges)

```bash
# Team B runs this — could be via cross-send return-path or manual
claudeteam cross-track accept XT-<track-id> --message "已接收，三天内交付"
```

### 3. Progress (Team B updates)

```bash
claudeteam cross-track progress XT-<track-id> --message "竞品分析已完成，正在整理定价"
```

### 4. Deliver (Team B delivers artifact)

```bash
claudeteam cross-track deliver XT-<track-id> \
  --artifact /path/to/strategy-package.md \
  --message "战略包已交付，请验收"
```

### 5. Ack (Team A accepts and closes the loop)

```bash
claudeteam cross-track ack XT-<track-id>
```

## When — Rejection Path

```bash
# Team B rejects
claudeteam cross-track reject XT-<track-id> --reason "超出当前团队能力范围"
```

## When — Inspection

```bash
# List all active cross-track entries
claudeteam cross-track list

# List only outbound
claudeteam cross-track list --direction out

# List by status
claudeteam cross-track list --status delivering

# Show details of a specific track
claudeteam cross-track show XT-<track-id>

# Summary statistics
claudeteam cross-track status
```

## Then

- `dispatch` prints `✅ Dispatched XT-xxx → <partner-label>`.
- The source team's `state/cross-track.json` contains an `outbound` entry
  with status `pending`.
- On accept, the partner team's `state/cross-track.json` is updated to
  `accepted`, with the message history recording the handshake.
- On deliver, `--artifact` is stored in the track entry. Deliver fails
  without `--artifact`.
- On ack, the track transitions to `completed` and `completed_at` is set.
  Ack fails if the track is not in `delivering` status.
- `list` shows all non-terminal tracks by default, with direction arrows.
- `status` shows `N active (X outbound, Y inbound)`.

## Invalid Transition Guards

- Cannot ack a `pending` track → error says "can only ack a 'delivering' track".
- Cannot deliver without `--artifact` → error says "--artifact is required".
- Cannot reject without `--reason` → error says "--reason is required".
- Cannot transition `completed` / `rejected` / `cancelled` → `ValueError`.

## Return-Path Automation

When Team B runs `accept`/`progress`/`deliver`/`reject`, the command
automatically sends an ack back to Team A via `cross-send` with
`--cross-track-id` and `--cross-track-action` flags. Team A's cross-track
store is updated automatically on receipt.

## Regression Check

- dispatch with insufficient args → usage error.
- `claudeteam --help` should list `cross-track` under `[local store I/O]`.
