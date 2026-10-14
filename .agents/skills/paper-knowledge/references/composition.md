# K4 · 一组有依据的更新候选

**输入准备：** 按 [owner 契约](../../paper/references/unit-owner.md#输入暂停与恢复)登记旧页/冻结对照/相关知识的真实快照；缺输入时 awaiting_input→补齐→preparing，本人继续。接受产物按引用使用，成文才加载下面资源。

本轮 E7。加载科学草稿中接受增量、最新正式相关正文、[knowledge 模板](../assets/knowledge.md)、[写作](../../paper/references/writing.md)、[Obsidian](../../paper/references/obsidian.md)和必要 [审阅协议](../../paper/references/review-publish.md)。

以已有页为基础合并可跨情境使用的机制、实例与成立/失败条件，不用新摘要覆盖整页。单篇来源可以支持早期知识，但须显式说明单来源边界与待检验反例；后续来源在同页调整条件。论文特有数值和完整流程留论文页，对比选择留专题页。用户原话、旧有效解释、身份与稳定块保留；知识→论文实例/证据→原始版本可回查。

论文/专题的修改以目标、候选基线哈希、建议片段、依据及是否需科学确认形成局部 diff，交唯一候选写者整合；实质判断由原科学 owner 核语义。不能写第二份现行论文候选，来源或目标新编辑使旧整合失效。唯一写者用 `workflow integrate --run <run> --unit <写者unit> --agent-id <写者ID> --payload <整合.json>`；payload 为 `transaction_id/target/base_sha256/owner_observed:true/actual_owner_record/fragments`，各片段含 `old/new/locator/evidence:[{path,locator}]/needs_owner_confirmation/owner_confirmation`。基线和真实 owner 确认必须有效，不能用布尔值冒充调用记录。整合使成文回 preparing，并使 report/final_review 失效；本人 begin→checkpoint→最终复核后重交候选，不物理重启。只将真实变化组成一组候选/diff；无增量记录不变理由，仍检查必要关系。拆并需旧入链迁移方案，语义重绑定由作者判断。

交付最终候选目标映射、修改原因/依据、保留和链接检查结果、残留限制。正式提交由协调单写者核最新编辑与实际授权；候选状态不代表已发布。完整单元结束后仅交最终候选小包。

交付前做语义验收：离开特定论文，本页能否解释机制、适用前提和失败边界；新证据究竟改变哪一句现有判断。没有增量就给不更新理由，不为完成流程造页。再按 [共用写作](../../paper/references/writing.md#交付前的展示检查)核实例、来源与展示；结构检查与实际界面检查分开记录。
