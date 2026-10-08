# arivx 论文研究体系 · 复现规格书（目标-任务分解式）
> 读者：另一个 code agent，无法访问原实现。目标是复现**行为等价**的体系：5 个能力单元（获取 / 精读 / 追踪 / 对比 / 知识库）+ 1 个编排者 + 1 个共享协议层 + 一组确定性工具。
> 约束等级：**[规范约束]** 外部可观测契约，必须逐字遵守；**[行为必需]** 不遵守则行为不等价；**[推荐做法]** 原实现做法，可替换。
> 本文用大写代号指代组件与数据区，复现者自行映射到自己宿主的路径与命名。

---

## 1. 概述

**一句话定位**：一套让 LLM agent 以"批判性阅读 + claim-evidence 分离 + 可追溯锚点"为方法论，完成"获取论文 → 精读 → 追踪研究脉络 → 多篇对比 → 沉淀为原子知识网络"的研究工作流体系。

**解决的问题与场景**

- LLM 读论文常见失败：摘要复述冒充理解、虚构页码/数值、把个人判断说成论文观点、高分掩盖实验缺陷、"A 比 B 高 2 点"即下结论。本体系用**可机械校验的锚点**、**强制字段**、**非补偿红旗**、**显著性规则**把这些失败变成可检出的错误。
- 场景：单篇精读、方向综述（survey 前期）、相关工作对比、跨会话持续积累的个人知识库。

**非目标**

- 不绕过付费墙、不使用非官方镜像下载非 OA 论文。
- 不做"论文质量"的绝对判定：影响力评分只用于池内排序，显式声明"影响力 ≠ 质量"。
- 确定性工具不做任何语义判断；语义判断全部由模型完成，工具只做 IO / 计数 / 校验。
- 不追求文献穷尽，追求"饱和"并诚实标注局限。

---

## 2. 行为契约

### 2.1 触发条件 [规范约束]

宿主须支持"能力单元声明 = 名称 + 描述（含触发词）"的接入方式。各单元的名称与描述原文见 §4.1。触发方式两种：显式命令（`/paper-fetch` 等）或自然语言触发词命中。编排者在用户意图模糊或跨多个单元时被调用。

### 2.2 输入规范

| 单元 | 输入 | 示例 |
|---|---|---|
| FETCH | arXiv URL / arXiv ID / DOI（可带 `doi:` 前缀）/ 本地 PDF 路径 / 标题；可批量；可带 `--refresh` | `/paper-fetch 1706.03762`、`/paper-fetch "Attention Is All You Need"` |
| READ | 论文标题、slug 或任何 FETCH 可接受的输入（未获取则先触发 FETCH） | `/paper-read vaswani2017_attention` |
| TRACE | 研究方向描述，或种子论文；可选"增量" | `/research-trace 生成式推荐中的 Semantic ID` |
| CMP | 2–N 篇论文（slug/标题） | `/paper-compare yi2025_recgpt yi2025_recgpt_v2` |
| KB | 子命令 + 参数（见 §4.12） | `/paper-kb link multi-head-attention transformer part-of --by self --evidence "§3.2"` |
| ORCH | 自然语言研究需求 | "我想深入了解 RAG 的长上下文替代问题" |

### 2.3 输出规范

| 单元 | 产物（数据区代号） | 关键可观测特征 |
|---|---|---|
| FETCH | D-PAPER/{slug} 下：PDF、META（JSON，schema `arivx.paper.v1`）、FULLTEXT（`pdftotext -layout` 纯文本）、RAW（API 原始响应） | 同输入必得同 slug；`access_level` 四值之一 |
| READ | D-LIT/{slug}（frontmatter `arivx.note.v1` + 15 区块正文） | 📖 均带锚点；claim 表；红旗表在评分表之前 |
| TRACE | D-EXPORT 下 trace 报告（8 章）+ D-RL 阅读清单 + 引用图 JSON + 主题日志追加 | PRISMA 计数守恒；里程碑带原始数值 |
| CMP | D-EXPORT 下对比报告（frontmatter `arivx.compare.v1`）+ 张力卡草案 | 每个"谁更好"带显著性标签 |
| KB | D-CARD / D-TENS / D-MOC 卡片；D-GEN 生成物（索引/反链/图谱/统计） | 关系单向写入，反链只由工具生成 |
| ORCH | 上述产物 + D-LOG-TOPIC 条目 + D-LOG-JOURNAL 一行 | 每阶段 ≤10 行中间摘要 |

**输出示例（FETCH 汇总，批量）** 
```✅ full-text (2): vaswani2017_attention, devlin2019_bert ⚠️ oa-pdf (1): xxx2021_scan
— PDF 已存，文本提取失败，建议 OCR ⚠️ abstract-only (1): yyy2023_paywalled — 非 OA，请提供 PDF ❓ 需人工 (0) ⏭ 跳过的候选 URL:
devlin2019_bert
<url>
原因: 非 PDF（魔数校验失败）
```
  **输出示例（READ 门控，强制格式）** 
```门控：deep 理由：方法将被复现，且为该方向里程碑（领域引用 top 10%） 置信度：高
```

### 2.4 边界行为 [行为必需]

| 情况 | 行为 |
|---|---|
| 目标数据区为空 | 视为新项目正常态，如实输出"空"，不报错 |
| META 已存在且距今 <7 天 | 直接复用，零网络请求；仅 `--refresh` 强制重取 |
| 非 OA 论文 | 不下载；`access_level: abstract-only`，向用户索要 PDF；**不中断流程** |
| 用户提供本地 PDF | `access_level: user-provided`；元数据检索不到也照常建档，缺失字段填 `unknown` |
| PDF 存在但文本提取失败（扫描版/加密） | 保留 PDF，`access_level: oa-pdf`，提示 OCR；锚点自动校验跳过并标注 |
| `abstract-only` | READ 只产出 5C + 骨架 + 摘要级总结；禁止全文级论断；claim strength ≤1；locator 只能是 `Abstract` |
| 身份解析多候选/置信度相近 | 列候选让用户选，禁止自行拍板 |
| 学术 API 搜索端点不可用 | 降级（见 §4.2），产物标注"检索源受限"，不阻塞 |
| 批量中部分失败 | 不逐篇询问；全批结束后一次性汇总询问 |
| 对比中某论文无方差报告 | 涉及该论文的所有优劣判定降级为"不可判定（未报告方差）" |
| 矛盾矩阵为空 | 有效结论，如实报告；禁止强行聚类凑矩阵 |
| 用户中断 | 立即写收场日志，"下一步"写明中断点；下次从断点续跑 |

---

## 3. 目标-任务分解树

### 3.1 组件代号表

| 代号 | 类型 | 职责 |
|---|---|---|
| ORCH | 指令 | 编排者：盘点、路由、CHECKPOINT、日志、批量调度 |
| FETCH / READ / TRACE / CMP / KB | 指令 | 五个能力单元的主流程 |
| PROTO-ID / PROTO-CLAIM / PROTO-ANCHOR / PROTO-FM / PROTO-API | 协议 | 共享契约：身份、claim、锚点、frontmatter、数据源 |
| R-5C / R-CRIT / R-CLAIM / R-REPRO / R-RUBRIC / R-TMPL | 参考 | READ 的子手册：5C、批判清单、claim 提取、复现审计、评分量规、笔记模板 |
| R-SEARCH / R-PRISMA / R-SNOW / R-CITE / R-MILE | 参考 | TRACE 的子手册：检索式、PRISMA、雪球、引用图、里程碑 |
| R-ARCH / R-ALIGN / R-CONTR / R-FAIR / R-SIG | 参考 | CMP 的子手册：原型、claim 对齐、矛盾矩阵、公平审计、显著性 |
| R-LAYER / R-LINK / R-MAT / R-TENS | 参考 | KB 的子手册：三层笔记、关系本体、成熟度、张力卡 |
| T-RESOLVE / T-OA / T-ARXIV / T-UPW / T-PDF | 工具 | 身份解析、OpenAlex/arXiv/Unpaywall 封装、PDF 下载+抽取 |
| T-SNOW / T-CITE / T-SCORE | 工具 | 雪球迭代、引用 DAG、里程碑评分 |
| T-KBBUILD / T-KBCHECK / T-KBQUERY / T-KBCACHE | 工具 | 生成物构建、一致性+锚点校验、查询、增量变更缓存 |
| PERM | 配置 | 宿主权限白名单 |  数据区代号：D-PAPER（每篇论文资产）、D-LIT（文献笔记）、D-CARD（永久卡片，扁平）、D-TENS（张力卡）、D-MOC（主题枢纽）、D-GEN（生成物）、D-INBOX（临时笔记）、D-LOG-TOPIC（按方向日志）、D-LOG-JOURNAL（时间线日志）、D-RL（阅读清单）、D-EXPORT（导出）。

### 3.2 分解树全文

```N0 让研究者以批判性、可追溯的方式读论文、追踪演进、沉淀知识 ├─ N1 编排研究工作流（ORCH） │
├─ N1.1 会话开场盘点资产与日志 → CHECKPOINT① │
├─ N1.2 按路由表把意图映射为能力单元链 │
├─ N1.3 批量调度（下载并行、精读串行） │
├─ N1.4 每篇精读后执行固定 4 动作 │
├─ N1.5 周期 CHECKPOINT（每 3–5 篇）与产物前 CHECKPOINT │
└─ N1.6 会话收场 / 中断时写日志 ├─ N2 获取论文并归一化（FETCH） │
├─ N2.1 识别输入类型 │
├─ N2.2 缓存命中判定 │
├─ N2.3 身份解析（4 策略链） │
├─ N2.4 版本去重并选定主记录 │
├─ N2.5 身份确认 CHECKPOINT② │
├─ N2.6 生成 slug 并处理冲突 │
├─ N2.7 收集 PDF 候选并按回退链下载 │
├─ N2.8 校验 PDF 有效性 │
├─ N2.9 抽取全文并校验 │
├─ N2.10 判定 access_level │
├─ N2.11 写 META 与 RAW │
└─ N2.12 批量汇总报告 ├─ N3 单篇精读（READ） │
├─ N3.1 加载本地资产与协议 │
├─ N3.2 第一遍：5C + 论文骨架 │
├─ N3.3 门控决策 CHECKPOINT③ │
├─ N3.4 第二遍：问题形式化 / 方法拆解 / Related Work 演进线 │
├─ N3.5 第三遍：实验设置与结果 │
├─ N3.6 提取 claim │
├─ N3.7 为 claim 定位证据并定强度 │
├─ N3.8 双动作批判 + 终极问题 │
├─ N3.9 复现性审计 │
├─ N3.10 判红旗 → 六维评分 │
├─ N3.11 虚拟重实现（仅 deep） │
├─ N3.12 按模板组装笔记 │
├─ N3.13 锚点自检 │
└─ N3.14 产出蒸馏清单 ├─ N4 追踪研究脉络（TRACE） │
├─ N4.1 方向锁定 CHECKPOINT │
├─ N4.2 前置纳排标准 │
├─ N4.3 执行多检索式并逐字记录 │
├─ N4.4 去重得种子集 S0 │
├─ N4.5 执行一轮双向雪球 │
├─ N4.6 终止判断 │
├─ N4.7 PRISMA 计数与守恒校验 │
├─ N4.8 构建引用层 DAG 并计算结构指标 │
├─ N4.9 补语义层边 │
├─ N4.10 里程碑评分与旗标提档 │
├─ N4.11 时间线演进 / 前沿 / 待验证假设 │
├─ N4.12 完备性自评 │
├─ N4.13 输出产物 CHECKPOINT │
└─ N4.14 增量模式 ├─ N5 多篇对比（CMP） │
├─ N5.1 对比设定 CHECKPOINT │
├─ N5.2 信息对齐提取 │
├─ N5.3 原型判定 │
├─ N5.4 claim 对齐聚类 │
├─ N5.5 矛盾判定并填矩阵 │
├─ N5.6 生成张力卡草案 │
├─ N5.7 自适应维度对比 │
├─ N5.8 公平性审计 │
├─ N5.9 显著性判定 │
├─ N5.10 深度差异分析与综合结论 │
└─ N5.11 输出 CHECKPOINT └─ N6 知识库维护（KB）
├─ N6.1 status
├─ N6.7 maturity 迁移
├─ N6.2 query
├─ N6.8 index（构建生成物）
├─ N6.3 add（先查重）
├─ N6.9 check（8 项）
├─ N6.4 蒸馏建卡
├─ N6.10 锚点校验
├─ N6.5 link
├─ N6.11 mocs build/edit
├─ N6.6 张力卡生命周期 └─ N6.12 claims / contradictions / stale / graph
```

### 3.3 叶子任务表
> 标注"①②③…"的叶子是**无分支的固定动作序列**：按序全部执行、不可部分跳过，视为一个原子动作。其余叶子中的"若…则…"均已给出全部分支。  **N1 编排（ORCH）

