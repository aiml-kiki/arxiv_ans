# 主会话：启动完整单元，接收最终结果

仅主会话在委派/恢复时加载本文件及路由；单元代理只加载自己的 [自主执行契约](unit-owner.md)。脚本保存顺序、身份和哈希，不调用模型，也不证明科学成立。

## 任务归属

1. 主会话确定用户目标、来源身份、授权、目标路径及单元依赖；不提前阅读每篇的科学指南、原文或中间产物。每篇论文一个独立 read owner；比较、追溯、集中知识更新各有自己的 owner。
2. 初始化计划 `autonomy: unit`。首次 `spawn_agent` 使用 `fork_turns: none`，只给真实 run/unit、库根、能力入口、来源/目标、用户约束和完整交付目标。不要给“只做 R1 后结束”的任务。
3. 真实 spawn 后用 `workflow start-owner --event` 登记一次；初始任务注明代理须等到该登记绑定本人真实 ID 后才写阶段状态，短暂登记时差不构成续派请求。代理取得拥有权后在自己的会话执行所有阶段，单元内自查、局部复核和返工不回主会话。
4. 主会话等待完成或异常，只查 `workflow status` / `workflow report` 简包。比较者直接从文件读取两篇的最终接口和必要证据；主会话不先读中间结果再转述一遍。知识整合者顺序去重，只有其本人写共同知识候选。
5. 主会话只审最终候选、具体差异、决定性结论的来源与真实检查状态；普通遗漏定向交原 owner 修正。结构检查失败优先读诊断，不把全库正文放进主上下文。最终授权/冲突保护/正式写入与 finish 仍由主会话单写者执行。

科学阶段记录的接受是单元内部质量门，不是主会话逐轮批准或独立评审。主会话的最终审阅也不能代表真实实验复现。用户主动带读时才设 `autonomy: interactive`，按遍交用户讨论；不要把该例外变成默认研究模式。

## 上下文与资源

- 主会话不加载能力入口的全部指南，不运行 `next --detail full`，不读阶段 md/json/累计证据，不接收逐轮长消息。最终报告给候选路径/哈希、固定来源、简要结论、知识线索和未决/异常；详细结果仍落盘可追溯。
- `next`、`recovery` 默认只给当前任务、资料入口及接受产物路径/哈希。正文由需要它的代理按问题读取；`--detail full` 仅明确审计/恢复缺口使用。
- 独立来源 IO 限速并发；多个独立 PDF 缓存未命中时才用文件级进程池。模型不抄写确定性解析/数值核对程序。
- 默认最多两位单元 owner，宿主容量允许时留一位局部助手；实际所有活跃代理计入宿主全树上限。没有助手槽就在 owner 内完成或排队，不为模型分档无限增代理。
- 不同 owner 只修改各自独占目录和本人 unit 的状态，workflow 每次读改写由脚本锁串行保护。索引、候选组和正式提交仍单写者；并发不得覆盖共同正文。

## 中断与异常

优先恢复原 owner。只把简版 `recovery --unit` 地址与新用户约束交它，已接受正文不重复发到主会话；原实例不可用时记录原因/真实新 ID，恢复同一逻辑 unit。既有逐轮运行不伪造历史：真实中断后用 `adopt-owner --event` 显式接管；然后真实恢复原代理并用 `dispatch mode:followup` 登记，待审结果恢复另加 `resume_review:true`。接管本身不代表宿主调用，此后在同 owner 内继续。修正只重开受影响阶段。

只有需要用户缺失信息、权限、跨单元接口冲突或实际额度/平台故障才向主会话报异常：具体阻塞、已完成范围、恢复入口及所需决定；不夹带所有过程材料。脚本无法隔离已经进入主会话的历史，但下一轮不再把它 fork 给代理。

## 最小 CLI

```sh
uv run python .agents/skills/paper/scripts/research.py --root . workflow init --run <run> --plan <计划.json>
uv run python .agents/skills/paper/scripts/research.py --root . workflow start-owner --run <run> --unit <unit> --event <真实spawn.json>
uv run python .agents/skills/paper/scripts/research.py --root . workflow report --run <run> --unit <unit>
uv run python .agents/skills/paper/scripts/research.py --root . workflow recovery --run <run> --unit <unit>
```

字段以当前 `--help` 为准；实际调用填真实 ID、模型请求和事件，运行时未暴露配置记 null。候选仍按 [审阅协议](review-publish.md)展示和提交，不能把内部自主执行当作正式页写入授权。
