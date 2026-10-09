# 维护资料：只在设计或优化技能时加载

本包实现一个研究入口、五个能力单元、单份共享协议、当前轮指南及四类成文模板。48 个叶任务是维护覆盖契约，不是 48 位代理或普通运行 prompt。完整设计位于 `.research/designs/paper-重构方案-20261008.md`。

## 采用的依据与边界

- [Agent Skills 规范](https://agentskills.io/specification) 与 [OpenAI Skills 文档](https://learn.chatgpt.com/docs/build-skills)：短入口、按需参考、脚本与 assets。六能力及指令软预算是本项目设计，不是官方规定。
- [Keshav 三遍阅读法](https://web.stanford.edu/class/ee384m/Handouts/HowtoReadPaper.pdf)：定位→理解内容→虚拟重实现与批判。实际代理交接是本包实现选择，不把三遍标题当阅读质量证明。
- [NeurIPS Paper Checklist](https://neurips.cc/public/guides/PaperChecklist)：假设、复现细节、统计及资源条件；按命题适配，不能用统一 sigma 阈值裁决所有实验。
- [PRISMA 2020](https://www.prisma-statement.org/prisma-2020-statement)：真实检索/筛选/覆盖及报告边界。只有实际系统调研才加载完整综述模块，不强制普通精读填写全表。
- [OpenScholar](https://github.com/OpenScholar/OpenScholar) 与 [PaperQA2](https://github.com/Future-House/paper-qa)：按研究问题检索、证据综合与反馈核验；本包不把自动引文或模型评审当独立实验证据。
- [OpenAI Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) 与 [多代理指南](https://developers.openai.com/api/docs/guides/responses-multi-agent)：主会话集中需求/决定/最终产出，子代理隔离过程、回传摘要；按任务选模型/推理档位。增加代理也可能增加 token，本包采用完整单元 owner、必要局部助手，不能承诺未测节省比例。
- [上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：最小工作包、持久高信号恢复记录与分工，不能据此承诺任意长程遵循率。
- [Obsidian links](https://obsidian.md/help/links)、[Properties](https://obsidian.md/help/properties)、[Callouts](https://obsidian.md/help/callouts)：原生交互语法；实际渲染验收须记录真实范围。
- [Advanced syntax](https://obsidian.md/help/advanced-syntax)、[Bases syntax](https://obsidian.md/help/bases/syntax)：表内 Wiki 别名转义、公式与原生视图。最近编辑用 `file.mtime`，分析更新日期用显式属性；旧页无分类也不能消失。排版规则只在成文/审阅加载，不能削弱科学条件或覆盖用户原话。

保留旧体系的自然正文、关键实现细节、用户原话、稳定块及库根链接；结合 arivx 的固定来源、显式命题/裁决、研究追溯与认知更新。拒绝按链接数/引文数给知识成熟度、删除引用边强行 DAG、把未披露项猜成实现事实、自动把全部临时记录写成公开卡片。
