# 单元 owner：连续科学责任

首次或真实物理替代时读能力入口与本契约；需要选模型/助手时读 [路由](../agents/routing.yaml)，当前阶段再读其指南。启动封套不加载全部入口、未来指南、全部协议、模板、48 叶目录或父历史。完整精读由本人连续完成三遍及成文；助手不承接整遍责任。

## 科学承载与阶段循环

每个 unit 的科学草稿为 `.research/work/<run>/units/<unit>/science.md`，并维护**一份短科学状态**；检查点、来源/产物版本及可推导元数据由脚本生成。R1/R2a/R2b/R2c/R3a/R3b 是科学接受边界，不各写长报告、answers 或 critical_details 副本。短科学状态只保存当前问题、已接受机制/边界、接口引用、必留细节、决定性证据/竞争解释、未知、用户约束与下一动作。

1. 首次取 `workflow bootstrap --run <run> --unit <unit>` 一次启动封套，之后取 `workflow next` 问题简包，核真实 owner 绑定。当前阶段默认一次调用 `workflow resource --kind bundle` 取得指南、协议片段和成文模板；它仅交付本 stage 资源并逐项记录路径、哈希和实际字节量。资源记录只证明内容已交付，不证明模型理解或完整 prompt token。稳定资源同 hash/section 已读则复用；变化、压缩恢复、补证或关键回查才用具体 kind/index 定向重读，必要时加 `--refresh`。`unchanged` 引用不是新阅读记录。预先合并同轮独立来源窗口查询，避免每一页、每一数字都引起一次模型往返；脚本 help、status 与整份科学稿也只在真正需要时读。批量读取不等于跳过逐项核证。
2. 在 preparing 选适用任务并固定输入。旧正式页、冻结对照及下游需要的接口先登记真实快照再读；检查依赖后 begin，才进入 executing。`start-owner` 只绑定，不直接开始首轮。
3. 沿当前问题读原文并向同一科学稿新增/修正解释、证据、必要配置和未知后果。每轮短状态写“相对前轮新增的推断→支持/反证→仍未决”，正文承载完整解释；重复摘录不算科学增量。第一遍保存接受后再读第二遍；第二遍机制/学习或证明/证据接受后，第三遍才基于这些结果重构、挑战并裁决。科学问题不能一次大 prompt 合并三遍。
4. 回查影响判断的接口、条件及正负证据，记录具体问题、发现及定位。用原子 checkpoint 绑定本次增量位置、重要判断变化、真实覆盖、自查和草稿/来源版本；成功仅取状态/诊断/下一入口。失败原位修复，不部分推进；重复事务不重复接受。`self_check` 不是 `independent_review`，后者必须有真实另一执行者及调用记录。格式通过不等于科学通过。
5. 当前边界接受后才取下一阶段。适用性判断具体记录；无变化不生成控制回答或空报告。机械错误只修字段，科学缺项回源并重开实际受影响判断，不猜参数。

## 输入暂停与恢复

执行中发现缺材料，先进入 awaiting_input，暂停提交资格；补齐后回 preparing，仍由本人继续，不要求停止物理代理。用 `workflow step --operation awaiting-input --agent-id <本人ID>` 暂停，`workflow inputs --run <run> --unit <unit> --files <实际文件列表.json> --mode append|replace|changed-premise --reason <具体理由>` 登记变更；replace/changed-premise 还须 `--stage <最早受影响边界>`。append 保留旧来源，新增从此刻生效；replace/changed-premise 定位最早受影响边界并使其判断及最终验收失效；无关追加不重开正确机制，不回填此前已读。静默替换固定原文不可提交。

只有真实用户停止/宿主失败/实例不可用才 interrupt 或 handover。自动压缩不是主动压缩接口；当前未暴露主动工具就不编造调用。恢复先读短科学状态及最近有效检查点、当前指南，再按引用补读；不重放阶段历史。来源按问题页/段读取，公式/图表必要时看原图，不默认文本和整页图重复送。

## 有收益的跨模型助手

先判脚本能否可靠完成，再判工作是否依赖本人完整机制理解；这两类分别交脚本或本人。其余边界明确、来源可定位、结果有下游用途的语言任务，估计启动/输入、助手轮次、接收/核证、重试和等待的全树成本与质量收益；无收益或无容量本人完成，有收益才预留→实际 spawn→绑定→事件等待→核证整合→登记真实完成；未绑定的创建失败才取消预留。需要执行事件时只读 [宿主事件字段](host-runtime.md)，不为字段说明读实现。