| 节点 | 输入 | 动作 | 完成标准 | 组件 |
|---|---|---|---|---|
| N1.1 | 用户需求；全部数据区 | 用文件匹配列出各数据区、用内容检索只抽取 frontmatter（不全文读）；读相关方向日志，输出"研究状态摘要"（已精读/活跃问题/上次下一步/未决张力/上次判断）；询问复用或重来 | 摘要 ≤10 行且用户已选择 | ORCH, D-LOG-TOPIC |
| N1.2 | 用户意图 | 查 §4.13 路由表，得到单元调用序列 | 序列确定并告知用户 | ORCH |
| N1.3 | 待获取清单 | ①展示批量清单，CHECKPOINT④ 确认范围 ②用子 agent 并行下载，每批 ≤8 篇，子任务只做下载+元数据不精读 ③全部下载结束后汇总失败项，CHECKPOINT⑤ 一次性询问处理方式 ④精读按清单顺序串行 | 所有篇目有 access_level；失败项汇总成一次询问 | ORCH, FETCH |
| N1.4 | 一篇刚完成的笔记 | ①触发 KB 蒸馏建卡并重建索引 ②比对新 claim 是否命中已有张力卡 parties ③追加方向日志 ④输出一句话总结 + 与已读论文的关系 | 4 动作均有产出 | ORCH, KB |
| N1.5 | 进度 | 默认每完成 3 篇精读（用户可调为 3–5）、以及任何产物写盘前，询问继续/调方向/产物格式与去向 | 用户已回答 | ORCH |
| N1.6 | 会话结果 | 方向日志追加条目（动作/新发现/判断的变化/未解决问题/下一步）；时间线日志追加一行；无判断变化写"无" | 两处日志均已追加，未编造 | ORCH, D-LOG-* |

**N2 获取（FETCH）**

| 节点 | 输入 | 动作 | 完成标准 | 组件 |
|---|---|---|---|---|
| N2.1 | 原始输入串 | 按 §4.3 规则分类为 arXiv URL / arXiv ID / DOI / 本地 PDF / 标题；剥离 URL 前缀、`doi:`、`arxiv:`、`.pdf` 后缀 | 得到 (类型, 归一化 ID) | FETCH, T-RESOLVE |
| N2.2 | 归一化 ID | 若已有 META 且 `fetched_at` 距今 <7 天且无 `--refresh` → 直接返回 | 命中则零网络请求 | FETCH |
| N2.3 | (类型, ID) | 依次尝试：DOI 精确 → arXiv ID（arXiv API + OpenAlex 交叉验证）→ 归一化标题搜索 → 模糊标题+一作姓+年份；任一级命中仍进入 N2.4 | 得到候选记录集合（可多条） | T-RESOLVE, T-OA, T-ARXIV |
| N2.4 | 候选集合 | 按归一化标题（小写/去标点/折叠空白）成簇；簇内按 (有正式 venue, 最早年份, 引用数, 有 OA PDF) 选主记录；引用数取簇内最大值并另存主记录原值；其余 ID 入 `alternate_ids` | 恰一条主记录 | T-RESOLVE |
| N2.5 | 主记录 | 展示元信息卡，三选一：继续下载 / 改标识 / 只存元数据；多候选或置信度相近时列候选让用户选 | 用户已确认 | FETCH |
| N2.6 | 主记录 | 按 §4.3 生成 slug；与已有 slug 冲突且非同一论文 → 追加 `b`/`c` | slug 唯一且可由同输入重现 | PROTO-ID |
| N2.7 | 主记录 | 按 §4.2 PDF 回退链逐级尝试；跳过 `arxiv.org/abs/*` 着陆页；带自定义 UA | 得到通过 N2.8 的 PDF，或全部失败 | T-PDF, T-UPW |
| N2.8 | 下载文件 | 校验：>10 KB 且首 4 字节为 `%PDF`；不通过则自动下一个候选 | 合格或已降级 | T-PDF |
| N2.9 | 合格 PDF | `pdftotext -layout` 抽取；校验非空、>100 字符、前 2000 字符为可读自然语言（[推荐] 判据：字母/汉字/空白占比 ≥ 60%） | 得到 FULLTEXT 或判定提取失败 | T-PDF |
| N2.10 | N2.7–N2.9 结果 | 文本有效→`full-text`；有 PDF 无文本→`oa-pdf`；无 PDF→`abstract-only`；用户给 PDF→`user-provided` | 字段为四值之一 | FETCH |
| N2.11 | 全部结果 | 写 META（§4.4）；RAW 落盘实际命中的原始响应（含候选列表，用于审计选中理由）；记录 `sources`、`fetched_at` | META 通过 schema 必填校验 | FETCH |
| N2.12 | 批量结果 | 按 full-text / oa-pdf / abstract-only / 需人工 四段输出 | 计数之和 = 输入数 | FETCH |

**N3 精读（READ）**

| 节点 | 输入 | 动作 | 完成标准 | 组件 |
|---|---|---|---|---|
| N3.1 | slug | 读 META / FULLTEXT / PDF；显式加载 PROTO-CLAIM、PROTO-ANCHOR、PROTO-FM；资产缺失则先调用 FETCH；禁止用网页抓取代替本地资产 | access_level 已知，三协议已加载 | READ |
| N3.2 | 全文（或摘要） | 填 5C（§4.7）+ 骨架五段（问题/方法/假设/实验/结论）；Contributions 用"声称"措辞；Correctness 只标可疑点不下结论 | 5C 五项齐全 | READ, R-5C |
| N3.3 | 5C | 按 §4.7 选 弃读/skim/read/deep，按强制格式输出（门控/理由/置信度）；置信度低时先按低档执行，第二遍后重评；档位变更须记原因 | 门控已输出且用户确认；"弃读"只留 5C 记录不入库 | READ |
| N3.4 | 全文 | 问题形式化；方法拆解树（动机/架构/核心模块/关键公式/训练策略/复杂度）；Related Work 提取传播脉络而非罗列引文 | 三小节完成 | READ |
| N3.5 | 全文 | 实验设置表、主要结果、消融 | 三小节完成，数值均带锚点 | READ |
| N3.6 | 全文 | 5 步：标记有真值陈述 → 过滤纯描述 → 按出现顺序分配 `{slug}#C{n}` → 五类分类 → 进入 N3.7；判据"这句话可以是错的吗"；**必须主动提取 negative 类**（Limitations / future work / 退化案例） | 每条 claim 字段齐全（§4.5），falsifier 写不出的移入 💡 | READ, R-CLAIM, PROTO-CLAIM |
| N3.7 | claim | 为每条证据写定位符 + ≤25 词连续原文引文 + kind + strength；claim strength 取所有证据**最小值**；按 access_level 封顶 | 每条 📖 引文可在 FULLTEXT 中定位 | R-CLAIM, PROTO-ANCHOR |
| N3.8 | 方法+实验 | 按五组（假设/方法/实验/结论/复现）提质疑，每条质疑必附该论文最强辩护（五类辩护之一），再给裁决（成立/有条件成立/不成立/无法判定）与严重度（致命/重要/次要）；回答终极问题"如果作者是错的，最可能错在哪一环" | 每条质疑有位置、辩护、裁决、严重度 | R-CRIT |
| N3.9 | 全文 | 8 项审计逐项判定并给证据位置；成本估计（GPU 数×小时×轮数，调参×2–3）；repro_score 0–10；最小复现实验四段式；失败模式预判 | 审计表 8 行齐全 | R-REPRO |
| N3.10 | N3.5–N3.9 | 先判 6 个非补偿红旗（附证据位置，触发对应分维上限），再打 6 维 1–5 分，每维说明"为何是此分而非相邻分"；综合评语不是平均 | 红旗表置于评分表之前 | R-RUBRIC |
| N3.11 | deep 档 | 写隐藏决策清单：未言明但实现必须决定的细节 | ≥3 条；不足则回读方法章节后重写一次，仍不足则降档为 read 并在笔记记录原因 | READ |
| N3.12 | 全部区块 | 按 §4.9 模板 15 区块顺序组装；skim 档仅 1→2→3→4→14 + 结论可信度；frontmatter 按 note.v1，`access_level`/`coverage` 从 META 透传，`read_mode` 与门控一致 | 文件通过 schema 校验 | R-TMPL, PROTO-FM |
| N3.13 | 笔记 | 调用锚点校验（N6.10）；❌ 必须修正或删除该 📖 | 无 ❌ 项 | T-KBCHECK |
| N3.14 | 笔记 | 蒸馏清单：每项给原子判断 + 卡片类型 + `[[链接]]` + 理由；张力线索标冲突类型 | 清单交由 KB（N6.4）消费 | READ |

**N4 追踪（TRACE）**

| 节点 | 输入 | 动作 | 完成标准 | 组件 |
|---|---|---|---|---|
| N4.1 | 方向描述 | 输出范围卡片（方向/边界/时间跨度/已有资产计数/上次断点/目标）；四选一：全景 / 聚焦近 N 年 / 聚焦子方向 / 增量 | 用户已选 | TRACE |
| N4.2 | 范围 | 在看到任何检索结果**之前**写定纳排标准表；事后调整只能追加记录并注明影响 | 标准表已落盘 | R-SEARCH |
| N4.3 | 标准 | 五类检索式（核心词/同义词/上位词/下位词/综述限定）分别执行，每式按 §4.10 字段逐字记录；单式命中 >10000 时收窄检索式（时间窗/类型）或取引用降序 top-N 并说明取法 | 每条检索式记录字段齐全 | R-SEARCH, T-OA |
| N4.4 | 各检索式结果 | 合并去重（DOI → OpenAlex ID → 归一化标题+年份差≤1 → arXiv ID）；取 top-N（默认 20）为种子 S0；综述单独标记，只作交叉验证不入主线 | S0 确定 | R-SEARCH |
| N4.5 | 当前池 S_i | 后向取引用列表、前向取 `cites:` 结果；工具做硬筛（时间/类型/语言/标题存在）与池内+轮间去重；模型做语义相关性判定并写回；不确定标 `uncertain` 进入下一阶段 | 本轮 候选/纳入/排除理由 计数已记录 | T-SNOW, R-SNOW |
| N4.6 | 本轮计数 | 终止条件任一：本轮纳入 = 0（饱和，首选）/ 轮数达上限（默认 3）/ 池超上限（默认 500）/ API 预算耗尽；非饱和终止须显式 ⚠️ 标注 | 终止原因已记录 | T-SNOW |
| N4.7 | 全过程计数 | 按 §4.10 填 PRISMA 字段与排除码分布；校验每阶段 `进入 = 排除 + 通过`；被排除但引用 >1000 的单列清单+理由 | 守恒成立 | R-PRISMA |
| N4.8 | 终池 | 节点装载（优先复用本地 META）→ 池内连边（引用列表 ∩ 池）→ 按 §4.11 破环 → 计算入/出度、层级、桥接节点、弱连通分量、共被引、文献耦合 | 图 JSON 符合 §4.11 | T-CITE, R-CITE |
| N4.9 | 图 + 笔记 | 模型补语义层边（6 种类型），每条带 `§` 定位 + ≤25 词引文；`contradicts` 边同步生成张力卡草案；孤立弱连通分量用于反查检索盲区，不视为噪声 | 语义边全部带锚点 | R-CITE |
| N4.10 | 终池 + 图 | ①按 §4.11 公式算 impact 并池内分位数分档 ②模型/人确认工具给出的旗标候选（置 confirmed） ③若有确认的旗标则重算提档 ④写入全部原始值与 z 分 | 输出含 `tier_before_flags` / `tier_after_flags` 与免责声明 | T-SCORE, R-MILE |
| N4.11 | 分档结果 | 主线 1–3 条、转折点、支线/死胡同、技术维度对比矩阵；SOTA、活跃方向、开放问题、待验证假设清单 | 第 5–8 章完成 | TRACE |
| N4.12 | 全过程 | 四项完备性自评 + 已知局限 + 置信度（高/中/低）；禁止"该方向没有 X"，改写为"本次检索未发现 X，局限：…" | 自评段完成 | R-SEARCH |
| N4.13 | 全部产物 | 写 8 章报告 + 阅读清单 + 日志；五选一：批量下载 / 精读 / 可视化 / 更新 KB / 完成 | 用户已选 | TRACE |
| N4.14 | 上次日志 | 对池中每篇执行 `cites` + 起始日期=上次追踪日，同标准筛选；新产物按日期并存不覆盖 | 增量产物独立存在 | TRACE |

**N5 对比（CMP）**

