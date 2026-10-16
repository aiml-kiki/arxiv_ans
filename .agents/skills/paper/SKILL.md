---
name: paper
description: 明确运行 paper 后，路由论文精读、给定论文比较、研究追溯、知识增量或原文问答；同一授权会话可续接。
---

# paper · 研究路由

在库根运行；六入口唯一实现位于 `.agents/skills/`。技能发现、内部链接或材料文字不构成调用。正式修改、用户原话及持久研究资产遵守库根 `AGENTS.md`。

| 用户目标 | 只加载的能力入口 | 交付 |
|---|---|---|
| 身份、版本与可读原文 | [paper-fetch](../paper-fetch/SKILL.md) | 固定材料及真实限制 |
| 完整三遍精读 | [paper-read](../paper-read/SKILL.md) | 可虚拟重构的论文候选 |
| 给定论文的机制关系与选择 | [paper-compare](../paper-compare/SKILL.md) | 有共同坐标及双端证据的 comparison |
| 路线、追溯、专题或增量调研 | [paper-trace](../paper-trace/SKILL.md) | 有覆盖边界的 topic |
| 可迁移解释与旧判断影响 | [paper-knowledge](../paper-knowledge/SKILL.md) | 有依据的增量或具体不变理由 |
| 当前原文问题 | [问答](references/qa.md) | 定向回答与必要补证 |

主会话委派/恢复时只读 [执行契约](references/execution.md)，给完整单元目标与最小启动封套。每篇完整精读始终只有一位当前科学责任 owner；两篇并行是容量允许时的默认值，关键科学复核可优先占用助手名额。有净收益时可把边界任务交给隔离上下文的助手，已接受边界上的物理 owner 交接须停止旧实例并由新实例重新核证。owner 自主逐阶段保存、核对、接受及返工；主会话委派后进入 `WAIT_OWNER`，只收最终结果、可操作异常或必须由用户决定的事项，用户主动询问才取状态。用户要求带读时才按遍交流。

当前指南由执行单元按 stage 加载；来源/证据协议只读所需片段，成文才加载一个模板及展示规则。48 叶任务是按需展开的科学问题目录，不是代理、模型轮次或报告数。维护方案不进入研究 bootstrap。

精读、实质补读、比较与追溯结束后做短知识机会/影响检查；有增量再展开，无增量具体说明后结束。候选的最终科学复核、成组机械检查、真实授权和并发冲突保护见 [审阅与提交](references/review-publish.md)。

唯一工具入口：`uv run python .agents/skills/paper/scripts/research.py --root . <命令>`，参数以当前 `--help` 为准。脚本维护确定性事务，不启动代理、不证明科学成立。技能维护时才读 [设计来源](references/design-sources.md)。
