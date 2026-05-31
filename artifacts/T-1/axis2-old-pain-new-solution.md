# T-3｜第2轴扫描：旧痛点 + 新解法

日期：2026-05-19
角色：worker_product
状态：Strategy Package（仅场景扫描；未做产品实现；未拿到 Product Lab 执行回执）

## 结论先行

老板这条轴可优先看 4 个场景：
1. 跨境小卖家的多语种售前/售后客服
2. Amazon/Shopify 小卖家的产品图与广告素材生产
3. 出海视频创作者/课程团队的多语种配音与字幕本地化
4. 亚马逊/EU 小卖家的合规材料预检与申诉材料整理
5. 小微老板/自由职业者的票据记账与报税前整理

共同特征：过去依赖“人工服务商/专业岗位/外包项目制”，单次成本高、等待慢；现在 LLM + OCR + 图像/语音模型可以先给 80 分结果，人类只做抽检与高风险复核。最适合老板今天验证的是“具体任务包”，不要先做大平台。

## 场景 1：多语种客服与商品问答

- 旧痛点：英文/小语种客服贵，培训慢；旺季消息堆积时，小卖家本人不懂西语/德语/日语，只能复制翻译，回复慢且容易误解退换货规则。
- 新解法：用低价 LLM API + 店铺 FAQ/物流政策微调或 RAG，先自动回复 30%-45% 常见问题，复杂售后再转人工。
- 具体痛的人：深圳坂田做蓝牙耳机 Shopify 独立站的表弟，晚上 11 点还在回巴西客户“耳机一边没声/多久到货/能否退货”，英文能看懂但葡语不行。
- 他这周最多愿付：¥99-299/周；若能少招一个兼职客服、降低差评，旺季可到 ¥499/周。
- 去哪找前 10 个：Shopify/独立站卖家微信群、深圳坂田/华强北跨境卖家群、雨果网/卖家之家评论区、Facebook Ads/Shopify Reddit 中抱怨客服负担的帖子、现有做蓝牙/小家电/汽配的朋友名单。
- 最小验证动作：拿 1 家店的 50 条历史客服消息，做“自动草稿 + 人工确认”演示；指标看首响时间、人工改写比例、是否减少差评风险。
- 反证：如果客户问题高度依赖订单系统、售后判责和平台规则，纯聊天机器人价值不够，必须接订单/物流/退款数据。
- 证据：2026 年小团队 AI 客服工具已出现低于 $100/月方案，Tidio 等方案宣称约 30%-45% 对话可自动处理；SiteGPT 类文章也把 SMB AI 客服预算写到 $100/月以内。

## 场景 2：产品图 / 广告素材批量生成

- 旧痛点：拍摄棚、摄影师、模特、修图和多平台尺寸适配贵；小卖家每个 SKU 上新都拖，广告素材 A/B 测试做不起。
- 新解法：手机基础图 + AI 背景/场景/模特/尺寸变体；真人摄影只保留 hero 图，高频 catalog 和广告图用 AI 批量生成。
- 具体痛的人：义乌做宠物饮水机的夫妻档，1688 拿样后要上 Amazon、TikTok Shop、独立站，每个颜色都要白底图、生活场景图和广告图，但找摄影棚一次就几千元。
- 他这周最多愿付：¥199-699/周；若一次性处理 30-50 个 SKU，可接受 ¥1,999-4,999 项目包。
- 去哪找前 10 个：义乌/深圳跨境摄影群里嫌贵的卖家、Amazon FBA Reddit、Shopify 卖家群、TikTok Shop 服务商群、1688 源头工厂想出海的老板。
- 最小验证动作：找 3 个真实 SKU，每个产出 1 张白底、3 张生活场景、3 张广告图；让卖家投 ¥100 小预算测 CTR 或至少做盲评。
- 反证：首饰、服装纹理、Logo 细节、透明材质容易失真；若平台审核要求极严，仍需真人摄影兜底。
- 证据：2026 年多篇电商 AI 产品摄影成本对比指出，AI 产品图约可降低 80%-95% 成本；传统拍摄常见 $200-$5,000/session 或 $300-$800/product，而 AI 工作流可能降到每图 $0.10-$2 或每产品几十美元级。

## 场景 3：视频/课程/短剧的多语种字幕与配音本地化

- 旧痛点：找翻译、配音、字幕、时间轴和审校很慢；创作者想测英语、西语、印尼语市场，但单语种本地化成本高，先投不起。
- 新解法：ASR 转写 + LLM 翻译改写 + AI 配音/字幕 + 人工抽检，先把 80 分版本推到 YouTube/TikTok/课程试销页验证。
- 具体痛的人：杭州做 AI 办公课的个人讲师，中文课卖得动，但想测东南亚英语版；每节 20 分钟视频找人工翻译配音太贵，自己英语口音又不稳。
- 他这周最多愿付：¥299-999/周；若能验证海外课单页收邮箱或首批订单，可接受 ¥3,000-8,000 的 10 节课本地化包。
- 去哪找前 10 个：小红书/知识星球课程主、B 站/YouTube 中文创作者、剪映/CapCut 创作者群、出海短剧/短视频服务商群、Product Lab 里有内容资产但没海外版本的项目。
- 最小验证动作：选 3 条已有热视频，各做英语+西语字幕/配音版，发到新频道或广告落地页；看完播、评论、邮箱订阅/试购。
- 反证：强人设内容、专业术语课程、品牌声线要求高，AI 直出可能损害信任；必须有母语抽检。
- 证据：2026 年视频本地化资料显示 AI-assisted dubbing/localization 已把翻译、配音、审校流水线化；Linguana 等模式甚至用收益分成降低创作者前期现金成本。

