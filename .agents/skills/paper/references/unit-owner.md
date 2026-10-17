# 单元 owner：连续科学责任

首次或真实物理替代时读能力入口与本契约；需要选模型/助手时读 [路由](../agents/routing.yaml)，当前阶段再读其指南。启动封套不加载全部入口、未来指南、全部协议、模板、48 叶目录或父历史。完整精读在任一时刻只有一位科学责任 owner；在已接受边界有成本依据地物理交接时，由新 owner 核源并明确接管责任。助手不承接整遍责任。

## 科学承载与阶段循环

每个 unit 的科学草稿为 `.research/work/<run>/units/<unit>/science.md`，并维护**一份短科学状态**；检查点、来源/产物版本及可推导元数据由脚本生成。默认 R1/R2a/R2b/R2c/R3a/R3b 是科学接受边界，不各写长报告、answers 或 critical_details 副本。短科学状态只保存当前问题、已接受机制/边界、接口引用、必留细节、决定性证据/竞争解释、未知、用户约束与下一动作。

只有隔离评估显式设置 read unit 的 `checkpoint_mode: pass_trial` 时，R2a→R2b→R2c 与 R3a→R3b 仍依次读、写、按来源自查，但各自用 `workflow substep --payload` 记录可定位增量与固定证据；整遍 R2/R3 各做一次原子 checkpoint。未记录完内部问题不能提交整遍；全部不适用的内部问题逐项判定后以 `decision:not_applicable` 明确跳过。内部记录是临时进度，不是已接受科学结论；返工改动前一问题时定向 reopen 整遍，清除内部进度再顺序核证。原默认模式保持不变，只有质量与恢复测试不降且完整树计量有收益才推广。

1. 首次读一次 `workflow bootstrap` 并用 `workflow step --operation prepare --agent-id <本人ID>` 取得当前问题与未读资源。后续直接使用 checkpoint/substep/tasks 回执的 `continuation`，不再逐轮调用 next＋resource。只准备当前问题，不自动 begin 或接受科学判断；待适用性/输入缺口处理后本人 begin。恢复才取 next/recovery，资源变化、压缩丢失或关键回查用具体 kind/index 和必要的 `--refresh`；相同实例已交付的同哈希协议小节不重复发送。路径/哈希/交付字节不等于理解或真实 prompt token。
2. 在 preparing 选适用任务并固定输入。旧正式页、冻结对照及下游需要的接口先登记真实快照再读；检查依赖后 begin，才进入 executing。`start-owner` 只绑定，不直接开始首轮。
3. 沿当前问题读原文并向同一科学稿新增/修正解释、证据、必要配置和未知后果。每轮短状态写“相对前轮新增的推断→支持/反证→仍未决”，正文承载完整解释；重复摘录不算科学增量。第一遍保存接受后再读第二遍；第二遍机制/学习或证明/证据接受后，第三遍才基于这些结果重构、挑战并裁决。科学问题不能一次大 prompt 合并三遍。
4. 回查影响判断的接口、条件及正负证据，记录具体问题、发现及定位。原子 checkpoint 绑定科学增量、真实覆盖、自查与草稿/来源哈希；接受后回执只准备下一阶段及其资源，失败不推进，事务重放不重复交付或接受。`self_check` 不冒充真实另一执行者的 `independent_review`，格式通过不等于科学通过。
5. 复用当前上下文中已读来源和刚写的科学增量；以短状态和定位接续，不为取哈希或下个问题重读整份科学稿。整合、恢复、来源变化或冲突才定向重读。合并同轮独立来源窗口和确定性工具操作，使用回执参数而不反复查 help/status；原图、负证据和未知接口仍逐项核。无科学变化不写空报告，科学缺项回源并重开受影响判断。

## 输入暂停与恢复