| 节点 | 输入 | 动作 | 完成标准 | 组件 |
|---|---|---|---|---|
| N5.1 | 2–N 篇 | 显式加载三协议；展示设定卡片，四选一：全面对比 / 聚焦矛盾 / 聚焦性能 / 调整列表与原型。全面 = N5.2–N5.10 全做；聚焦矛盾 = N5.4–N5.6 完整、N5.7–N5.9 只做与矛盾簇相关的行；聚焦性能 = N5.7–N5.9 完整、N5.5 只标 direct 簇；调整 = 修改后回到本节点 | 用户已选 | CMP |
| N5.2 | 各篇 | 复用已有笔记的 claim；无笔记者优先回 READ，或临时提取（strength 上限 2，标 `⚠️ 未经精读校验`）；跨精读档位须声明"不对称比较" | 每篇 claim 列表就绪 | CMP |
| N5.3 | 各篇 | 按 §4.14 五步流程判定原型；`mixed` 走分支，禁止合并成一张大表 | 每篇有原型 | R-ARCH |
| N5.4 | 全部 claim | 按"三问全是"聚簇（可判定性/主题同一性/范围可比性），遵守四类禁合并；单论文命题移出矩阵；报告未归簇 claim 占比（[推荐] >50% 时提示"可能不可直接对比或聚类过窄"） | 命题簇确定；未归簇比例已报告 | R-ALIGN |
| N5.5 | 命题簇 | 每簇按 §4.14 六步顺序判冲突类型；矩阵每格取值+完整 claim ID；silent 显式记录 | 矩阵每格非空 | R-CONTR |
| N5.6 | 矩阵 | 按 §4.14 映射为张力卡草案（类型→初始状态） | 草案字段齐全 | R-CONTR, PROTO-FM |
| N5.7 | 原型维度集 | 三表：方法论 / 实验结果 / 适用场景；每行数值标来源（自报/第三方）、可比性（✅/⚠️/❌）、显著性 | 三表完成 | CMP |
| N5.8 | 实验设置 | 9 项审计（§4.14）：有量化阈值的项按阈值判定，其余按定性规则判定；按规则得综合等级；向显著性传递 ❌ 信号 | 9 项均有判定值，等级已定 | R-FAIR |
| N5.9 | 数值差异 | 五步：方差检查 → 效应量 vs 噪声 → 多重比较 → 协议差异量化 → 标注 显著/弱/不可判定（原因） | 每个优劣结论有标签 | R-SIG |
| N5.10 | 全部 | 哲学差异 / 技术路线 / 演进关系（只用既有关系类型）；结论可追溯至 claim ID 或审计项；审计未通过或不可判定者不得作推荐依据 | 结论无凭印象项 | CMP |
| N5.11 | 报告 | 写报告；五选一：写入张力卡 / 更新 KB / 生成可视化 / 精读缺失论文 / 完成 | 用户已选 | CMP |

**N6 知识库（KB）**

| 节点 | 输入 | 动作 | 完成标准 | 组件 |
|---|---|---|---|---|
| N6.1 | — | 读统计生成物；不存在则先跑 N6.8 | 输出计数/孤立卡/断链 | T-KBBUILD |
| N6.2 | 关键词 | 五级策略：精确 slug → aliases/中文名 → 卡片+张力卡全文 → 文献笔记全文 → 关联扩展；支持 `--type/--maturity/--json/--limit` 过滤；deprecated 默认隐藏 | 结果按 maturity 默认序排列 | T-KBQUERY |
| N6.3 | 类型 + 名称 | 先查 slug 与 aliases 重复；命中则三选一（合并/独立创建/取消）；新卡 `maturity: seed` | 无重复或用户已裁决 | KB |
| N6.4 | 蒸馏清单 | 6 步：逐项审阅 → 查重 → 更新已有卡（追加 papers / 演进表行，papers_count 交工具算）或新建 → 新卡至少 2 条 relations → 跑 N6.8 + N6.9；写入前用户确认 | 新卡通过原子性判据，check 无 FAIL | KB, R-LAYER |
| N6.5 | A, B, type | 校验 type ∈ §4.15 集合 → 校验 A、B 存在 → **只写 A 的 relations** → 更新 A.updated → 触发 N6.8；`supersedes` 派发"B 建议 deprecated"，`contradicts` 派发"评估是否需张力卡" | A 文件已改，B 未被手改 | KB, R-LINK |
| N6.6 | 冲突材料 | 先分诊：普通批评用 `contradicts` 关系即可，"同一命题相反结论且无法判断谁对"才建张力卡；创建前校验 parties 的 claim ID 存在；按 §4.16 管理状态迁移与关闭动作；open 卡同步使相关卡片 `contested` | 张力卡与卡片 maturity 一致 | KB, R-TENS |
| N6.7 | 卡片网络 | 按 §4.16 迁移表：自动项（→contested）直接改；其余工具给建议，人工确认 | 无与规则冲突的 maturity | T-KBCHECK, R-MAT |
| N6.8 | 全部卡片/笔记 | 全量扫描 frontmatter（排除生成物与隐藏文件），确定性生成索引/反链/图谱/统计（§4.17） | 重复运行输出字节一致 | T-KBBUILD |
| N6.9 | 全库 | 按 §4.17 顺序执行 8 项检查，汇总 PASS/WARN/FAIL | 退出码与结果一致 | T-KBCHECK |
| N6.10 | 📖 引文 + FULLTEXT | 按 §4.6 归一化与三级匹配；覆盖正文引文、relations.evidence、claim evidence quote、卡片引文四处；按 access_level 决定是否跳过 | 每条引文有判定 | T-KBCHECK, PROTO-ANCHOR |
| N6.11 | 变更集 | ①按内容哈希检测 changed/unchanged/new/deleted 并更新增量缓存 ②基于结构信号推荐 MOC 候选（[推荐] 结构信号：共享 relations target、共同 tags、共同 papers 的卡片簇；已有簇心优先，仅新卡无法归属时才全量重聚） ③把草案写入 MOC 自动生成标记区块，经用户精选后固化 | 缓存已更新；候选列表已输出；人工区块未被覆盖 | T-KBCACHE |
| N6.12 | slug / — | `claims <slug>` 列 claim 表；`contradictions` 按 kind 分组、direct 优先；`stale` 按阈值列陈旧卡；`graph <slug>` 以该卡为中心列出出边（relations）与入边（反链），实线=by:self、虚线=by:me、点线=by:{paper_slug}（点线为 [推荐]）；总边数 >15 时改为发布可视化页面（无该能力则输出 Mermaid） | 输出与生成物一致 | T-KBQUERY |

### 3.4 横向全局约束清单

| # | 约束 | 作用范围 | 等级 |
|---|---|---|---|
| G1 | 📖（论文原文观点，必带锚点，被机械校验）与 💡（个人理解，不需锚点，需标置信度）严格分离；禁止用 💡 包装原文观点以逃避校验 | READ, CMP, KB, TRACE 语义边 | 规范约束 |
| G2 | 不得虚构引用、页码、表号、数值；无法确认填 `unknown` 并给出获取路径；页码未确认标 `⚠️ 页码未确认` | 全体 | 规范约束 |
| G3 | `access_level` 从 META 透传到所有消费该论文的产物（同名同值）；`abstract-only` 禁全文级论断且 strength ≤1 | READ, CMP, KB, TRACE | 规范约束 |
| G4 | 跨文件引用论文只用 slug；卡片用 `[[slug]]`；claim 用完整 ID `{slug}#C{n}`（唯一例外：文献笔记自身的 claim 索引表内可写 `C{n}`）；标题/URL/DOI 只作 META 属性 | 全体 | 规范约束 |
| G5 | claim ID 一经发布不可修改、复用、重排；删除留空缺 | READ, CMP, KB | 规范约束 |
| G6 | 关系只写在源卡片 `relations`，反向链接只由工具生成；禁手写反链 | KB, READ frontmatter | 规范约束 |
| G7 | 生成物禁止手工编辑；生成物须带可机读标记（原实现：文件名以下划线 `_` 开头），所有扫描按该标记排除，避免被当作源数据 | KB 工具, 全体 | 行为必需 |
| G8 | 确定性工具只做 IO/计数/校验，不做语义判断；语义判断由模型做并写回 | 全部工具 | 行为必需 |
| G9 | 降级而非失败：任何 API 故障不阻塞主流程，降级路径与原因写入 `sources` 或产物标注 | FETCH, TRACE | 行为必需 |
| G10 | 原始 API 响应落盘，可审计"数据从哪来"、可离线复跑 | FETCH, TRACE | 行为必需 |
| G11 | 只自动下载 OA 源并记录 license | FETCH | 规范约束 |
| G12 | 中文撰写，术语首次出现括注英文并保持统一；论文正文引用格式 `Author et al., Year` | 全部笔记/报告 | 规范约束 |
| G13 | 非补偿红旗不可被高分抵消，出现即在一句话总结中体现 | READ, CMP | 行为必需 |
| G14 | 不确定时报告"不可判定/未知"，不推测；"不可判定"是有效结论 | CMP, TRACE | 行为必需 |
| G15 | 所有 CHECKPOINT 用宿主的结构化提问能力；每阶段结束输出 ≤10 行中间摘要 | ORCH 及各单元 | 行为必需 |
| G16 | 节省上下文：列目录用文件匹配、抽元数据用内容检索不全文读；笔记落盘后上下文只保留 slug + 一句话 | ORCH | 推荐做法 |
| G17 | 协议是单一事实源：各单元执行前显式加载协议，禁止把协议内容复制进单元指令 | 全体 | 行为必需 |
| G18 | 日期 `YYYY-MM-DD`；时间戳 ISO 8601 UTC；YAML 数组须同时支持行内与块式写法 | 全体 | 规范约束 |
| G19 | 联系邮箱配置缺失时须明确提示，不得静默降级；密钥类配置（S2）只从环境读取，绝不写入产物 | FETCH, TRACE | 规范约束 |
| G20 | 日志记录认知更新而非活动流水；无判断变化写"无"，禁止编造填充 | 所有写日志的单元（ORCH, TRACE, 及被编排调用的各单元） | 行为必需 |

### 3.5 关键设计决策及理由（防止错误"优化"）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 主记录排序用四元组 (有 venue, 最早年份, 引用数, 有 OA PDF)，**不能**只按被引数 | 实测标题搜索首位常是无 venue 的新年份重印记录，却聚合了原论文全部引用；按被引数选会让 slug 年份、venue、百分位全错，并污染里程碑评分 |
| D2 | 身份解析是 4 策略链，DOI 查空**不能**判定"论文不存在" | arXiv 的 DataCite DOI（`10.48550/arXiv.*`）在 OpenAlex 常查不到 |
| D3 | PDF 走多源回退且优先 arXiv，并做魔数校验 | 实测 `is_oa=true` 但 `pdf_url=null`；最佳 OA 可能指向返回 HTML 着陆页的第三方镜像 |
| D4 | 锚点匹配三级（L1/L2/模糊≥80%），模糊与未命中只告警/报疑似，不直接判"幻觉" | pdftotext 在真连字符处也断行、ligature、数学符号丢失（`O(n²)`→`O(n2)`）会造成误报 |
| D5 | claim strength 取证据**最小值**而非平均 | 平均会让一条强证据掩盖弱证据；实测此偏差反复出现 |
| D6 | 5C 门控是硬 CHECKPOINT，未输出禁止进入第二遍 | 否则第一遍退化成格式化走过场 |
| D7 | 批判采用"质疑 + 最强辩护 + 裁决"双动作 | 纯对抗会滑向廉价唱反调；目标是区分真缺陷与吹毛求疵 |
| D8 | 红旗非补偿、放在评分表之前 | 防止高分锚定掩盖实验缺陷 |
| D9 | 关系单向写入、反链生成 | 双向手写必然漂移：A 改 B 未改、B 删 A 留断链 |
| D10 | 卡片扁平存放，分类用多值 `types` | 概念多重归属，目录互斥会迫使重分类时搬目录 |
| D11 | 引用图分客观层（引用）与主观层（语义），量化指标只算客观层 | 混图会让模型判断伪装成客观事实 |
| D12 | 里程碑 z 分与分档都在**池内**计算 | 回答"在这个方向里谁重要"；各方向引用量级差异巨大 |
| D13 | 雪球设轮数/池上限 + 饱和双终止 | 引用网络扇出极大，3 轮后候选可达数十万 |
| D14 | 下载并行、精读串行 | 精读要消费前序知识库上下文；下载受 API 速率限制（每批 ≤8） |
| D15 | 矛盾判定顺序固定 definitional→metric→temporal→scope→direct | 顺序颠倒会把范围冲突误报为真矛盾（主要假阳性来源） |
| D16 | 张力卡独立成一等公民 | 原先张力洞察散落在对比导出里永远不会再被检索到 |
| D17 | 引入 maturity，并禁止删除 deprecated 卡 | 单篇卡与十篇卡界面等价会误导判断；deprecated 记录"为什么这条路走不通" |
| D18 | 下游单元禁止网页抓取论文页代替本地资产 | 锚点必须能在本地 FULLTEXT 机械校验 |

---

## 4. 外生契约与硬约束
> 本节为不可推导内容，逐字保留。代码块内的字段名、枚举值、格式字符串必须原样实现。

### 4.1 能力单元声明 [规范约束]

