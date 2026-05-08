# 飞书企业自建应用（机器人）创建指南

ClaudeTeam 部署需要一个飞书企业自建 App + 机器人能力 + 一组权限 +
事件订阅 + 卡片回调 + 已发布版本。整个流程由
[`scripts/feishu_bot_creator/create_feishu_bot.js`](../scripts/feishu_bot_creator/create_feishu_bot.js)
分成 **7 个 stage**，每个 stage 内部由 Playwright 跑完一段 UI 操作，
跑完即 exit；驱动它的 AI agent 用 `status` 自检结果，再用 `next`
推进到下一 stage。**用户全程只需要扫一次 QR 登录**，之后由 agent
托管完成，最后报回 `App ID` + `App Secret`。

如果 UI 改版导致脚本某个 stage 失败，agent 可以参照本文里对应章节
的"页面变化"描述手动操作那一个 stage，再用 `next` 接着自动跑剩下
的——不必整套重来。

---

## 入口命令（drive 模式 — chromium 一次开到底）

`drive` 是唯一入口，里面已经包含 login（首次跑时停下让用户扫
QR，cookies 持久化所以以后不再扫）：

```bash
cd scripts/feishu_bot_creator
npm install                                       # postinstall 自动装 chromium
node create_feishu_bot.js drive <bot-name> "<desc>" \
  > /tmp/drive-<bot-name>.log 2>&1 &
```

drive 跑完一个 stage 就阻塞等命令文件，agent 读 state + log
判断结果后写命令推进：

```bash
# 推进下一 stage（happy path, 上一 stage 自动跑完了）:
echo next > scripts/feishu_bot_creator/.state/<bot-name>.cmd

# Agent 自己在浏览器里手动完成了当前失败的 stage, 标记 done:
echo skip > scripts/feishu_bot_creator/.state/<bot-name>.cmd

# 重跑某个 stage (drive 不退出):
echo "redo events" > scripts/feishu_bot_creator/.state/<bot-name>.cmd

# 提前结束:
echo quit > scripts/feishu_bot_creator/.state/<bot-name>.cmd
```

**`skip` 是核心 escape hatch** —— 当 Feishu UI 改版导致某个 stage
的 Playwright selector 失败时, agent 不必整套放弃 / 重起浏览器:
直接在 drive 还开着的那个 chromium 窗口里手动完成那一步 (paste
JSON / 点该点的按钮 / 改下拉选项), 然后 `echo skip` 让 drive 把这
个 stage 标 done, 自动推进到下一 stage 继续自动化. 这就是 stage 化
的真正价值 — UI 飘移不会让流程整体崩, agent 只需要修一个 stage.

状态 / 进度查看：
- `scripts/feishu_bot_creator/.state/<bot-name>.json`：JSON state
  含 `appId` / `completedStages` / `lastError`
- `/tmp/drive-<bot-name>.log`：实时 stdout / stderr
- `node create_feishu_bot.js status --app <bot-name>`：单次打印
  state 表格

drive 跑完 publish 自动退出，浏览器关闭。Crash / kill 后再起一次
`drive` 命令从同一断点续跑（按 `completedStages` 跳过已做完的）。

> **底层命令** (`stage <id>` / `next` / `login` / `create` / `batch`)
> 在 `--help` 里有列, 主要给手动调试或批量预热用; agent 平时不用
> 关心, 直接 drive 即可.

---

## Stage 1 — `create-app`

**目标**：在飞书开放平台创建一个企业自建应用，从 URL 拿到 App ID。

