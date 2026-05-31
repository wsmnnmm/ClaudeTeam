# Scenario: Boss Base Decision Intake

## Given

- The boss cockpit Base is reachable by the configured `product-lab`
  lark profile.
- The Base contains:
  - `团队战情板` (`tblEyoEGZOZ0gfJr`)
  - `老板任务流` (`tblJ67mLhY9oM91G`)
- Both watched tables have text fields `老板决策` and `下发回执`.
- `团队战情板` also has `建议操作` (system-written) and `老板操作`
  (boss-edited; select or text) for repeatable cockpit actions.
- Exactly one owner team has `[base_intake].enabled = true`.

## When

On `团队战情板`, edit a team card and fill `老板决策`, for example:

```text
接入，先让 manager 创建审计任务并回报阶段证据。
```

Or set `老板操作` on a stale team card:

```text
老板操作 = 重新核验
```

Or on `老板任务流`, create/update a row with:

```text
当前状态 = 待下发
负责人团队 = Product Lab 本地
负责人agent = manager
任务标题 = 验证智能伙伴是否接入
下一步动作 = 先做审计任务，不进入正式执行
```

## Then

- The owner router receives the Base record-change event.
- It fetches the changed record by record_id.
- It dispatches a high-priority `boss_base` task/inbox message into the
  target team and injects the target agent pane when available.
- It writes `下发回执` back to the Base record, and clears `老板操作`
  after dispatch so the boss can select the same operation again later.
- Repeated writeback/sync events with the same decision fingerprint do not
  create duplicate tasks.

## Regression Checks

- `团队战情板` rows must not dispatch merely because `老板分组=现在要你决定`;
  they require explicit `老板决策` or `老板操作`.
- `老板操作=重新核验` / `重新激活` must become a task telling the manager
  to wake/check the pane, run health, reconcile tasks, and write fresh facts
  back to the cockpit.
- `老板任务流` rows may dispatch by `当前状态=待下发` plus a concrete
  `下一步动作`.
- If `负责人agent` is not a real agent in that team, dispatch to `manager`
  and include the original requested agent in the message.
- Base intake must be off by default.