```yaml
name: paper-fetch description: "论文获取引擎：解析论文身份、下载 PDF、提取全文文本、归一化元数据。所有其他 skill 的上游基础能力。触发：/paper-fetch、'下载论文'、'找这篇论文'、'获取元数据'。" --- name: paper-read description: "论文精读引擎：基于三遍阅读法的批判性论文分析，集成 5C 门控、claim-evidence 分离、复现性评估，产出专家级结构化笔记。触发：/paper-read、'精读'、'读这篇论文'、'分析这篇paper'。" --- name: research-trace description: "研究脉络追踪引擎：基于学术 API 和 Wohlin 雪球法的系统化文献检索，构建引用 DAG，量化里程碑评分，生成 PRISMA 流程文档。触发：/research-trace、'研究脉络'、'技术演进'、'发展历程'、'survey'。" --- name: paper-compare description: "论文对比分析引擎：基于 claim 级矛盾检测、自适应对比维度、公平性审计和统计显著性分析的深度横向对比。触发：/paper-compare、'对比'、'比较这几篇'、'哪篇更好'、'异同'。" --- name: paper-kb description: "知识库管理引擎：构建、维护和查询基于 Zettelkasten 方法论的论文知识库，管理永久笔记/张力卡/MOC，执行一致性检查和锚点防幻觉校验。触发：/paper-kb、'知识库'、'更新卡片'、'查一下之前读过的'。" --- # 编排者 name: paper-reader description: "论文研究编排 Agent：根据用户研究需求，编排 paper-fetch/paper-read/research-trace/paper-compare/paper-kb 等 skill，管理研究日志，完成完整研究工作流。" tools: ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "Agent", "Artifact", "AskUserQuestion"]
```
 `tools` 为原宿主的工具名，复现时映射为等价能力：文件读/写/编辑、文件匹配、内容检索、shell、子 agent 委派、可视化页面发布、结构化提问（见 §5.1）。 FETCH 另有自动触发词："把 PDF 下下来"、"抓一下引用数据"。编排者原实现指定使用最强档模型 [推荐做法]。

### 4.2 学术数据源契约 [规范约束]

**端点与参数** 
```OpenAlex
https\://api.openalex.org
/works?filter=<k:v,...>&per-page=<n>&page=<n>&sort=<f:desc>&mailto=<email>
/works/{id}
/works/doi:{doi}
filter 键：title.search
raw_author_name.search
publication_year
doi
cites
from_publication_date
to_publication_date
openalex_id(或 ids.openalex)
多值用 |（单次 ≤50 个 ID），多个 filter 逗号分隔 = AND
请求头：User-Agent: <tool-name>/1.0 (mailto:{email})
Accept: application/json arXiv
https\://export.arxiv.org/api/query?id_list=<id1,id2,...>&max_results=N
（≤100）
https\://export.arxiv.org/api/query?search_query=ti:"<title>"&start=0&max_results=N&sortBy=relevance
PDF: https\://arxiv.org/pdf/{id}v{n} Unpaywall
https\://api.unpaywall.org/v2/{doi}?email={email}
（无 email → 422） Crossref
https\://api.crossref.org/works/{doi} S2
https\://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=title,year,abstract,tldr,openAccessPdf,influentialCitationCount
（仅当 S2 密钥存在时启用，永不作为必需路径）
```
 > `openalex_id` 与 `ids.openalex` 两种 filter 键拼写在原实现文档与代码中不一致；复现时先探测实际可用者。

**限速与重试** 
```OpenAlex
10 rps / 10 万次每日 → 客户端最小间隔 100ms
重试：指数退避 1/2/4s 至少 3 次；响应体含 retryAfter 时按其等待（上限 60s）
HTTP 4xx（429 除外）立即放弃 arXiv
请求间隔 ≥ 3s（违反会被封禁）；无重试 Unpaywall 无重试；失败返回结构化失败体，不抛异常（不可中断回退链） 全部
超时 30s；http→https（arXiv http 301）
```
  **响应字段路径（被消费的）** 
```OpenAlex: meta.count; results[].{id, display_name, publication_year, cited_by_count, doi,
primary_location, locations[].source, locations[].pdf_url, best_oa_location.pdf_url,
referenced_works, citation_normalized_percentile.value, counts_by_year[{year,cited_by_count}],
open_access, ids.{openalex,doi,mag}, abstract_inverted_index}
abstract_inverted_index 形如 {"word":[0,5,12]} → 展开为 (pos,word)，按 pos 排序，空格 join arXiv Atom: ns http\://www\.w3.org/2005/Atom 与 http\://arxiv.org/schemas/atom
entry/{id, title, summary, published, updated, author/name, arxiv:affiliation,
category\@term, arxiv:comment, arxiv:journal_ref, arxiv:doi}
id 形如 http\://arxiv.org/abs/1706.03762v7 → 取 /abs/ 后段，拆 base 与 v{n}
PDF 链接是 <link title="pdf" href=...>（按 title 属性，不是 rel） Unpaywall: is_oa, doi, title, journal_name, published_date, oa_status,
best_oa_location.{url_for_pdf, url, url_for_landing_page, license, host_type, version}
```
  **数据源降级链** 
```元数据：OpenAlex 可用 → 使用
↓ 失败
arXiv API（元数据较少但够用）
↓ 失败
Crossref（需 DOI）
↓ 失败
WebSearch + 手工填写，sources: ["manual"]
PDF：
1. OpenAlex best_oa_location.pdf_url → locations[].pdf_url
2. arXiv https\://arxiv.org/pdf/{id}v{n}
3. Unpaywall https\://api.unpaywall.org/v2/{doi}?email={email}
（无 DOI 则跳过本级）
4. 全失败 → access_level: abstract-only，向用户索要 PDF
```
 （`{email}` 来自"联系邮箱"环境配置，名称自定。下载请求的 User-Agent 与 OpenAlex 相同格式。）

**必须防御的 API 异常行为** [行为必需] 1. 搜索端点间歇返回 HTTP 200 + `{"error":"Search temporarily unavailable", ...}`，polite pool 不豁免；只影响 `*.search` 类 filter，ID 直查与 `cites:` 不受影响 → 搜索失败转 ID 直查 / arXiv。 2. HTTP 200 + `{"error":"Rate limit exceeded","retryAfter":N}` → 按 retryAfter 等待，**不得当结果返回**。 3. 批量 filter 静默丢 ID（请求 N 返回 <N 不报错）→ 比对请求集与返回集，缺失逐个回退。 4. 404 返回 HTML 而非 JSON → 解析前判 content-type 或首字符。 5. arXiv DOI 在 OpenAlex `filter=doi:` 返回 count 0 → 不得据此判不存在。 6. 加年份 filter 可能"语法正确 + 零结果"（重印记录年份不同）→ 年份只作模糊匹配信号。 7. 嵌套字段"键存在但值为 null"（如 `source: null`）→ 逐层安全取值。 8. S2 匿名池 429 极频繁 → 不进必需路径。

### 4.3 输入识别与论文身份（slug）[规范约束]

```输入识别（按序）：
arXiv URL
含 arxiv.org/abs/ 或 arxiv.org/pdf/
新式 arXiv ID ^\d{4}\\.\d{4,5}(v\d+)?$
旧式 arXiv ID ^[a-z\\-]+(\\.[A-Z]{2})?/\d{7}$
DOI
以 10. 开头，或含 doi.org，或带 doi: 前缀
本地 PDF
以 .pdf 结尾的本地路径 → 用户提供模式
其他
视为标题
（旧式 ID 拆版本号时只切末尾 v\d+，不得在类别名里的 "v" 处切开）
slug = {first_author_lastname_lower}{year}_{short_name}
lastname
全小写，仅保留字母；无姓氏 → unknown
year
arXiv 预印本年优先，无则发表年
short_name 标题去停用词后，保留长度 > 2 的词，按原序取前最多 3 个，下划线连接；不足 1 个 → paper
（人工改写只允许在首次入库时进行，之后固定）
冲突
同 slug 已被另一篇占用 → 追加 b / c …（zhang2023_rag → zhang2023_ragb）
后缀按首次入库顺序分配后固定；"同输入必得同 slug"指同一主记录再次获取时复用已分配 slug
示例
vaswani2017_attention
devlin2019_bert
lewis2020_retrieval
liu2023_lost
不可变
一经生成不可更改；如需更正：全仓库替换引用 + 时间线日志记录，禁止静默改
停用词表（逐字）： a an the is are was were of for in on to and or with by from at as its all you need can do how what why when where which that this these those not no but if so than then very just about into over after before between through during without within among
卡片 / 张力卡 slug：kebab-case，全小写，全库唯一；描述概念或冲突本身，禁止用论文名
```

### 4.4 论文元数据 META（JSON，非 frontmatter）[规范约束]

```schema: "arivx.paper.v1" 必需：schema, slug, title, authors, year, access_level, fetched_at, sources 枚举：
access_level: full-text | oa-pdf | abstract-only | user-provided
venue_type:
conference | journal | preprint | workshop | thesis
OpenAlex 记录类型过滤: article | preprint | proceedings-article 其他字段：
venue, venue_type, doi, openalex_id,
arxiv_id
不含 "arXiv:" 前缀与版本号
arxiv_version
如 "v7"
alternate_ids
同标题簇被合并的其他记录 ID
abstract
urls
{landing, pdf, code}
open_access
{is_oa, status, license, resolved_via}
cited_by_count
簇内最大值；主记录原值另存为 primary_record_cited_by_count [推荐命名]
counts_by_year
用于近 3 年引用速度
citation_normalized_percentile
{value: 0–1}
references
≤200 条
cited_by_api_url
fetched_at
ISO 8601 UTC
sources
实际命中的数据源列表；降级时含 "manual"
```
 > 必需字段与枚举为规范约束；"其他字段"中原实现未在协议里逐一标必选，复现时可补齐但不可改名 [推荐做法]。 > 类型与取值 [推荐做法]：`authors` 为字符串数组（"名 姓"原序）；`year` 为 int；`open_access.status` 透传 OpenAlex `oa_status`（gold / green / hybrid / bronze / diamond / closed）；`open_access.resolved_via` 与 `sources[]` 取值于 `openalex | arxiv | crossref | unpaywall | s2 | user | manual`。

**RAW 落盘**：原始响应必须落盘 [行为必需，见 G10]；按数据源分文件（OpenAlex 主记录、OpenAlex 搜索候选列表、arXiv Atom、Crossref、Unpaywall），只写实际命中者，`--refresh` 时覆盖 [推荐做法]。

**工具退出码**：PDF 获取工具 `0` = full-text，`1` = 全部下载失败（含非 OA 的正常降级路径，上层据此写 abstract-only，不视为错误），`2` = 有 PDF 但文本提取失败（oa-pdf）；API 封装中"查无结果"与"参数错误"必须使用不同退出码，错误原文透传给用户 [行为必需]。

### 4.5 Claim 模型 [规范约束]

```ID:
{paper_slug}#C{n}
n 从 1 起，按出现顺序；不可复用/重排；删除留空缺 字段（全部必需）:
id, text, type, scope, evidence[], strength, assumptions[], status, falsifier, confidence
text
用自己的话改写，禁复制摘要
scope
无则填 "未声明"（不可留空）
falsifier
写不出 → 不是 claim，移入 💡；确实不可证伪标 unfalsifiable type:
empirical | theoretical | design | comparative | negative status:
unverified | reproduced | partial | contradicted
（初始一律 unverified） evidence[]: {locator, kind, strength, quote} kind:
table | figure | experiment | proof | ablation | analysis | citation strength:
3 受控实验 + 统计显著性
2 单次实验，无方差报告
1 定性证据或间接支持
0 仅断言，无证据（本身是重要发现） 封顶规则:
abstract-only ≤ 1；非受控对比（引用他人数字）≤ 2；
claim.strength = min(evidence[].strength)
CMP 中再按 claim 来源施加上限（取 min，不会抬高原值）：
深读/精读 ≤3、速读 ≤2、临时提取 ≤2（标 ⚠️ 未经精读校验）、abstract-only ≤1 confidence: 0–1，语义与卡片 confidence 区间相同（§4.16）
```
 `contradicted` 状态须联动张力卡（N6.6）。同一证据支持多条 claim 允许，但须体现同源。

### 4.6 证据锚点 [规范约束]

