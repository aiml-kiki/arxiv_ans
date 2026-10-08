# 协调者：真实交接与短轮次

仅开始委派、续派或恢复时加载；各作者收到当前轮工作包，不需要整份执行指南。阶段与叶任务契约由 `assets/schemas/` 维护。脚本登记事实、检查依赖及哈希，科学接受由协调者判断。

## 从当前目标构建任务图

1. 定位现有正式页、固定来源、未完成 run/待审提案和有效授权。缓存核验通过就复用；缺身份/材料才走 paper-fetch。研究范围不明且会改变对象时才澄清。
2. 选必要能力。每篇独立完整精读为一个 read unit；给定论文比较依赖所需 read 结果，方向追溯不自动精读全部候选。知识候选统一在一个正式快照上去重、顺序更新。
3. 在 `workflow init` 的计划中明确 unit、输入、目标和依赖。不把所有 48 个叶任务交给代理；不适用任务有实际理由。经验/理论任务 B4/B5 在 R2b 前经 `workflow tasks` 明确。
4. 默认同时最多两位子代理，覆盖所有角色。下载/查询用限速并发；独立 PDF 解析可用文件级进程池；共享索引、状态、提案和正式写入保持协调单写者。缓存全命中不启动解析池。

知识链接可在成文前接入：知识单元依赖单篇 R3b 或比较 M3 的已接受科学结果，先完成候选/影响决策，再将稳定页名、关系与必要解释用 `workflow inputs --stage R4/M4` 登记给原作者；知识成文不重复接管该作者的论文/对比目标。只需处理已有知识时也先给作者相关正式快照。每个最终目标只有一个候选作者，协调者合并顺序明确，不能让两位作者各交同页后覆盖其一。成文后才出现实质新认识，则按受影响范围重开；导航整理不成为虚假科学更新。

## 当前轮真实调用

`workflow next` 返回当前轮和直达指南。协调者仅加：唯一目标、当前任务 IDs、用户最新约束、固定来源窗口、接受产物版本/关键接口、独占输出路径、接受条件及交回要求。只加载指定 `guide_section`，不得把后续小节当当前指令。约 1,000–2,000 新指令 token 为软设计预算；没有 tokenizer 就记未测，不能为预算删科学条件。

- 第一次派发用宿主实际 spawn 工具。同一 unit 后续使用**同一 agent 的真实 follow-up**，每轮结束后交回控制。协调者先检查当前科学答案和决定性来源，accept 或给具体 revise finding，再续派下一轮。
- `workflow dispatch` 在真实调用后登记 `observed: true`、mode、agent ID、tool、call ID/实际事件记录。不能提前把“预计调用”写成实际执行；不可把读取指南记作 spawn。
- 作者按 workflow 契约交 `result` 和实际正文产物；无具体证据的“已完成”不是接受依据。接受绑定本轮版本/哈希；相关修正使必要依赖阶段重开，不全量重读。
- 三遍之间始终有真实边界；第二遍可机制、学习/证明、证据分子轮，第三遍可替代/辩护、验证/细节分子轮。短文可以合并同一遍紧密任务，不能合并三遍。
- 比较者用接受的单篇结果及关键双端来源，缺口只给原阅读者定向补证；父会话不重复全文精读。普通缺项原作者补，困难局部才委派有边界核验；自查不是独立评审。

### 阶段交付接口

正文写在 `workflow next` 返回的独占绝对路径，对应 `.research/work/<run>/units/<unit>/results/<stage>.md`；JSON 封装另交 `submit`。本轮必需字段为 `stage`、`answers`（具体 question/answer/task_ids）、`evidence`（真实 path/locator）、`coverage`（实际 read/image_checked/not_covered 及原因）、`judgment_changes`、`critical_details`、`gaps`。answers/evidence/coverage 非空，前三者之外的列表确无内容时可以为空；不得用空字段代替科学答案。正文只存结果/证据及理解变化，不存逐步思考。

成文轮另交 `candidates` 的 draft/target 映射：draft 位于 `.research/drafts/<run>/units/<unit>/`，target 仅正式三目录。协调者 accept/revise 的 finding 给实际 reviewer、summary 和具体 question/finding/locator checks；返工另给 required_changes。字段事实源为 `assets/schemas/workflow-contract.json` 的**当前对象**，不向作者加载完整 48 行或未来阶段字段。

## 模型与恢复

实际工具能力优先于 routing.yaml 偏好。完整阅读初次按全任务需要选择模型/推理等级；宿主 follow-up 不支持切换模型时，同实例不虚构“先便宜后升级”。复杂比较/谱系按其需要初次选高档；确定性操作用脚本，简单定位或数值核验可局部低成本代理。主会话不会因 YAML 自动切换。

持久恢复记录保留接受产物哈希、接口/关键值及作用、条件/例外、实际覆盖、用户修正、开放问题、授权和下一轮。不存完整逐步思考、原文副本或全部旧工具输出。压缩后核对恢复包及正式页最新修改；实际 agent 不可恢复时记录替代原因和真实新 ID，保持同一逻辑 unit，不称物理代理未变。

用户“继续”从有效目标和接受状态接续；状态问题简答后继续。真实额度/平台故障按宿主规则恢复重试，不反复建代理规避限制。新约束只重开受影响阶段；用户明确取消/替换才换目标。

## CLI（库根运行，JSON 放文件）

```sh
uv run python .agents/skills/paper/scripts/research.py --root . workflow init --run <run> --plan <计划.json>
uv run python .agents/skills/paper/scripts/research.py --root . workflow next --run <run> --unit <unit>
uv run python .agents/skills/paper/scripts/research.py --root . workflow dispatch --run <run> --unit <unit> --event <实际事件.json>
uv run python .agents/skills/paper/scripts/research.py --root . workflow submit --run <run> --unit <unit> --result <本轮结果.json>
uv run python .agents/skills/paper/scripts/research.py --root . workflow accept --run <run> --unit <unit> --finding <具体核验.json>
uv run python .agents/skills/paper/scripts/research.py --root . workflow recovery --run <run> --unit <unit>
```

尖括号是文档占位符，实际用真实路径/ID；CLI `--help` 及 schema 是字段事实源。返工用 `revise`，失效用 `reopen`，查询迭代用 `repeat`，新约束用 `constraints`。不同意接受时明确缺哪项科学答案、去哪一来源、什么结果可接受；不能只写“更深入”。

最终科学结果进入知识影响检查，再按审阅协议形成一组候选；具体授权范围内 publish，完成后 finish。过程阶段不自动成为正式页，持久研究证据不因 finish 丢弃。
