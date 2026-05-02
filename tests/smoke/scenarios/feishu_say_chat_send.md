# Feishu chat send via `claudeteam say`

## 场景
manager（或 worker）通过 `claudeteam say` 把一句话发到 Feishu 群里。同步把 say 这一动作记到本地 log（审计），失败时不污染本地状态。

## 范围
- 类型：host-live (Feishu)
- 凭证：lark-cli profile + chat_id 已 setup

## Given
- runtime_config.json 含 `chat_id`（oc_xxx）和 `lark_profile`
- 该 profile 已 `lark-cli auth login`（用户身份）或 app 有 `im:message` scope（bot 身份）

## When

```bash
claudeteam say manager "smoke test #$(date +%s)"
claudeteam say worker_codex "as user reply" --as user --reply om_parent_xxx
```

## Then
1. 第一次 `say` 退出 0，stdout 含 `manager → chat (om_xxx)`
2. 飞书群里看到一条 `[manager] smoke test #<timestamp>`（bot 身份发出）
3. `claudeteam workspace manager` 列出一行 `say` 类型 log，content == 原消息
4. 第二次 `say` 退出 0，引用了 `om_parent_xxx`
5. 故意把 chat_id 设空再跑：退出 1，stderr 含 `chat_id not set`

## 反例

- 网络慢路径：lark-cli 默认 73s timeout 不少见。设 `CLAUDETEAM_LARK_TIMEOUT=120` 给余量
- 代理拦截：set `LARK_CLI_NO_PROXY=1` 让 wrapper 自动剥 HTTPS_PROXY

## 证据（执行时填）

```
- chat_id: oc_xxx
- T_send: …
- T_visible_in_group: …
- 结果: pass | fail
- 后续: …
```