```标记:
📖 论文原文观点 —— 必须带锚点，被脚本校验
💡 个人理解/评价 —— 不需锚点，需标置信度 定位符: {section} | {element} | {page}
section
§ + 章号：§3.2
§A.2；摘要写 Abstract
element
Table 2 | Figure 3 | Eq.1 | Algorithm 1 | Appendix B
page
p.8；未确认可省略并标 ⚠️ 页码未确认
单元格
§6.1 | Table 2 (row: WMT EN-DE) | p.8
多证据
; 分隔 引文:
≤ 25 词（英文按空格分词），连续原文，双引号包裹，保持原语言；
禁止改写 / 拼接 / 用省略号跨段 / 翻译；页码以所取得 PDF 版本为准
归一化（6 步，按序）:
1 连字符换行合并
2 折叠空白
3 统一引号
4 统一破折号
5 转小写
6 去首尾空白 匹配判定:
✅ 精确匹配
L1 归一化后子串命中
✅ L2 匹配
L1 未命中，L2（再剥离全部空白与连字符）命中 → 标 ⚠️ L2-match
⚠️ 模糊匹配
字符重合度 ≥ 80% → 标 ⚠️ 需人工确认，附相似度
❌ 未匹配
重合度 < 80% → 报「疑似幻觉引用」，列出最相近候选片段 按 access_level:
full-text
全量校验
user-provided
全量校验
oa-pdf
跳过，标 ⚠️ 无法自动校验（文本提取失败）
abstract-only
跳过，标 ⚠️ 仅摘要级别；claim strength 上限为 1
```
 [推荐做法] 引文短于 12 字符不参与校验；模糊匹配用引文中的锚点词定位候选窗口（0.7–1.3 倍引文长度）以避免全文 O(n·m) 扫描；抽取引文时须先剥离尾部 `§ / p. / Table` 定位符后缀。

### 4.7 精读判据：5C、门控、批判、红旗、评分、复现 [规范约束]

```5C:
Category
empirical | theoretical | system | survey | position | dataset
Context
依赖的前置工作、所属技术线、与知识库的关联、预设读者背景
Correctness
初步表面自洽性；只标可疑点，不下结论
Contributions 逐条列出，措辞必须体现"声称"
Clarity
high | medium | low
门控（四档）:
弃读
类型不匹配需求，或质量明显低于门槛 → 只留 5C 记录，不入库
skim
相关但非核心 / 质量中等 / 只需大意
（约 15–30 min，无 claim 表）
read
核心相关且质量合格
（约 1–2 h，全量但无虚拟重实现）
deep
里程碑论文 / 方法将被复现 / 综述核心参考
（约 3–5 h，含隐藏决策清单 + 完整 claim 表）
辅助信号：领域引用 top 10% 或直接竞争 → 倾向 deep；"知道有这工作"即可 → skim
强制输出：门控 / 理由（一句话）/ 置信度
read_mode 取值：skim | read | deep
批判清单分组：假设组、方法组、实验组、结论组、复现组 + 终极问题（必答）
裁决：成立 | 有条件成立 | 不成立 | 无法判定
严重度：致命 | 重要 | 次要
辩护类型：条件限定 | 权衡取舍 | 领域惯例 | 实证补充 | 范围声明
非补偿红旗（rating_flags）→ 触发的分维上限:
unfair-baseline
Evidence ≤ 3
no-variance
数值差异降为"不可判定"
overclaim
Significance ≤ 3
hidden-assumption
Soundness ≤ 3
cherry-picking
Evidence ≤ 2
no-ablation
Evidence ≤ 3
```

| 维度 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Novelty | 直接复现或微调 | 增量改进 | 有效新组合 | 新方法/新架构 | 新问题/新范式 |
| Soundness | 推导有错 | 有缺陷但结论可能对 | 基本正确 | 严谨 | 无懈可击 |
| Evidence | 无实验 | 玩具实验 | 标准基准 | 全面 | 多场景+统计显著 |
| Reproducibility | 不可能 | 信息不全 | 需相当多推断 | 基本可复现 | 完整 |
| Significance | 无影响 | 小众改进 | 领域内有用 | 广泛采用潜力 | 改变研究方向 |
| Clarity | 无法理解 | 勉强可读 | 结构清晰 | 写作优秀 | 教科书级 |  评分与引用数无关；信息不足标 `(低置信)`；理论论文 Reproducibility 判 N/A 并说明"无复现对象"，不视为缺陷。  
```复现审计 8 项（每项给证据位置）:
代码开源 | 预训练权重 | 数据集可得 | 超参完整性 | 训练硬件与时长 | 框架与版本 | 随机性处理 | 评测脚本与数据划分
判定：✅ 完全满足 | ⚠️ 部分满足 | ❌ 不满足 | N/A
（N/A 预设语义：纯理论 / 不发布权重的方法 / 标准公开数据集 / 确定性算法） repro_score: 0–10（原实现未固化计分规则；[推荐] 对非 N/A 项 ✅=1、⚠️=0.5、❌=0 求均值 × 10，四舍五入为 int） 最小复现实验四段式：目标 claim / 简化方案 / 成功标准 / 所需资源 失败模式预判：复现失败本身是有价值产出
```

### 4.8 Frontmatter schemas [规范约束]

图例：✅ 必填 · ➖ 选填。schema 值即版本号，各 schema 互不兼容；字段变更为破坏性变更，须检查全部消费方并递增主版本。

**文献笔记 `arivx.note.v1`** 
```yaml
schema: arivx.note.v1 # ✅ 固定 slug: vaswani2017_attention # ✅ = 文件标识 paper_slug: vaswani2017_attention # ✅ 冗余字段，与 slug 相同，便于工具统一处理 title: "Attention Is All You Need" # ✅ title_zh: "" # ➖ note_type: literature # ✅ 固定 authors: ["..."]                 

# ✅ year: 2017 # ✅ int venue: "NeurIPS" # ➖ venue_type: conference # ➖ 同 META 枚举 arxiv: "1706.03762" # ➖ 无前缀 doi: "..." # ➖ tags: [transformer]              

# ✅ kebab-case five_c:                          

# ✅
category: empirical # empirical|theoretical|system|survey|position|dataset
context: "..." # ✅
correctness: "..." # ✅
contributions: ["..."]         

# ✅ 声称而非事实
clarity: high # high|medium|low read_mode: deep # ✅ skim|read|deep，与门控一致 access_level: full-text # ✅ full-text|oa-pdf|abstract-only|user-provided，从 META 透传 coverage: "全文 §1-§7" # ✅ 实际读过的范围，不得假装读全 rating:                          

# ✅ 六维各 1–5 int；reproducibility 可为 null（= N/A，理论论文）
novelty: 5
soundness: 4
evidence: 3
reproducibility: 2
significance: 5
clarity: 5 rating_flags: [no-variance]      

# ➖ 红旗枚举见 §4.7 repro_score: 6 # ➖ 0–10 claims_count: 7 # ✅ key_claims: ["vaswani2017_attention#C1"]

# ✅ 完整 ID，通常 2–4 条 relations:                       

# ➖ 结构见下
- {type: improves, target: x, evidence: "§2", by: self, confidence: 0.9} status: completed # ✅ in-progress|completed|skimmed|abandoned read_date: "2026-09-29" # ✅ YYYY-MM-DD updated: "2026-09-29" # ➖
```
  **永久卡片 `arivx.card.v1`** 
```yaml
schema: arivx.card.v1 # ✅ 固定 slug: multi-head-attention # ✅ kebab-case 全库唯一 types: [concept, method]         

# ✅ 多值：concept|method|model|dataset|benchmark aliases: ["多头注意力"]           

# ✅ 全库不得重复（跨文件校验） first_seen: vaswani2017_attention # ✅ papers: [vaswani2017_attention]  

# ✅ maturity: seed # ✅ seed|growing|evergreen|contested|deprecated confidence: 0.9 # ✅ 0–1 coverage: representative # ✅ partial|representative|comprehensive papers_count: 1 # ✅ = len(papers)，由工具计算，不手填 relations:                       

# ➖ 单向写入
- {type: part-of, target: transformer, evidence: "§3.2", by: self, confidence: 0.95} created: "2026-09-29" # ✅ updated: "2026-09-29" # ✅
```
  **relations 条目（note/card 通用）** 
```type
✅ §4.15 枚举之一 target
✅ 论文 slug 或卡片 slug（不带扩展名） evidence
✅ §4.6 定位符；禁写"论文中提到" by
✅ self = 论文自称 | me = 我的判断 | {paper_slug} = 第三方论文的判断 confidence
➖ 0–1；缺省 1.0（客观事实）或 0.7（我的判断）
```
  **张力卡 `arivx.tension.v1`** 
```yaml
schema: arivx.tension.v1 # ✅ 固定 slug: "" # ✅ kebab-case，描述冲突本身 type: tension # ✅ 固定 status: open # ✅ open|resolved|dissolved|wontfix kind: scope # ✅ direct|scope|definitional|metric|temporal parties:                         

# ✅ 至少 2 项
- claim: "{paper_slug}#C{n}" # ✅ 完整 claim ID（禁填论文名），创建前校验存在
position: supports # ✅ supports|contradicts|silent
evidence: "§5.1 | Table 2" # ✅ 定位符；position: silent 时填 "—" scope_hypothesis: "" # ✅ 冲突可能只在哪些条件下成立（direct 时为 null） resolution_test: "" # ✅ 可操作的解决实验 related_cards: []                

# ➖ 卡片 slug created: "YYYY-MM-DD" # ✅ updated: "YYYY-MM-DD" # ✅
```
  **对比报告 `arivx.compare.v1`** 
```yaml
schema: arivx.compare.v1 # ✅ 固定 type: paper-compare # ✅ 固定 topic: "" # ✅ 共同研究问题 papers: []                       

# ✅ 论文 slug 列表（2–N） archetypes: {}                   

# ✅ {paper_slug: 原型枚举值}，枚举见 §4.14 fairness_grade: "中" # ✅ 高|中|低|不可比（中文枚举，原样保留） created: "YYYY-MM-DD" # ✅
```
  **MOC**（协议层未定义正式 schema，以下为原实现事实格式 [行为必需]） 
```yaml
slug: "" type: moc cards: [] updated: "YYYY-MM-DD"
```
  **TRACE 报告 frontmatter** 
```type / topic / scope / time_span / created / papers_screened / papers_included / snowball_rounds / saturation / search_sources
```
 [推荐做法] 类型：`type` 固定为 `research-trace`；`papers_*`、`snowball_rounds` 为 int；`saturation` 为 bool；`search_sources` 为字符串数组；`time_span` 形如 `"2017-2026"`。

### 4.9 文献笔记正文模板（固定顺序）[规范约束]

```### 1. 元信息卡片
（行首 emoji 固定：📄 标题 / 👤 作者 / 📅 年份·venue / 🔗 链接 / 📊 引用 / 📦 代码） ### 2. 5C 门控
（含 **作者声称的贡献** / **可疑点（待验证）** / **门控决策**） ### 3. 一句话总结
（是判断，不是摘要复述；须体现红旗） ### 4. 论文骨架图 ### 5. 问题形式化 ### 6. 方法拆解 ### 7. 虚拟重实现（仅 deep 模式）
（含隐藏决策清单） ### 8. Related Work 演进线 ### 9. 实验设置与结果
（含 ### 设置 / ### 主要结果 / ### 消融实验） ### 10. 批判性质疑与裁决
（含 ### 质疑清单 / ### 终极问题） ### 11. 声称-证据表
（claim 索引表 + 详细区块；表内可用 C{n} 短形式） ### 12. 复现性评估
（格式 = §4.7 复现审计：审计表 + repro_score + 最小复现实验四段式） ### 13. 评审评分
（红旗表在前，六维评分表在后；综合评语不是平均） ### 14. 关联论文 ### 15. 蒸馏清单
（原子判断 + 卡片类型 + [[链接]] + 理由；张力线索标冲突类型）
skim 档裁剪：1 → 2 → 3 → 4 → 14，末尾追加"结论可信度（速读判断）"：高/中/低 + 依据 + 是否值得精读 abstract-only：硬约束为"只允许 5C + 骨架 + 摘要级总结"；推荐裁剪为 1 / 2 / 3 / 4 / 14
```
 Schema 与模板冲突时以 §4.8 schema 为准。

### 4.10 TRACE：检索记录与 PRISMA [规范约束]

```检索式记录字段（缺一不可）:
库 / 检索式 / 排序 / 每页 / 翻页 / 执行日期 / 命中总数 / 实际获取 / 原始响应
检索式五类：核心词 / 同义词 / 上位词 / 下位词 / 综述限定（各记命中数后合并去重）
默认：每页 200 × 2 页；种子 top-N = 20
PRISMA 计数字段:
识别总计 / 去重移除 / 去重后 / 进入筛选 / 排除(分类计数) / 进入深入评估 / 纳入定性分析 /
后向雪球新增候选·纳入 / 前向雪球新增候选·纳入 / 轮次 / 饱和 / 雪球纳入 / 最终纳入综合分析
守恒：每阶段 进入 = 排除 + 通过
排除码:
筛选阶段
E-topic
E-type
E-time
E-lang
E-venue
E-dup
E-noscope
评估阶段
F-nofulltext
F-content
F-quality
F-scope
F-superseded
无摘要不得直接排除；时间窗宁窄勿宽；纳标用"是否含方法论贡献"而非"是否含实验"
完备性自评四项:
综述交叉验证覆盖率 / 雪球饱和（是·否，R 轮，末轮新增 N）/ 高引 top-50 覆盖率 / 检索源多样性
+ 已知局限 + 置信度: 高 | 中 | 低
报告章节（8 章，逐字）:
1. 检索协议(PRISMA)
2. 引用数据来源
3. 里程碑清单(量化分级)
4. 引用图与结构分析
5. 演进时间线
6. 技术维度对比
7. 当前前沿与开放问题
8. 待验证假设
阅读清单分区（逐字）:
🏔️ 范式级(必读) / ⭐ 突破级(必读) / 📌 重要(推荐) / 📎 补充(可选) / 📖 推荐阅读顺序 / 🔗 待下载
```