完整精读默认请求中档连续 owner，先亲自承担三遍核心推理；若来源或任务在启动前已明确需要持续高档推理，记录理由再提高 owner 档位。遇到会改变中心结论的机制矛盾、不能排除的归因或同约束替代难题，先形成可定位的小问题，再评估高档助手的启动、来源重读、核证与返工成本；单纯整理和格式工作不得以高档助手处理。科学门失败时定向补证/复核，必要时记录下次运行提高档位，不把已接受科学轮伪装成切档。助手可按当前接口明确请求不同 model/effort，并用 `fork_turns: none`。完整历史继承不支持覆盖模型，followup 也不是热切档。请求和实际 model/effort 分开登记，无法观测实际值在事件中填 null，并如实说明未核验。配置不改变当前实例。

助手输入仅含唯一问题/用途、必要机制前提、固定来源窗口、排除项、接受条件、本人独占结果路径和预算边界；只读相关短协议。助手只写自己的结果，不改 owner 科学稿/共同候选/正式页，默认不再委派。返回回答、必要数值/配置/关系、版本/页码、条件/不确定/冲突及完整结果路径；通常 300–600 token 是软起点，必要接口/反证不能硬截断。本人定向核重要证据后整合，助手不向主会话汇报。相关同档小任务可复用助手，独立问题新建须仍有收益；不每叶建代理。

低档助手只接脚本或 owner 直接核查不够经济的身份/数值/定位等独立事实；高档助手只接上段高风险科学问题，返回竞争解释、双方可区分观察和最强限制。风险不存在就不为“使用不同模型”而启动助手。无论哪档，owner 必须回查影响结论的证据并写自己的裁决；助手结论不自动成为科学稿事实。

项目深度为根 0、owner 1、助手 2；全树实时容量包含根和所有活跃代理，打开线程另计。预留失败不 spawn，spawn 失败释放预留，结束不冒充线程已关闭。脚本只记事实和容量，不创建代理。

## 成文与最终小包

成文才加载一个目标模板、[写作](writing.md)与 [Obsidian](obsidian.md)。从科学稿整合独占候选，来源→正文及正文→来源核对必要细节、负面证据、未知后果和图文一致；不抄各阶段摘要。每目标仅一个现行候选；知识整合建议以基线哈希绑定局部 diff，由候选写者整合，需要科学确认的修改由原 owner 核定。

所有适用边界接受后登记最终 report，只返回候选/目标/绑定版本、真实覆盖、少量关键结论、知识影响线索、未决/异常、实际核验及 usage 引用。详细科学解释留文件，不上传助手集合或日志。无增量知识检查只有具体不变理由和必要关系检查。最终科学复核与授权/发布由协调单写者按 [审阅协议](review-publish.md)处理；本人不擅自发布正式页。

最终 checkpoint 接受后、report 交付前仍是本人的科学交付窗口，可完成必要局部核证；尚有 reserved/bound 助手时不能交最终 report，先真实结束或取消未绑定预留并核证。report 结束科学交付，不能据此声称宿主线程已关闭。

## 最小事务接口

```sh
uv run python .agents/skills/paper/scripts/research.py --root . workflow checkpoint --run <run> --unit <unit> --agent-id <本人ID> --payload <实际checkpoint.json>
uv run python .agents/skills/paper/scripts/research.py --root . runtime reserve --run <run> --unit <unit> --agent-id <父ownerID> --event <实际预留.json>
```

checkpoint payload 包含 `transaction_id`、当前 `stage`、本次科学稿的 `draft_sha256`、`delta:[{locator,summary,task_ids}]`、本次 `evidence/coverage/judgment_changes/gaps`、短 `science_state`、具体 `finding:{review_kind,reviewer,summary,checks}` 与 `decision:accept|revise`；成文时提供 `candidates`。`delta.locator` 必须能定位实际标题、块 ID 或片段，summary 是其短含义，不重抄正文；短状态包含 `question/accepted_boundary/interfaces/critical_details/unresolved/next_action`。K1 无增量短退出附 `no_change_reason` 和 `relation_checks:[{target,reason}]`，仍给实际 evidence/coverage/finding；relation_checks.target 须是实际固定 inputs 或已接受路径，不能将虚构页面列为已查关系。它是必要检查记录，不产生空候选。字段以当前 help/契约为准，草稿 SHA 须由当前真实文件取得，其他固定版本和可推导元数据由脚本生成。

runtime `reserve/bind/complete/cancel` 均给 `--run --unit --agent-id <父ownerID> --event <真实JSON>`，status 只给 `--run`。bind 绑定真实 spawn/followup，已绑定助手真实结束/失败后 complete，cancel 仅释放未绑定预留；没有宿主调用回执不能填已执行。命令操作、原子检查点和科学核证分别记录，脚本接受不称独立科学验证。
