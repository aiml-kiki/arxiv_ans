---
name: paper-read
description: 明确调用或已授权 paper 流程中，对固定版本论文连续三遍精读并形成可虚拟重构的完整候选。
---

# paper-read · 单篇理解与重构

输入：固定来源/版本及真实能力、用户问题、已有页/接受接口。输出：一篇连贯论文候选、关键证据定位与可迁移知识线索。未知和适用条件是结果的一部分。

## 当前轮路由

| 轮次 | 只加载的指南 | 当前交付 |
|---|---|---|
| R1 / 第一遍 | [orientation](references/orientation.md) | 问题模型、贡献声称、5C、特有疑问与入口 |
| R2a / 第二遍 | [mechanism](references/mechanism.md) | 输入/输出/状态契约与有效例子完整路径 |
| R2b / 第二遍 | [learning](references/learning.md) 或 [proof](references/proof.md) | 适用学习规则或构造/证明依赖；混合时分对象 |
| R2c / 第二遍 | [evidence](references/evidence.md) | 核心命题、正负证据、验证配方与支持范围 |
| R3a / 第三遍 | [reconstruction 的替代与作者辩护](references/reconstruction.md#替代与作者辩护) | 同约束替代、最强作者理由与条件裁决 |
| R3b / 第三遍 | [reconstruction 的区分验证与细节保留](references/reconstruction.md#区分验证与细节保留) | 可观察区分方案及必留细节落点 |
| R4 / 成文 | [composition](references/composition.md) | 图与正文一致的完整论文候选 |

首次派发给一篇论文的完整目标，由独立 owner 读 [自主执行契约](../paper/references/unit-owner.md)。按上表向同一科学草稿增量写入→来源回查/自查→原子 checkpoint 接受或返工→再加载下一指南；所有阶段在本人会话完成，只把最终候选/摘要或异常交父会话。长文可同遍拆轮，三遍不能在初始 prompt 中合并要求；自查不冒充独立审查。

B4 只在真实学习过程存在时、B5 只在主要理论主张存在时选择；混合论文分别核对象，无适用项具体说明，不造训练/证明节。[系统](assets/modules/system.md)、[数据集与测量](assets/modules/measurement.md)、[综述](assets/modules/survey.md)仅论文涉及且当前问题需要时加载，不把全部检查施加每篇。第二遍/第三遍共同保留改变计算、可见性、训练/证明、状态、成本、验证或取舍的细节。

成文才读 [论文模板](assets/paper.md)、[共用写作](../paper/references/writing.md)及 [Obsidian](../paper/references/obsidian.md)。方法详解先给一幅反映真实结构的机制总览，图后给原生可点击导航，再沿路径解释；不按三遍拼接报告。科学缺项回当前作者补，结构合规不是科学接受。

协调者在成文后把有价值候选送 paper-knowledge，按共享审阅协议提交；本作者不直接改正式页或共同知识。虚拟重实现不等于真实实验复现，不制造用户原话或作者未披露步骤。