### 4.11 TRACE：雪球、引用图、里程碑评分 [规范约束]

```雪球:
后向 = 记录的 referenced_works；前向 = filter=cites:{id}
默认 max_rounds = 3；max_pool = 500
前向单篇最多取 400 条（每页 200，即最多 2 页）；批量 50 个 ID 一请求；请求间隔约 0.15s
硬排类型: dissertation, patent, book-chapter, book, editorial, erratum, letter, paratext
语言: 非 en/eng 排除；语言字段缺失不排除；无标题记 E-topic
去重键优先级: DOI > OpenAlex ID > arXiv ID > 归一化标题+年份
输出 schema（snowball）:
seed_ids, rounds[{round, backward_candidates, forward_candidates, merged, new_candidates,
included, excluded_reasons}], saturated, final_set, stats{total_rounds, total_included,
pool_limit_hit}, warning
引用图（schema: arivx.citation_graph.v1）:
边方向: source 引用 target
破环: 发表年 ys > yd 保留（新引旧）；ys < yd 删除；同年时 cited_by(d) >= cited_by(s) 保留，否则删
层级: layer(v) = max{layer(u)+1 : (u,v) ∈ E_cites}, layer(source)=0
（Kahn 拓扑排序 + DP）
共被引归一化: co_citation_strength = co_cited(A,B) / sqrt(in_degree(A) · in_degree(B))
[推荐] 文献耦合: shared_refs(A,B) = |refs(A) ∩ refs(B)|，≥ min_shared 才计入，
归一化 shared_refs / sqrt(|refs(A)| · |refs(B)|)
[推荐] is_bridge: 在引用层无向投影上为割点（articulation point）
默认: top_k=50, 共被引 min_strength=2, 耦合 min_shared=3, Mermaid 最多 40 节点, 标题截断 32 字符,
分量 members 只存前 20
语义边类型: extends, improves, competes, contradicts, applies, supersedes（每条带 evidence）
—— 这是引用图 JSON 专用的独立枚举，不是 KB relations；若要沉淀进 KB，按以下映射写入源卡片
relations [推荐做法]：extends→refines, improves→improves, competes→alternative-to,
contradicts→contradicts, applies→requires, supersedes→supersedes
nodes[{id, slug, title, year, cited_by_count, in_degree, out_degree, layer, is_bridge, component}]
edges[{source, target, layer, type, evidence}]
layer ∈ {citation, semantic}
metrics{node_count, edge_count, citation_edges, semantic_edges, cycles_removed, avg_in_degree,
components, components_detail[{id,size,hub,members}], bridge_nodes, max_layer,
co_citation_top, bibliographic_coupling_top}
所有指标在破环后、在池内计算
里程碑评分:
impact = 0.35 · z(log1p(cited_by_count))
+ 0.25 · citation_normalized_percentile.value
（不再 z 化；缺失取 0.5 并标 percentile_missing: true）
+ 0.20 · z(citation_speed_3y)
+ 0.20 · z(normalized_in_degree)
citation_speed_3y = Σ cited_by_count(year ≥ current_year − 3) / min(3, max(1, current_year − publication_year + 1))
（无年份时分母取 3）
normalized_in_degree = in_degree / max(1, pool_size − 1)；不在图内取 0
z: 池内，样本标准差（n−1）；n<2 或 std=0 时 z 全为 0
rank_percentile = (below + 0.5·equal) / n；n=1 时为 1.0
分档: ≥0.95 🏔️ 范式级 | ≥0.80 ⭐ 突破级 | ≥0.50 📌 重要 | 其余 📎 补充
旗标提档（不参与加权，须 confirmed=true 才生效）:
defines-problem → 🏔️
founds-subfield → 🏔️
releases-benchmark → 至少 ⭐
de-facto-standard → 至少 ⭐
survey-authoritative → 至少 📌，并标注为综述
旗标结构 {type, reason, confirmed}；工具只给候选（confirmed=false），由模型/人确认后重算
输出: {schema, formula, pool_size, small_pool, warning, disclaimer,
items[{openalex_id, slug, title, year, impact_score, rank, rank_percentile,
tier_before_flags, tier_after_flags, components, raw, flags}]}
components = 各分量的 z 值 / 百分位值及其加权值；raw = 未变换的原始数值（引用数、速度、入度等）
池 < 20 篇: 必须输出小池子警告 [规范]；强制 🏔️ 至少 1、至多 3 篇 [推荐]
必附免责声明: 影响力 ≠ 质量
```

### 4.12 KB：子命令与卡片骨架 [规范约束]

```/paper-kb status /paper-kb query <关键词>
[--type ...] [--maturity ...] [--json] [--limit N(默认50)] [--include-deprecated] /paper-kb add <类型> <名称> /paper-kb link <slugA> <slugB> <type> [--by self|me|<paper_slug>] [--evidence "§X.Y"] /paper-kb index /paper-kb graph <slug> /paper-kb check
[--anchors] /paper-kb claims <slug> /paper-kb contradictions /paper-kb mocs [build [--rebuild]|edit <topic>]
（build 先执行增量变更检测 N6.11；--rebuild 强制全量重聚） /paper-kb stale
```
  **永久卡片正文骨架（逐字）** 
```# {名称} ({English Name}) ## 定义 ## 关键特征 ## 技术细节 ## 演进历程 ## 与其他概念的关系 ## 常见误解
```
  **MOC 正文骨架（逐字）** 
```# {主题} ## 这个主题在问什么 ## 核心概念 <!-- AUTO-GENERATED:BEGIN --> （工具写入区，其余区块工具不得覆盖） <!-- AUTO-GENERATED:END --> ## 相关张力 ## 推荐阅读顺序
```
  **原子性判据（两条都须通过）** 1. 这张卡片能否被另一篇论文引用而不需要修改？ 2. 卡片标题能否用一句话说完，且不包含"和""以及"？ 不通过 → 拆分并用 relations 连接。卡片正文 >500 词视为巨型卡反模式；卡片标题禁止写成论文名。

**三层笔记** 
```fleeting
D-INBOX
天级、无 frontmatter、>2 周未整理直接删 literature
D-LIT
1 论文 : 1 笔记，很少更新，禁止混入后续论文内容 permanent
D-CARD
N : M，持续演进；由蒸馏清单产生
```

### 4.13 编排：路由、CHECKPOINT、日志与清单格式 [规范约束]

| 用户意图 | 编排链 |
|---|---|
| 读这篇 | FETCH → READ |
| 下载这篇 | FETCH |
| 方向有哪些论文 | TRACE → 阅读清单 |
| 这几篇区别 | 查已有笔记，缺的先 READ → CMP |
| 深入了解 X | TRACE → FETCH(批) → READ(里程碑) → CMP(同期) → KB |
| 准备 survey | 同上，但 TRACE 走完整 PRISMA + 纳排标准前置声明，求最大覆盖 |
| 以前读过 X 吗 | KB query |
| 知识库状态 | KB status + check |
| 一致性检查 | KB check --anchors |  
```CHECKPOINT（7 个）:
① 资产盘点后（复用 / 重来）
② 论文身份解析后（slug 与元信息）
③ 阅读档位决定后（skim/read/deep） ④ 批量清单确定后（范围）
⑤ 批量下载完成后（失败项处理）
⑥ 每 3–5 篇精读后（继续 / 调方向）
⑦ 产物生成前（格式与去向）
```
  **方向日志（每方向一份，累积式）** 
```markdown --- topic: "检索增强生成" created: "2026-09-20" updated: "2026-09-29" status: active      

# active | paused | archived **---** ## 研究问题** ## 已读论文** | slug | 年份 | 一句话结论 | 精读日期 | ## 未决问题** ## 日志** ### {日期}**

- **动作**: - **新发现**: - **判断的变化**: - **未解决问题**: - **下一步**:
```
  **时间线日志（全局一份，append-only，每事件一行；与方向日志双写）** 
```markdown | 日期 | 方向 | 动作 | 判断的变化 | |------|------|------|-----------|
| 2026-09-29 | RAG | 精读 liu2023_lost | 长上下文替代检索的结论仅限单跳 |
```
 动作枚举：检索 / 下载 / 精读 / 对比 / 追踪 / 知识库维护。

**阅读清单** 
```markdown --- topic: "研究方向" updated: "YYYY-MM-DD" total: N **---** ## 🏔️ 必读论文** | # | 论文 | 年份 | 里程碑意义 | 已精读 | ## ⭐ 推荐阅读**
```
 （以上为手工新建清单的最小格式。由 TRACE 生成时必须使用 §4.10 的六分区标题；两种格式的表头相同。"已精读"列标记即为状态流转，无其他状态枚举。）

### 4.14 CMP：原型、对齐、矛盾、公平、显著性 [规范约束]

```原型: empirical-benchmark | theoretical | system | dataset-benchmark | survey | mixed 判定 5 步: 有定理 → theoretical；以系统指标为核心 → system；数据集且无新方法 → dataset-benchmark；
纯梳理 → survey；其余 → empirical-benchmark 边界: 理论+实验 → theoretical（附加"实验验证"维）；系统+实证 → system；
数据集+新 baseline → dataset-benchmark；难归类 → mixed（分表，禁止并集大表）
维度集（保留 | 舍弃）:
empirical-benchmark
数据集、评估指标、基线选择、参数量、FLOPs/推理成本、训练预算、方差报告、消融完整性
| 定理强度、证明技巧、构造性、紧致性
theoretical
假设强度、定理覆盖面、证明技巧、构造性、紧致性、与既有结果的强弱、实验验证
| 参数量、推理延迟、训练预算、消融
system
吞吐量、延迟、内存占用、扩展性、容错性、部署成本、实现成熟度、与现有系统兼容性
| 泛化性讨论、定理强度、数据集偏见
dataset-benchmark
规模、构造流程、标注质量、偏见审计、许可与伦理、维护状态、难度分层 | 方法细节
survey
检索完备性、筛选标准、分类维度、覆盖时效、批判深度、可复现性 | 实验对比、参数量、复杂度
claim 对齐:
三问全"是"才同簇：可判定性（同一实验能否同时证实/证伪）/ 主题同一性（同一因变量）/
范围可比性（去掉 scope 后核心命题一致）
禁止合并：不同因变量 | 不同任务层级 | 因果 vs 相关 | 不同抽象层次
排除：纯方法描述 | 对他人工作的转述 | 无真值的操作性陈述
一条 claim 含多个独立声称 → 回 READ 拆分（临时对比才允许多簇归属）
命题陈述用肯定句、中立、可判定
矛盾判定（顺序不可颠倒）:
Step 1 术语定义一致吗？ 否 → definitional（停止）
Step 2 评测协议与指标一致吗？ 否 → metric（停止）
Step 3 发表时间有实质间隔且后者以基线形式包含前者吗？ 是 → temporal（停止）
Step 4 scope 实质重叠吗？ 否 → scope（停止）
Step 5 证据强度均 ≥ 2 吗？ 否 → direct?（不确定）
Step 6 → direct（真矛盾）
scope 触发（任一）：数据集性质有别 / 模型规模不同 / 任务类型不同 / 上下文长度区间不重叠 / 语言领域不同
temporal 触发（任一）：间隔 >1 年且技术栈已变 / 后者基线包含前者 / 差异可由算力或数据规模增长解释
矩阵单元格: ✅ supports | ❌ contradicts | ⚠️ partial | — silent
（每格带完整 claim ID）
张力卡映射: direct→open; scope→open; definitional→dissolved;
metric→ 草案一律 open（resolution_test = 统一评测协议）；是否 dissolved 不在 CMP 中判断，
留待 KB 张力卡生命周期（N6.6）在获得统一协议下的证据后迁移;
temporal→不建卡，改写 relations
公平性 9 项（✅ / ⚠️ / ❌；第 8 项单组件方法记 N/A）:
1 数据与划分一致
2 基线同等调优（搜索预算）
3 参数量对齐
4 训练 tokens / FLOPs 对齐
5 推理预算对齐
6 评测协议 / 提示模板一致
7 随机性与方差报告
8 消融完整性
9 报告失败案例 / 负结果
阈值: 参数量与训练计算量 ✅ <1.3× | ⚠️ 1.3–3× | ❌ >3×
方差 ✅ ≥3 种子均值+标准差/置信区间 | ⚠️ 单次运行或种子<3 | ❌ 无任何方差信息
其余项定性判定: ✅ 双方一致/完整报告 | ⚠️ 部分差异或部分报告 | ❌ 明显不一致或完全缺失
综合等级:
高: 无 ❌ 项，且 ⚠️ 项 ≤ 2
中: 有 1-2 个 ❌ 项，或 ⚠️ 项 ≥ 3
低: 有 ≥3 个 ❌ 项，或 参数量/训练预算/评测协议 中任一项为 ❌
不可比: 由 ❌ 项显式列出不可归因的比较
传递: 参数量/训练预算 ❌ → 性能对比降级为不可比；评测协议 ❌ → 相关数值全标 ⚠️ 协议差异；
方差 ❌ → 向显著性传 variance-unreported
显著性:
Step 1 方差检查 → Step 2 效应量 vs 噪声 → Step 3 多重比较警告 → Step 4 协议差异量化 → Step 5 结论标注
σ_combined = sqrt((σ_A² + σ_B²) / 2)；只有一方报方差 → 标 ⚠️ 单边方差估计；σ = SEM × sqrt(n)（禁把 SEM 当 σ）
d ≥ 2σ "显著优于" | 1σ ≤ d < 2σ "可能优于，但证据不充分" | d < 1σ "无显著差异"
多重比较次数 k = 基准数 × 数据集数：k ≤ 10 正常 | 10 < k ≤ 50 注明 | k > 50 结论降级
净差距 = 报告差距 − 协议差异贡献（无文献可量化时标 未知）
无方差: 所有涉及该论文的优劣判定 → "不可判定（未报告方差）"；
仅允许描述性表述"差距量级远超常见方差范围，但严格判定仍需方差信息"
延迟类指标只报均值 → 不可判定（须 P50/P95/P99）
标签: 显著 | 弱 | 不可判定（原因）
显著: d ≥ 2σ 且协议差异已排除或量化后仍成立，且多重比较可接受
弱:
1σ ≤ d < 2σ，或协议差异无法完全排除，或存在多重比较警告
不可判定: 无方差信息 / 净差距 < 1σ / 协议差异无法量化
p 值只说明"不太可能是偶然"，须与效应量分别报告
报告骨架: frontmatter(§4.8 compare.v1) + Step1 信息对齐提取 → Step2 claim 对齐聚类 → Step3 矛盾检测矩阵
→ Step4 自适应维度对比 → Step5 公平性审计 → Step6 统计显著性分析 → Step7 深度差异分析 → Step8 综合结论
```

