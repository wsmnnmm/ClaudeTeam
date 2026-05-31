# ClaudeTeam 老板追问：用 Claude Code Hook 机制实现 API 成本守卫？

## 老板的理解

读了 Claude Code 的 Hook 文档后，发现刘小排说的"100 行脚本拦截 API 调用"，本质上就是 **PreToolUse Hook**：

- **Skill**：建议，Claude 可以自行判断要不要听
- **Hook**：命令，系统强制，跳不过、忘不了、不能商量
- **PreToolUse**：工具被调用之前，系统先跑你的脚本。脚本说 OK 放行，说不行直接断

对应到 API 成本守卫：
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "./check-api-cost.sh"
      }]
    }]
  }
}
```

agent 要调用付费 API → Hook 拦截 Bash → 估算成本、对预算 → OK 放行 / 超预算断掉

**优势**：系统级拦截（agent 跳不过）、独立进程（不占 context）、时序在花钱之前（不是事后审计）、ClaudeTeam 已有 `install-hooks` 基础设施

## 追问刘小排

1. Hook 这个架构是不是你说的"100 行脚本"的正确实现方式？
2. 如果用 PreToolUse Hook，跟做成独立产品（CLI/插件/SaaS）矛盾吗？还是 Hook 只是第一步？
3. 打分：Hook 方案 1-10？
