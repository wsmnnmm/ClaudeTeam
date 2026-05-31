# 双活云端准备

## Given

- 本地 Product Lab 已运行，主链 preset 为 `flux-primary`
- 云上 Product Lab 使用独立飞书 app / bot / 群聊
- 云上配置文件：
  - `ops/claudeteam-cloud/claudeteam.cloud.toml`
- 云上同步脚本：
  - `scripts/cloud/claudeteam-cloud-sync.sh`
  - `scripts/cloud/claudeteam-cloud-bootstrap.sh`
  - `scripts/cloud/claudeteam-cloud-start.sh`

## When

1. 同步本地代码与 provider 配置到云上：

```bash
cd /Users/wsm/Project/product-lab
scripts/cloud/claudeteam-cloud-sync.sh
```

2. bootstrap 云上运行时：

```bash
scripts/cloud/claudeteam-cloud-bootstrap.sh
```

3. 启动云上团队：

```bash
scripts/cloud/claudeteam-cloud-start.sh
```

4. 云上健康检查：

```bash
scripts/cloud/claudeteam-cloud-health.sh
```

## Then

- 云上应使用独立 `chat_id` / `lark_profile`
- 云上 `manager` 默认走 `flux-primary`
- 云上存在 `worker_rescue`，但保持 lazy，不在平时消耗 DeepSeek 额度
- 云上 failover 命名与本地一致：
  - `flux-primary`
  - `zyapi-backup`
  - `deepseek-rescue`
- 如果云上 manager 主链失效：
  - 先切 `zyapi-backup`
  - 只 `recycle manager`
  - 若 backup 仍失败，再唤醒 `worker_rescue`

## Not Allowed

- 本地和云上长期共用同一个飞书 app / bot / 群聊还宣称“双活”
- 云上静默覆盖本地任务状态
- 把云上 `/srv/...` 路径当成本地已交付