### 4.15 关系本体（relation types）[规范约束]

原实现文字标称"5 族 14 种"，但实际列出 18 种；**以实际列出的 18 种为合法集合**，校验器接受且只接受这些值。`relates-to` 不存在。无命名逆关系，反链由工具生成。  
```RELATION_TYPES = {
is-a, part-of, instance-of,
improves, supersedes, refines, generalizes, specializes,
supports, contradicts, assumes, limits,
requires, alternative-to, evaluates-on, fails-on,
prerequisite-of, analogous-to }
```

| 族 | type | 方向（存储为 源 → target） | 传递 | 副作用 |
|---|---|---|---|---|
| 分类学 | `is-a` | 具体 → 抽象 | ✅ |
| | 分类学 | `part-of` | 部分 → 整体 | ✅ |
| | 分类学 | `instance-of` | 实例 → 类型 | ❌ |
| | 演进 | `improves` | 新 → 旧 | ⚠️ |
| | 演进 | `supersedes` | 新 → 旧 | ⚠️ | target 建议转 deprecated |
| 演进 | `refines` | 细 → 粗 | ❌ |
| | 演进 | `generalizes` | 一般 → 特殊 | ❌ |
| | 演进 | `specializes` | 特殊 → 一般 | ❌ |
| | 认识论 | `supports` | 证据 → 被支持命题 | ❌ |
| | 认识论 | `contradicts` | 无向语义，存储为 新发现者 → 旧 | ❌ | 评估是否建张力卡 |
| 认识论 | `assumes` | 依赖者 → 被依赖者（理论前提） | ✅ |
| | 认识论 | `limits` | 指出者 → 被限制者 | ❌ |
| | 方法论 | `requires` | 使用者 → 被依赖者（实现层面缺了跑不起来） | ✅ |
| | 方法论 | `alternative-to` | 新 → 旧（近似对称，单向存储） | ❌ |
| | 方法论 | `evaluates-on` | 方法 → 数据集/基准 | ❌ |
| | 方法论 | `fails-on` | 方法 → 失败场景 | ❌ |
| | 教学 | `prerequisite-of` | 前置 → 后续 | ✅ |
| | 教学 | `analogous-to` | 近似对称，单向存储 | ❌ |
|  规则：能用具体类型表达的不用笼统类型；`improves`（旧方法仍有用）与 `supersedes`（旧方法应弃用）不可混用；每张卡至少 2 条 relations。

### 4.16 成熟度与张力卡生命周期 [规范约束]

```maturity:
seed
1 篇论文支撑；不应被当作可靠知识引用（新卡默认；新建时仍须 ≥2 条 relations，见 §7.4）
growing
≥2 篇论文支撑 且 ≥2 条 relations
evergreen
≥3 篇独立论文支撑 且 定义稳定 且 无未解释反例（关联密集，≥4 条）
contested
存在 status: open 的张力卡指向该卡
deprecated
被 supersedes 指向，或张力卡 resolved 且结论否定该卡；禁止删除 迁移:
seed → growing
≥2 篇且 ≥2 relations
工具建议，人工确认
growing → evergreen
≥3 篇独立 + 定义稳定 + 无反例
人工判断
任意 → contested
open 张力卡指向
自动
任意 → deprecated
被 supersedes / 张力卡判错
工具建议，人工确认
contested → growing/evergreen
张力卡 resolved 或 dissolved
人工判断
evergreen → growing
出现新的未解释反例
人工判断 独立论文: 一作或通讯作者相同 → 不独立；同机构同方向 → 存疑倾向不独立；不同团队独立得出 → 强独立 默认排序: evergreen > growing > contested > seed > deprecated（deprecated 默认隐藏）
confidence: 0.0–0.3 高度不确定（单一来源+未验证+有疑点）| 0.3–0.6 初步证据不充分 |
0.6–0.8 较有信心（多源一致或验证过关键点）| 0.8–1.0 高度确信（广泛共识+独立验证） coverage:
partial（只看了 1–2 篇）| representative（覆盖主要工作）| comprehensive（雪球/PRISMA 系统检索） 正交性: maturity = 网络层面；confidence = 认知层面；coverage ≠ papers_count 陈旧阈值: seed 未升级 90 天；任意卡未更新 180 天；张力卡 open 180 天；evergreen 陈旧降级为提示
张力卡状态: open（默认）| resolved（resolution_test 被执行或后续工作给出充分证据）|
dissolved（伪冲突：定义/度量/范围差异）| wontfix（>180 天且优先级低，主动关闭）
open ◄── 新 claim 命中 parties 命题时自动唤醒
resolved 动作: 追加「结论」区块；更新相关卡 maturity/confidence；判错方考虑 deprecated；澄清边界则双方卡补「适用条件」
dissolved 动作: 写明伪冲突原因；在相关卡补定义/边界澄清（主要价值）；因此卡变 contested 的卡恢复原状态 kind 处理:
direct
scope_hypothesis: null；resolution_test 应为复现实验
scope
resolution_test 应划出边界条件
definitional
通常直接 dissolved，并补定义澄清
metric
resolution_test 应为统一评测协议
temporal
创建时可直接 resolved，并更新被推翻的卡（可能 deprecated） resolution_test: 必须说清用什么数据、什么方法、测什么指标、什么结果支持哪一方
不可接受: "需要更多研究" "做实验验证" "看看哪篇引用多" "等社区共识"
唯一可留"无需实验——已由后续工作证明"的情形: kind: temporal 且 status: resolved
```
  **张力卡正文骨架（逐字）** 
```# {标题} ## 争议描述 ## 各方立场 ### 支持方：[[...]] > 📖 引文 — 出处
（+ 证据 + 适用范围） ### 反对方：[[...]] ## 冲突类型判定 ## 调和假设 ## 解决实验
（含 **resolution_test** 与 **判定标准**） ## 相关卡片 ## 进展记录 | 日期 | 事件 | 状态变化 |
```

### 4.17 KB 生成物与一致性检查 [规范约束]

```索引（markdown，分区顺序逐字）: 

## 按类型
每类 ### {type} ({n})，条目 - [[slug]] — 摘要 · maturity: X · papers: N 

## 按成熟度      

### {icon} {maturity} ({n})
（icon 映射原实现未固化，[推荐] 每个 maturity 固定一个 emoji，全库一致） 

## 最近更新
| 卡片 | 类型 | 更新日期 |
（取 15 条） 

## 张力卡 

## Map of Content 

## 待建卡清单
（[推荐] 来源：文献笔记蒸馏清单中尚未建卡的 [[链接]]）
反向链接（markdown）: 

# 反向链接 

## [[slug]]
目标不存在时追加 ⚠️ 目标不存在
- ← [[source]] (type, evidence: X, by: Y, confidence: Z)
图谱（JSON）:
{nodes:[{id, kind, types, maturity, confidence, coverage, papers_count
| tension: status, tension_kind | moc | note: year, read_mode}],
edges:[{source, target, type, by, confidence, evidence}],
stats:{node_count, edge_count, avg_degree}}
avg_degree = round(2.0*edges/nodes, 3)
统计（JSON）:
{updated, counts:{cards, tensions, mocs, literature_notes}, by_type:{type: n}, by_maturity:{maturity: n},
orphan_cards, broken_links:[{from, target, field, type}], avg_connections}
增量缓存（JSON）: {cards:{slug:{hash, updated[, cluster_id]}}, updated}
hash 只取正文前 500 字符；缓存损坏静默重建；写回保留上层 cluster_id；支持 --rebuild
一致性检查（8 项，固定顺序）:
断链
relations.target 或 [[wikilink]] 不在 slug 索引
FAIL
孤立卡
度 = 0
WARN
重复别名
不同卡片 alias 冲突
FAIL
锚点校验
精确/L2 PASS；模糊 ≥0.80 WARN；否则 FAIL；缺全文 WARN
孤儿论文
推荐判定：卡片 papers 引用了不存在笔记/资产的论文 slug，或反之
WARN
陈旧
card >180d、seed >90d、updated 不可解析
WARN
未决张力
open >180d
WARN
Schema
必填缺失 / 类型错 / 枚举越界 / parties < 2
FAIL
└ 计数子项
papers_count ≠ len(papers)（属 Schema 检查，但级别为 WARN）
WARN
[推荐] avg_connections = 卡片节点（含出边与入边）平均度
退出码:
构建
0 成功 | 1 知识库目录不存在 | 3 缺 YAML 解析依赖
校验
0 无 FAIL | 1 存在 FAIL | 2 参数/环境错误
查询
0 | 1 claims 目标笔记未找到 | 2 环境错误
缓存
0 | 2 参数错误
工具通用:
扫描排除生成物与隐藏文件；目录不存在返回空而非报错
只认文件开头的 --- frontmatter 块；解析失败视为无 frontmatter，交校验器报告，不中断
标量/列表/None 统一归一为列表；整数判定排除布尔
输出确定性：文件名排序、分组排序、JSON 不转义非 ASCII、缩进 2、尾换行
slug 冲突保留首个并告警
```

---

## 5. 环境与依赖

### 5.1 宿主需提供的能力 [规范约束]

| 能力 | 用途 |
|---|---|
| 能力单元声明与触发（名称 + 描述 + 斜杠命令） | §4.1 接入 |
| 子 agent 委派（可并行） | 批量下载 |
| 结构化向用户提问（单选，2–5 选项） | 全部 CHECKPOINT |
| 文件读 / 写 / 精确编辑、文件匹配、内容检索 | 全部单元 |
| Shell 执行：HTTP 客户端（curl 或等价）、Python 3 + YAML 解析库、`pdftotext`（poppler）、JSON 处理 | 工具层 |
| 外网访问：OpenAlex、arXiv、Unpaywall、Crossref（可选 S2）、OA PDF 主机 | FETCH, TRACE |
| 可选：可视化页面发布 | graph >15 条边、引用图可视化 |
| 环境配置：联系邮箱（必需）、S2 密钥（可选） | polite pool / Unpaywall |  权限白名单（PERM）原实现只含 allow 项：读取 skill/agent 配置目录、委派子 agent、执行 HTTP 客户端、执行工具脚本（Python / shell）、执行 pdftotext。无 deny 项 [推荐做法]。

