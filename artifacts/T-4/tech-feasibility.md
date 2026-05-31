# T-6 技术可行性速评：AI 产品图素材包

日期：2026-05-19
任务边界：只做技术评估，不写代码。目标场景是跨境小卖家上传手机拍摄 SKU 基础图，生成白底图 / 生活场景图 / 广告图，用 Credits 或订阅变现。

## 结论先行

推荐 MVP 方案：**Next.js + Replicate 托管的 Black Forest Labs FLUX 系列**。

- 首选模型：`black-forest-labs/flux-1.1-pro` 或 BFL 官方 `FLUX.2 [pro]`。
- 原因：单图成本透明（约 $0.03-$0.04 起）、速度和质量适合电商素材，API 接入简单，前期不用自建 GPU。
- 白底图建议不要完全依赖生成模型：先做 `remove background / segment` + 生成或合成白底；生活场景图 / 广告图再走 FLUX / GPT Image。
- Credits 可行：若每次生成 4 张候选图，基础模型成本约 $0.12-$0.16；按 1 Credit = $0.10 或 10 Credits = $1 的零售价设计，毛利空间存在，但必须限制重试和高质量档。

## 1. API 选型对比

| 方案 | 当前价格证据 | 适合点 | 不适合点 | MVP 判断 |
|---|---:|---|---|---|
| Replicate + FLUX | Replicate `flux-1.1-pro` $0.04 / output image；`flux-dev` $0.025 / image；`flux-schnell` $3 / 1000 images | 接入最快；价格按图透明；有现成模型页和 SDK；适合快速验证 | 商用许可、模型版本、冷启动/队列由平台控制 | **推荐 MVP 默认** |
| BFL 官方 FLUX API | FLUX.2 `[pro]` from $0.03 / image；`[klein]` from $0.014-$0.015；`[max]` from $0.07 | 官方渠道；图像质量和速度更可控；支持 FLUX.2 编辑 | 定价随分辨率和输入图变化；需要接 BFL 账号 | **推荐生产候选** |
| OpenAI `gpt-image-1` | 低/中/高质量方图约 $0.02 / $0.07 / $0.19；文字 $5/M tokens、输入图 $10/M、输出图 $40/M | 指令理解、文字渲染、复杂编辑强；安全/审核体系成熟 | 高质量成本偏高；组织可能需验证；对批量便宜图不一定划算 | **作为高质量/广告图档位** |
| Stability AI | 官方 Brand Studio 为 $50/月含 5000 credits；生成/编辑消耗 credits，API 可企业集成；公开页面未直接给每端点 API 单图价 | Stable Diffusion 生态成熟；可自托管/企业部署 | 当前公开价格页对 Platform API 单图成本不够直接；需要登录平台核价 | **暂不做 MVP 默认** |
| 自托管开源 SD/FLUX | GPU 成本自控，边际成本低 | 大批量后成本可能最低 | 运维、排队、显存、模型调优、审核都要自己做 | **等 PMF 后再评估** |

来源：
- OpenAI image generation API pricing: https://openai.com/index/image-generation-api/
- Replicate pricing: https://replicate.com/pricing
- Black Forest Labs API pricing docs: https://docs.us.bfl.ai/quick_start/pricing
- Stability AI pricing: https://stability.ai/pricing

## 2. 最小 Demo 架构

目标：上传 1 张 SKU 手机图 → 选择生成类型 → 调 API → 返回 2-4 张变体 → 保存任务和消耗 credits。

架构草图：

```text
Next.js App Router
  app/create/page.tsx
    - 上传 SKU 原图
    - 选择：白底图 / 生活场景图 / 广告图
    - 输入可选卖点、目标平台、尺寸

  app/api/generate/route.ts
    - 校验登录、文件大小、图片类型
    - 上传原图到对象存储或临时 signed URL
    - 根据模式拼 prompt
    - 调 Replicate/BFL/OpenAI Images API
    - 记录 usage、cost、credit debit
    - 返回生成图 URL 列表

Storage
  originals/{userId}/{jobId}.jpg
  outputs/{userId}/{jobId}/{variant}.png

DB
  users, credit_ledger, generation_jobs, generation_outputs

Queue（MVP 可先不用）
  高延迟时再引入 Inngest / Trigger.dev / BullMQ
```

预计代码量（Next.js 已有 auth/storage 前提下）：

| 文件 | 作用 | 估算 LOC |
|---|---|---:|
| `app/create/page.tsx` | 上传和结果展示 UI | 120-180 |
| `app/api/generate/route.ts` | API 入口、鉴权、扣 credits、调用模型 | 120-180 |
| `lib/image-provider.ts` | Replicate/BFL/OpenAI provider wrapper | 80-140 |
| `lib/prompts/product-image.ts` | 三类图的 prompt 模板 | 60-100 |
| `lib/credits.ts` | 成本估算、扣费流水 | 60-100 |
| `db/schema` 或 ORM migration | jobs / outputs / ledger | 40-80 |