## 场景 4：Amazon / EU 合规材料预检与申诉包整理

- 旧痛点：CE/GPSR/成分声明/测试报告/平台申诉材料难懂；找合规顾问贵，小卖家出问题时又很急，Listing 被下架一天就是损失。
- 新解法：AI 读取平台通知、测试报告、标签图、Listing 文案，先做缺口清单、申诉草稿和材料命名规范；高风险结论再给合规顾问复核。
- 具体痛的人：东莞做儿童夜灯的 Amazon.de 小卖家，突然收到 GPSR/CE 相关审核邮件，不知道缺的是欧代信息、标签图还是测试报告字段。
- 他这周最多愿付：¥299-999/周；如果正在被下架、日销损失明显，单次申诉包可接受 ¥1,500-5,000。
- 去哪找前 10 个：AmazonFBA / FulfillmentByAmazon Reddit 合规帖、亚马逊欧洲站卖家群、深圳/东莞 CE 检测服务商客户群、被 Listing suppression 卡住的卖家论坛、GPSR/欧代服务商评论区。
- 最小验证动作：收 5 封真实平台合规通知，输出“缺口表 + 材料清单 + 申诉草稿”；让卖家或合规顾问判定是否节省沟通时间。
- 反证：不能承诺法律/合规最终正确；若模型误读法规，风险比省钱更大，定位必须是“预检/整理/转交顾问”，不是替代律师或检测机构。
- 证据：2026 年 Amazon 卖家社区仍有大量合规文件被拒、Listing 下架、AI/自动化审核循环的抱怨；也已有创业者围绕 EU/Amazon compliance checker 找 beta/客户。

## 场景 5：票据记账与报税前整理

- 旧痛点：小老板和自由职业者每月手动整理发票、收据、银行流水；请兼职会计/记账员不便宜，自己做又拖到报税前崩溃。
- 新解法：微信/Telegram/邮箱丢票据图片或 PDF，OCR + LLM 自动抽取商户、金额、税额、类别、币种，导出给 QuickBooks/Xero/Excel，人工只复核异常。
- 具体痛的人：广州做 Etsy 手作饰品的自由职业者，每周有国际物流、材料采购、广告费、平台费，报税前才发现收据散在邮箱、相册和聊天里。
- 他这周最多愿付：¥49-199/周；如果能直接交给会计报税，月费 ¥199-499 可试。
- 去哪找前 10 个：Etsy/Shopify 小卖家群、自由职业者社群、跨境物流/广告投手群、Xero/QuickBooks 中文用户群、Reddit smallbusiness/bookkeeping 里抱怨 receipt chaos 的用户。
- 最小验证动作：收 100 张真实收据/账单截图，输出分类表和异常项；让用户对比手工整理耗时。
- 反证：税法判断、跨境税务、个人/公司混账仍需专业会计；AI 适合做资料归集和初分，不适合直接给税务结论。
- 证据：2026 年 AI bookkeeping 资料普遍把价值点放在 receipt scanning、automatic categorization、bank reconciliation；也有小项目在 Reddit 招募 freelancer/small business beta testers。

## 老板今天该做什么

- 先选 1 个“本周能见 10 个真人”的场景，不要同时开 5 个。
- 推荐优先级：产品图素材包 > 多语种客服 > 合规预检 > 视频本地化 > 票据记账。
- 原因：产品图和客服最贴近 Product Lab 的“产品与收钱”，交付边界清楚，7 天内可用样品/历史消息做演示；合规预检客单更高但责任边界重。
- 最小验证动作：让 manager/Product Lab 今天找 3 个真实卖家，各给 1 个 SKU 或 50 条客服记录；worker_product 可在 30-60 分钟内转成落地页价值主张和访谈问题。
- 预计收益：不是立刻做 SaaS，而是先找到“愿为省钱/省时间付费”的窄任务包；如果 10 个目标用户里 3 个愿付试点费，就进入 Product Lab 原型。
- 截止时间：建议 24 小时内定 1 个场景，48 小时内完成 10 人触达脚本和首个 demo 样例。

## 接手建议给 Product Lab

- owner 建议：Product Lab 先接“产品图素材包”或“多语种客服草稿器”。
- worker_product 可继续负责：目标用户画像、首屏文案、FAQ、定价锚点、反证问题、访谈脚本。
- Product Lab 需要负责：真实样品/历史数据接入、demo 产出、用户试点费收款路径。

## 来源与核验状态

- 已实时检索 2026-05-19 可访问网页/搜索摘要；未逐一验证每家工具官方价格页，金额用于方向判断，不作为采购报价。
- AI product photography cost guides: Digital Applied, P20V, Lamazi Studio, TriedByHumans, Reddit ecommerce/FacebookAds/GrowthHacking discussions.
- AI support tools for small businesses: Twig 2026 small-team AI support guide, SiteGPT 2026 AI customer support guide.
- Translation/localization cost context: Transphere AI translation cost analysis, IAMT Q1 2026 AI video localization material, Linguana company profile.
- Contract/compliance/legal AI context: Docusign 2026 AI contract review release, Caversham Digital SME contract review article, Amazon seller Reddit compliance threads, EU compliance checker beta/customer-call thread.
- AI bookkeeping context: BookZero 2026 AI bookkeeping guide, Gennai 2026 guide, Reddit SideProject receipt/bookkeeping beta tester post.
