# MiniMax Research Worker

> 用途：把快过期的 MiniMax 调用额度专门用于批量研究，而不是工程开发。

---

## 为什么单独做这个入口

当前已经验证：

- `https://minimax.a7m.com.cn/v1` 的 OpenAI 兼容模型列表可用
- `MiniMax-M2.7-highspeed` 可用于 `chat/completions`
- 但 `codex-cli` 的 custom provider 目前强制走 Responses API
- 该网关的 `/v1/responses` 返回 `not implemented`

所以不要把 MiniMax 额度硬塞给 `codex-cli` 交互式 worker。

更稳的做法是：

- ClaudeTeam 继续负责调度
- MiniMax 通过一个本地 helper 脚本承担“批量研究 worker”角色

---

## 本地配置文件

配置文件路径：

`/Users/wsm/Project/ClaudeTeam/state/minimax_research.json`

结构：

```json
{
  "url": "https://minimax.a7m.com.cn/v1",
  "api_key": "sk-...",
  "model": "MiniMax-M2.7-highspeed",
  "max_tokens": 4096
}
```

注意：

- `state/` 已被 `.gitignore` 忽略，不会进 Git
- 优先用 `/v1`
- 不建议用根域名 Anthropic 兼容形式，因为已经验证不通

---

## 用法

直接传 prompt：

```bash
python3 scripts/minimax_research.py prompt --text "列出 10 个 AI 客服 SaaS 方向"
```

从文件读 prompt：

```bash
python3 scripts/minimax_research.py file --input /tmp/research_prompt.txt
```

---

## 适合拿来做什么

- 长名单生成
- 竞品粗筛
- 用户痛点归纳
- 批量评分初稿
- 多版本文案生成

## 不适合拿来做什么

- 复杂工程开发
- 仓库级重构
- daemon / infra 修复
- 最终拍板决策