**自动操作**：
1. 跳转 [https://open.feishu.cn/app](https://open.feishu.cn/app)
2. 点 **"Create Custom App"**（创建企业自建应用）
3. 在弹出的表单填 `--name` 给出的应用名
4. 在 textarea 填 `--desc` 给出的应用描述
5. 点 **"Create"**
6. 跳转后从 URL `…/app/cli_xxx/capability` 中正则匹配 App ID
7. 写入 `.state/<bot-name>.json` 的 `appId` 字段

**对应 manual UI**：登录开放平台 → 「创建企业自建应用」→ 填名字 +
描述 → 「创建」。完成后浏览器地址栏的 `cli_xxx` 就是 App ID。

**完成判断**：state 文件里 `appId` 非空，且 `completedStages` 含
`create-app`。

**失败常见原因**：用户未登录（前置 `login` 没跑或 cookie 过期）。
解决：跑 `node create_feishu_bot.js login` 重新扫码。

---

## Stage 2 — `add-bot`

**目标**：给应用添加"机器人"能力，否则后续没办法发卡 / 收消息。

**自动操作**：
1. 跳转 `…/app/<appId>/capability`
2. 在能力列表里点第一个 **"Add"** 按钮（机器人卡片）
3. 等待跳转到 `…/bot` 页面

**对应 manual UI**：进应用 → 左侧「添加应用能力」→ 找到「机器人」
卡片点「添加」。

**完成判断**：URL 里出现 `/bot`，且 `completedStages` 含 `add-bot`。

**失败常见原因**：能力列表的 "Add" 按钮顺序变了。解决：手动加完
机器人能力后跑 `next` 跳到 stage 3。

---

## Stage 3 — `import-scopes` ⚠️ 部分成功是常态

**目标**：粘贴
[`feishu_scopes.json`](../scripts/feishu_bot_creator/feishu_scopes.json)
里的 ClaudeTeam 必备 scope（精简成 ~6 条 tenant / 3 条 user，不再是
之前的 ~480 条 wishlist）。

**已知 Feishu UI 限制（2026-05-08 forensic 验证）**：Monaco 编辑器在
这个 dialog 里**拒收所有 programmatic 输入**——synthetic
ClipboardEvent / `keyboard.type` / OS clipboard + Playwright Cmd+V
都试过，textarea 都会被 Feishu 后端的 sync 立即覆盖回 bot 当前 scopes。
只有真 OS 用户手按 Cmd+V（chromium 真获 OS 焦点 + 非 CDP-injected
keystroke）能让 Monaco 模型真正接受新内容。Drive 跑过来这一步 stage
**实际生效的 scope 顶多 3 个**——大约是 add-bot / events stage 默认带来的
那批，跟我们 paste 的内容关系不大。

**Drive 的应对**：粘贴 + Add 仍照流程走（Add 之后调
`/developers/v1/scope/applied/<app_id>` 实查 bot 当前 scope，跟
`feishu_scopes.json` 要求的对比，差了哪些就 log 出来 + 给一句"上
[https://open.feishu.cn/app/<app_id>/auth](https://open.feishu.cn/app/<app_id>/auth)
手动加"。drive 不会 throw——这一步原本就没法 100% 自动化。

**操作员补救**：drive 跑完看 log "scopes Feishu UI didn't activate"
那行；缺的 scope 浏览器打开提示的 URL 一条一条手动 + → Submit 即可。
ClaudeTeam 基础部署只需 `im:message:send_as_bot`，多数情况无需手动补；
仅当用 OpenAPI 自动建群 / 跨场景做高级集成才需要 `im:chat:create_by_user` 等。

**对应 manual UI**：左侧「权限管理」→「添加权限」→ 搜对应 scope 名 → +。
批量导入按钮也存在但同样只对 OS-级人手 Cmd+V 有效。

---

## Stage 4 — `data-range`

**目标**：把"数据访问范围"设为「全部」，否则后续机器人在某些群
里读不到消息。

**自动操作**：
1. stage 3 导入权限后会自动弹"配置数据访问范围"对话框
2. 点对话框内的 **"Configure"**
3. 选 **"All"** → **"Save"** → **"Confirm"**
4. 如果对话框未弹（之前已配过），跳过这步

**对应 manual UI**：弹出对话框 →「配置」→ 选「全部」→ 「保存」→
「确认」。

**完成判断**：对话框消失，`completedStages` 含 `data-range`。

**失败常见原因**：对话框选择器变化。解决：手动在权限管理页面找
「配置数据范围」按钮设为「全部」，然后跑 `next`。

---

## Stage 5 — `events`

**目标**：把订阅模式设为**长连接（persistent connection）**而不是
回调 URL，并订阅所有 `message` 相关事件（Tenant + User token 双
tab 全勾）。

**自动操作**：
1. 跳转 `…/app/<appId>/event`
2. 找「Subscription mode」编辑按钮 → 点开 → 默认是长连接 → **Save**
3. 点 **"Add Events"** → 搜 `message` → Tenant Token tab 勾全部
   checkbox → User Token-Based Subscription tab 切换勾全部
4. **"Add"** 提交
5. 如果弹「建议添加的权限」对话框，点 **"Add Scopes"** 关掉

**对应 manual UI**：左侧「事件与回调」→「事件配置」→ 编辑订阅方
式 → 选「长连接」保存 →「添加事件」→ 搜 `message` → 两个 tab 全
勾 → 「添加」。

**完成判断**：事件列表里出现 `im.message.receive_v1` 等条目；
`completedStages` 含 `events`。

**失败常见原因**：tab 切换的文案 "User Token-Based Subscription" 改
了。解决：手动按上述步骤勾选完事件订阅后跑 `next`。

---

## Stage 6 — `callbacks`

**目标**：在「回调配置」tab 启用 **`card.action.trigger`**，让用户
点卡片按钮的事件能回到机器人（ClaudeTeam 不依赖这个但保留以备
未来用）。

**自动操作**：
1. 在 events 同一页切到 **"Callback Configuration"** tab
2. 编辑订阅方式 → 长连接 → Save
3. 点 **"Add callback"** → 勾第一个 checkbox（`card.action.trigger`）
   → **"Add"**

**对应 manual UI**：「事件与回调」→「回调配置」→ 编辑订阅方式 →
长连接保存 → 「添加回调」→ 勾「卡片回传交互」→ 「添加」。

**完成判断**：回调列表里出现 `card.action.trigger`；
`completedStages` 含 `callbacks`。

---

## Stage 7 — `publish`

**目标**：把以上所有配置打包成一个版本并发布上线，否则机器人不
会真的开始接事件。

**自动操作**：
1. 跳转 `…/app/<appId>/version`
2. 点 **"Create Version"**
3. 跳到表单，滚动到底部点 **"Save"**（保留默认值）
4. 在弹出确认框点 **"Publish"**

**对应 manual UI**：左侧「版本管理与发布」→「创建版本」→ 表单保
留默认 → 滚到底「保存」→ 弹出确认框「确认发布」。

**完成判断**：版本列表里出现新版本，状态「已启用」；
`completedStages` 含 `publish` —— 这时整个 7 stage 走完，agent
应该停下来去开放平台「凭证与基础信息」页读 App ID + App Secret，
报给用户。

---

## 完成之后

把 `App ID` + `App Secret` + 你把机器人加到的飞书群的 `chat_id`
喂给 `claudeteam`（写进 `.env` 或 `claudeteam.toml`），后面就走
[`docs/DEPLOYMENT.md`](DEPLOYMENT.md) 的 step 2-4。

`chat_id` 怎么拿：

```bash
LARK_CLI_NO_PROXY=1 lark-cli im +chat-search \
  --query "<群名关键字>" --as user
```

输出里的 `oc_xxxxxxxx` 就是 chat_id。