执行中发现缺材料，先进入 awaiting_input，暂停提交资格；补齐后回 preparing，仍由本人继续，不要求停止物理代理。用 `workflow step --operation awaiting-input --agent-id <本人ID>` 暂停，`workflow inputs --run <run> --unit <unit> --files <实际文件列表.json> --mode append|replace|changed-premise --reason <具体理由>` 登记变更；replace/changed-premise 还须 `--stage <最早受影响边界>`。append 保留旧来源，新增从此刻生效；replace/changed-premise 定位最早受影响边界并使其判断及最终验收失效；无关追加不重开正确机制，不回填此前已读。静默替换固定原文不可提交。

真实中断或按 [上下文成本与证据交接](context-cost.md)在已接受边界主动交接时才 interrupt/handover；后者须记录基于实际响应的保守全树成本理由、真实停止及新 owner 的关键来源复核。交接不以曾否调用助手为前提。自动压缩不是主动压缩接口；当前未暴露主动工具就不编造调用。恢复先读短科学状态及最近有效检查点、当前指南，再按引用补读；不重放阶段历史。来源按问题页/段读取，公式/图表必要时看原图，不默认文本和整页图重复送。

## 有收益的跨模型助手

先判脚本能否可靠完成，再判工作是否依赖本人完整机制理解；这两类分别交脚本或本人。其余边界明确、来源可定位、结果有下游用途且能独立验收的语言任务，按 [上下文成本与证据交接](context-cost.md)估计启动/输入、助手轮次、接收/核证、重试和等待的全树成本与质量收益；无收益或无容量本人完成，有收益才预留→实际 spawn→绑定→事件等待→核证整合→登记真实完成；未绑定的创建失败才取消预留。需要执行事件时只读 [宿主事件字段](host-runtime.md)，不为字段说明读实现。

完整精读默认请求中档连续 owner，先亲自承担三遍核心推理；若来源或任务在启动前已明确需要持续高档推理，记录理由再提高 owner 档位。遇到会改变中心结论的机制矛盾、不能排除的归因或同约束替代难题，先写明可能改变的中心判断、双方解释及固定原文位置，再把这个小问题交高档助手；评估启动、来源重读、核证与返工成本，普通整理不得升档。科学门失败时定向补证/复核，必要时记录下次运行提高档位，不把已接受科学轮伪装成切档。助手可按当前接口明确请求不同 model/effort，并用 `fork_turns: none`。完整历史继承不支持覆盖模型，followup 也不是热切档。请求和实际 model/effort 分开登记，无法观测实际值在事件中填 null，并如实说明未核验。配置不改变当前实例。

助手输入仅含唯一问题/用途、必要机制前提、固定来源窗口、排除项、接受条件、本人独占结果路径和预算边界；只读相关短协议。助手只写自己的结果，不改 owner 科学稿/共同候选/正式页，默认不再委派。返回回答、必要数值/配置/关系、版本/页码、条件/不确定/冲突及完整结果路径；通常 300–600 token 是软起点，必要接口/反证不能硬截断。本人定向核重要证据后整合，助手不向主会话汇报。相关同档小任务可复用助手，独立问题新建须仍有收益；不每叶建代理。

低档助手可接脚本或 owner 直接处理不够经济、能按原文位置和接受条件核验的局部分析，例如表格口径归一、实验对照提取、候选机制差异整理；它不独立解释中心机制、裁决反证或代写整遍。高档助手只接上段高风险科学问题，返回竞争解释、双方可区分观察和最强限制；不得默认重读整篇。风险不存在就不为“使用不同模型”而启动助手。无论哪档，owner 必须回查影响结论的证据并写自己的裁决；助手结论不自动成为科学稿事实。