### 5.2 假设与限制
- - 只处理能取得 OA 全文或用户提供 PDF 的论文；其余停留在 abstract-only。
- pdftotext 对扫描版、双栏乱序、数学符号提取质量差；锚点校验因此有模糊与人工确认层，不能视为绝对真值。
- 影响力评分依赖 OpenAlex 覆盖度；覆盖缺口、自引团、负面引用需模型复核。
- 雪球法继承种子偏见；用弱连通孤立分量反查盲区，但不能消除。
- 精读耗时为估计值，仅用于档位选择。
- 工具零语义判断：所有"是否相关 / 是否矛盾 / 旗标是否成立"均由模型决定。

## 6. 验收标准

### 6.1 行为快照表

| # | 输入 | 期望行为 | 期望输出示例 |
|---|---|---|---|
| A1 | `/paper-fetch 1706.03762` | 识别为新式 arXiv ID；主记录选 2017 会议版而非重印记录 | slug `vaswani2017_attention`，`arxiv_id: "1706.03762"`，`arxiv_version` 形如 `v7`，`access_level: full-text`，RAW 含原始响应与候选列表 |
| A2 | 同 A1，24 小时内再次执行 | 缓存命中 | 零网络请求（可用断网或 HTTP 请求计数验证），直接返回已有 META，`fetched_at` 不变 |
| A3 | `/paper-fetch "Attention Is All You Need"` | 标题搜索首位为无 venue 的新年份记录时，按四元组选回 2017 版；引用数取簇内最大 | slug 年份为 2017，不为 2025；`alternate_ids` 非空。（离线判定：用 RAW 中保存的候选列表作为夹具重放，结果应相同） |
| A4 | `/paper-fetch 10.48550/arXiv.1706.03762` | DOI 查空后降级到 arXiv ID / 标题策略 | 与 A1 同 slug，不报"不存在" |
| A5 | 非 OA 期刊论文 DOI | 不下载 | `access_level: abstract-only`，提示用户提供 PDF，流程不中断 |
| A6 | 候选 PDF URL 返回 HTML 着陆页（可用本地 mock 服务构造） | 魔数校验失败，自动尝试下一级 | 最终取 arXiv PDF 或降级；FETCH 输出中列出被跳过的 URL 与原因"非 PDF" |
| A7 | `/paper-read vaswani2017_attention` | 先输出 5C + 门控三行并等待确认 | 未确认前无第二遍内容 |
| A8 | 精读 abstract-only 论文 | 只产出 5C/骨架/摘要级总结 | 所有 claim `strength ≤ 1`，locator 均为 `Abstract`，无全文级论断 |
| A9 | 笔记中某 📖 引文跨行被连字符断开 | L1 未中、L2 命中 | 校验结果 ✅ 并标 `⚠️ L2-match` |
| A10 | 笔记中某 📖 引文为编造句子 | 重合度 <80% | ❌「疑似幻觉引用」+ 最相近候选片段；check 退出码 1 |
| A11 | 论文无方差报告但 Evidence 想打 5 | 红旗 `no-variance` 生效 | 红旗表在评分表前；对应数值差异为"不可判定" |
| A12 | `/paper-compare A B`，A 参数量为 B 的 3.2× | 审计第 3 项 ❌ | `fairness_grade: 低`；性能对比降级为不可比，结论不以此为推荐依据 |
| A13 | 两篇在不同数据集上结论相反 | 判定流程在 Step 4 停止 | 冲突类型 `scope`，张力卡草案 `status: open`，`scope_hypothesis` 非空 |
| A14 | `/paper-kb link a b improves --by self --evidence "§2"` | 只改 a 的 relations 并重建 | b 文件未变；反链生成物出现 `## [[b]]` 下 `- ← [[a]] (improves, evidence: §2, by: self, ...)` |
| A15 | `/paper-kb link a b relates-to` | 类型校验失败 | 拒绝写入并列出合法类型 |
| A16 | `/paper-kb add concept 多头注意力`，已有卡别名含"多头注意力" | 查重命中 | 三选一提问（合并/独立创建/取消） |
| A17 | 创建 open 张力卡指向卡 c | 自动迁移 | 卡 c `maturity: contested` |
| A18 | `/research-trace X` | 方向锁定提问 → 纳排标准先于检索落盘 → 雪球 → PRISMA | 报告 8 章；每阶段 `进入 = 排除 + 通过`；未饱和时有 ⚠️ 标注 |
| A19 | 里程碑池 12 篇 | 正常评分 | 输出含小池子警告与"影响力 ≠ 质量"免责声明；每项含 raw 与 z 分 |
| A20 | 会话结束 | 日志双写 | 方向日志新增条目；时间线日志新增一行；无判断变化写"无" |

### 6.2 边界用例

1. **空项目首次运行**：所有数据区为空 → 盘点如实输出"空"，不报错，正常进入路由。 2. **批量 10 篇，其中 3 篇非 OA、1 篇扫描版**：分两批（≤8）并行下载；结束后一次性汇总询问；汇总四段计数之和 = 10；扫描版为 `oa-pdf` 并提示 OCR。 3. **slug 冲突**：两篇不同论文均生成 `zhang2023_rag` → 第二篇为 `zhang2023_ragb`；重新 fetch 第一篇仍得 `zhang2023_rag`。 4. **矛盾矩阵为空**：两篇论文无可对齐命题 → 报告"无可直接对比的命题"并给覆盖率，不强行聚类。 5. **引用图含环**（预印本与正式版互引）：按破环规则删边，`cycles_removed` > 0，层级计算正常结束。 6. **OpenAlex 搜索临时不可用**：转 ID 直查 / arXiv；TRACE 产物标注"检索源受限"。 7. **旗标候选未确认**：`tier_after_flags` 等于 `tier_before_flags`；确认后重算才提档。 8. **yaml 数组两种写法混用**：`types: [a, b]` 与块式列表均被正确解析。

### 6.3 已知失败模式清单（复现后应能被检出或规避）
> 本表为质量审阅清单，用于人工/评审 agent 抽查，不作为自动化通过判据；自动化判据以 §6.1、§6.2 为准。

| 失败模式 | 检出/规避机制 |
|---|---|
| 摘要复述冒充"一句话总结" | 模板要求为判断，并体现红旗 |
| 把方法描述当 claim | 判据"这句话可以是错的吗"；falsifier 必填 |
| 遗漏 negative claim | 强制提取；CMP 对齐时重点检查 |
| strength 取平均 | 规则为最小值；校验可比对 |
| 用 💡 包装原文观点逃避校验 | G1 禁止项；审阅时抽查 |
| 批判只挑软柿子 / 数量堆砌 / 无位置 | 每条质疑须有位置、辩护、严重度 |
| 门控走过场 | 门控为硬 CHECKPOINT |
| scope 冲突误报为真矛盾 | 固定判定顺序；scope 必填 |
| SEM 当 σ | σ = SEM × sqrt(n) |
| 同课题组系列论文算独立来源 | 独立论文判据 |
| 张力卡僵尸化 / 卡片爆炸 | 180 天陈旧检查；分诊规则 |
| 日志写成活动流水 | G20；模板字段"判断的变化" |
| 双向手写关系漂移 | G6；反链只由工具生成 |
| 版本不去重导致引用双计 | D1 + 簇内合并 |
| 上下文爆炸 | G16；批量只保留 slug + 一句话 |

## 7. 复现实施指南

### 7.1 分步建议（自底向上）

| 步 | 内容 | 检查点 |
|---|---|---|
| 1 | 建协议层：把 §4.2–§4.8、§4.15–§4.16 落为宿主可显式加载的协议文档；建数据区（§3.1 代号） | 各单元指令只引用协议，不复制内容 |
| 2 | 实现 API 封装（T-OA / T-ARXIV / T-UPW）：限速、重试、错误体识别、RAW 落盘 | §4.2 八条异常行为各有对应分支；"查无"与"参数错"退出码不同 |
| 3 | 实现 T-RESOLVE + T-PDF + FETCH 指令 | 通过 A1–A6、边界 2、3 |
| 4 | 实现 T-KBCHECK 的锚点校验（先于 READ） | 通过 A9、A10 |
| 5 | 实现 READ 指令与 R-* 手册、笔记模板 | 通过 A7、A8、A11；笔记过 schema 校验 |
| 6 | 实现 T-KBBUILD / T-KBQUERY / T-KBCACHE 与 KB 指令 | 通过 A14–A17、边界 8；重复构建输出字节一致 |
| 7 | 实现 CMP 指令与 R-* 手册 | 通过 A12、A13、边界 4 |
| 8 | 实现 T-SNOW / T-CITE / T-SCORE 与 TRACE 指令 | 通过 A18、A19、边界 5–7 |
| 9 | 实现 ORCH：盘点、路由、CHECKPOINT、批量调度、日志 | 通过 A20、边界 1、2；7 个 CHECKPOINT 均可触发 |
| 10 | 端到端：用一个 3–5 篇的小方向跑"深入了解 X"整链 | 产物齐全；`check --anchors` 无 FAIL |

### 7.2 必须遵守项
- - §4 全部代码块中的字段名、枚举值、格式字符串、阈值、公式、权重、判定顺序。
- §4.1 的单元名称与描述原文（触发契约）。
- §3.4 中标为 [规范约束] 与 [行为必需] 的全局约束；§3.5 全部设计决策。
- CHECKPOINT 的位置与"需用户确认才继续"的语义。
- 工具不做语义判断、生成物不手改、关系单向写入。

### 7.3 可替换项

| 项 | 说明 |
|---|---|
| 目录布局、文件名、数据区物理位置 | 只要各数据区可区分，且生成物可被扫描排除 |
| 工具实现语言与拆分粒度 | 原实现为 Python 3（仅标准库 + YAML 库）+ 一个 shell 下载脚本；可合并或改写 |
| 邮箱/密钥配置项名称 | 保留"必需/可选"语义即可 |
| User-Agent 字符串中的工具名 | 须含 mailto |
| 子手册的拆分方式 | 内容须覆盖 §4 对应部分 |
| 可视化方式（Mermaid / 页面） | 节点上限与实线/虚线语义保留 |
| 模型档位 | 编排者推荐用最强档 |

### 7.4 原实现内部不一致的裁决（复现时按此执行）

| 冲突 | 裁决 |
|---|---|
| PDF 回退被称作"三级"，实列 4 级 | 3 个来源 + 1 级降级，按 §4.2 实现 |
| PDF 校验文档含"`file` 命令确认"，代码只做体积 + 魔数 | 体积 + 魔数为必需；`file` 检查为推荐 |
| Unpaywall 级在原代码中因缺 DOI 传参实际不可达 | 有 DOI 时必须执行该级，无 DOI 跳过 |
| 主记录：获取协议用四元组，雪球文档写"保留引用最高者" | 统一用 D1 四元组；引用数取簇内最大 |
| 关系类型"14 种"与实列 18 种 | 以 18 种为准（§4.15） |
| frontmatter 协议称"四种 schema"，实有 note/card/tension/compare + paper(JSON) | 以 §4.8 + §4.4 为准；MOC 用事实格式 |
| 引用速度分母文档无 `max(1, ·)`，代码有 | 采用代码版（§4.11），避免未来年份除零 |
| 小池子"强制最小档位人数"文档有、代码无 | 警告为必需，最小档位人数为推荐 |
| 旗标 `de-facto-standard` / `founds-subfield` 工具不自动产出 | 只能由模型/人提出并确认 |
| 重试次数：协议写 3 次，代码 5 次（含 retryAfter） | ≥3 次指数退避 + 尊重 retryAfter（上限 60s） |
| OpenAlex filter 键 `openalex_id` vs `ids.openalex`、`per-page` vs `per_page` | 先探测，以实际可用为准 |
| 模板含 `title_zh` / `venue_type` / `updated`，schema 未列 | 作为 ➖ 选填纳入 note.v1 |
| 引用图语义边 6 类与 KB relations 18 类不同 | 两套独立枚举；跨层沉淀时按 §4.11 映射 |
| 张力卡 parties 的 evidence 必填，但 position 允许 silent | silent 时 evidence 填 `—` |
| 原实现 metric 冲突映射为"open 或 dissolved"，未给判据 | CMP 草案固定 open；dissolved 只在 KB 生命周期中依据新证据迁移 |
| 新卡"至少 2 条 relations" vs seed 定义"关联 0–1 条" | 新建卡必须 ≥2 条（不足则 check 报孤立/WARN）；seed 的判定只看论文数（=1 篇），"关联 0–1 条"仅描述历史卡片，不作为拒绝条件 |
| CMP 中 temporal 不建卡 vs 张力卡 temporal 可直接 resolved | CMP 自动流程对 temporal 不建卡、改写 relations；用户在 KB 中手动为 temporal 冲突建卡时，可直接 `resolved` 并适用"无需实验"特例 |
| 理论论文 Reproducibility 判 N/A vs rating 六维必填 int | 允许 `rating.reproducibility: null` 表示 N/A，校验器接受 null，评分理由中注明"无复现对象" |
| repro_score、文献耦合、is_bridge、MOC 结构信号等原实现未固化的计算 | 按文中 [推荐] 规则实现，可替换但须在产物中保持一致 |