合计：约 **480-780 LOC，5-6 个核心文件**。若不接支付、只做手动 credits demo，可压到 300-450 LOC。

## 3. 单张成本与 Credits 定价

### 基础成本假设

按 2026-05-19 公开价格：

- Replicate FLUX 1.1 Pro：$0.04 / 输出图。
- Replicate FLUX dev：$0.025 / 输出图。
- BFL FLUX.2 Pro：from $0.03 / 输出图。
- BFL FLUX.2 Klein：from $0.014-$0.015 / 输出图。
- OpenAI gpt-image-1：低/中/高质量方图约 $0.02 / $0.07 / $0.19。

### 常见生成包成本

| 生成包 | 模型 | 输出数 | API 成本 | 建议售价 |
|---|---|---:|---:|---:|
| 快速试图 | FLUX.2 Klein | 4 | $0.056-$0.060 | $0.40-$0.80 |
| 标准产品图 | FLUX.2 Pro / Replicate FLUX Pro | 4 | $0.12-$0.16 | $1.00-$2.00 |
| 高质量广告图 | OpenAI medium | 4 | $0.28 | $2.00-$4.00 |
| 高质量精修 | OpenAI high | 4 | $0.76 | $5.00+ |

### Credits 可行性

建议前期定义：**1 Credit = $0.10 零售价**。

- 标准 4 张图成本 $0.16，向用户收 10 Credits = $1.00，毛利约 $0.84，毛利率约 84%（未计支付手续费、存储、失败重试、获客）。
- 若用户反复重试，每次都真实消耗 API 成本，必须把“重生成”也扣 credits，不能无限免费重试。
- 白底图如用传统抠图 + 合成，成本可能低于生成图，建议作为低价入口：2-3 Credits / 张。
- 高质量广告图不要混在低价包里，必须单独档位，否则 OpenAI high 的 $0.19/张会吃掉毛利。

## 4. 技术风险清单

| 风险 | 影响 | 应对 |
|---|---|---|
| 商品主体不一致 | SKU 变形、颜色/Logo 错误，影响商用可信度 | 上传原图作为强参考；结果页强制人工确认；高风险类目加“非最终商用图”提示 |
| 白底图质量 | 纯生成可能改商品细节 | 白底图优先用分割/抠图/背景替换，不用文生图重画主体 |
| 背景融合 | 商品光影、透视和场景不自然 | prompt 固定摄影规则；生成多张候选；后续加局部编辑/inpaint |
| 多尺寸适配 | Amazon/Shopify/TikTok 广告尺寸不同 | 先生成主图，再裁切/扩图；存储尺寸元数据 |
| 平台审核 | 夸张广告、误导图、侵权素材可能违规 | 内置平台模板和禁用词；保留用户确认和申诉链路 |
| 成本失控 | 用户多次点击、失败重试、队列重复执行 | job 幂等键；生成前扣费/预授权；失败按原因返还 |
| 延迟体验 | 图片生成 5-30 秒，用户等待焦虑 | 异步 job 状态、进度 skeleton、完成通知；首屏展示低成本快速版 |
| API 供应商变更 | 模型下线、价格变化、限流 | provider 抽象层；记录每次 job 的 provider/model/version/cost |
| 用户上传风险 | 成人、侵权、敏感图 | 上传前审核；日志保留最小化；明确服务条款 |

## 5. 推荐落地路径

1. 第一周只做 Demo：Replicate `flux-1.1-pro` 或 BFL `FLUX.2 [pro]`，支持“生活场景图 / 广告图”；白底图用 remove-bg 合成。
2. 定价测试：免费给 20 Credits；标准生成 10 Credits / 4 张；高质量广告图 25-50 Credits / 4 张。
3. 验证指标：上传完成率、生成完成率、用户下载率、二次重试率、每付费用户平均 API 成本。
4. 生产前必须补：内容审核、job 幂等、失败退款、对象存储生命周期、provider 成本监控。

## 6. 最终建议

技术上可行，且适合用 Next.js 做 1-2 周内的 MVP。不要一开始自托管模型，也不要把所有生成都走高价模型。最佳组合是：

- **低成本/快速预览**：BFL FLUX.2 Klein 或 FLUX schnell。
- **默认付费质量**：FLUX.2 Pro / Replicate FLUX 1.1 Pro。
- **高价精修/广告文字**：OpenAI `gpt-image-1` medium/high。
- **白底主图**：抠图 + 合成优先，生成模型只做辅助。