图表/公式的局部视觉歧义按[来源协议](protocol-source.md#实际覆盖与图像核验)处理：先脚本抽取，必要时仅委派绑定页图哈希的短候选，owner 核决定性原图。选档同时看可验证性、科学风险、对完整上下文的依赖和全树成本；高档不可用而争议仍在时标未决，限制相应强结论，不静默降档宣称已核。

项目深度为根 0、owner 1、助手 2；全树实时容量包含根和所有活跃代理，打开线程另计。预留失败不 spawn，spawn 失败释放预留，结束不冒充线程已关闭。脚本只记事实和容量，不创建代理。

## 成文与最终小包

成文才加载一个目标模板、[写作](writing.md)与 [Obsidian](obsidian.md)。若 R4 的预计重复长上下文输入大于隔离成稿的完整树成本，可让全新上下文助手只组织**初稿**；本人交给它已接受科学边界、必要证据 ID、目标模板和旧页变动约束，不交整段历史。本人回查决定性机制、图中每条跨模块边、证据、反证与未知，裁决后写唯一候选及 checkpoint；若委派无净收益或质量不达标，本人成文。从科学稿整合独占候选，来源→正文及正文→来源核对必要细节、负面证据、未知后果和图文一致；不抄各阶段摘要。每目标仅一个现行候选；知识整合建议以基线哈希绑定局部 diff，由候选写者整合，需要科学确认的修改由原 owner 核定。

所有适用边界接受后登记最终 report，只返回候选/目标/绑定版本、真实覆盖、少量关键结论、知识影响线索、未决/异常、实际核验及 usage 引用。详细科学解释留文件，不上传助手集合或日志。无增量知识检查只有具体不变理由和必要关系检查。最终科学复核与授权/发布由协调单写者按 [审阅协议](review-publish.md)处理；本人不擅自发布正式页。

最终 checkpoint 接受后、report 交付前仍是本人的科学交付窗口，可完成必要局部核证；尚有 reserved/bound 助手时不能交最终 report，先真实结束或取消未绑定预留并核证。report 结束科学交付，不能据此声称宿主线程已关闭。

## 最小事务接口

```sh
uv run python .agents/skills/paper/scripts/research.py --root . workflow step --run <run> --unit <unit> --agent-id <本人ID> --operation prepare
uv run python .agents/skills/paper/scripts/research.py --root . workflow checkpoint --run <run> --unit <unit> --agent-id <本人ID> --payload <实际checkpoint.json>
uv run python .agents/skills/paper/scripts/research.py --root . workflow substep --run <试验run> --unit <read单元> --agent-id <本人ID> --payload <内部问题增量.json>
uv run python .agents/skills/paper/scripts/research.py --root . runtime reserve --run <run> --unit <unit> --agent-id <父ownerID> --event <实际预留.json>
```

checkpoint payload 包含 `transaction_id`、当前 `stage`、本次科学稿的 `draft_sha256`、`delta:[{locator,summary,task_ids}]`、本次 `evidence/coverage/judgment_changes/gaps`、短 `science_state`、具体 `finding:{review_kind,reviewer,summary,checks}` 与 `decision:accept|revise`；成文时提供 `candidates`。`delta.locator` 必须能定位实际标题、块 ID 或片段，summary 是其短含义，不重抄正文；短状态包含 `question/accepted_boundary/interfaces/critical_details/unresolved/next_action`。K1 无增量短退出附 `no_change_reason` 和 `relation_checks:[{target,reason}]`，仍给实际 evidence/coverage/finding；relation_checks.target 须是实际固定 inputs 或已接受路径，不能将虚构页面列为已查关系。它是必要检查记录，不产生空候选。字段以当前 help/契约为准，草稿 SHA 须由当前真实文件取得，其他固定版本和可推导元数据由脚本生成。

试验 `substep` payload 只含当前 `substep`、草稿哈希、可定位 `delta`（覆盖当前适用任务）、固定 `evidence` 与真实 `self_check finding`；全部不适用时只含 `substep` 和 `decision:not_applicable`，以已登记的逐项理由为依据。整遍 checkpoint 仍需覆盖本遍的证据与判断，内部进度不能代替它。

runtime `reserve/bind/complete/cancel` 均给 `--run --unit --agent-id <父ownerID> --event <真实JSON>`，status 只给 `--run`。bind 绑定真实 spawn/followup，已绑定助手真实结束/失败后 complete，cancel 仅释放未绑定预留；没有宿主调用回执不能填已执行。命令操作、原子检查点和科学核证分别记录，脚本接受不称独立科学验证。
