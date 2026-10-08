# T3 · 一轮前后向扩展

本轮 D4，所得候选再按 D3 筛选。对已纳入代表工作执行真实前向/后向查询，记录种子、方向、provider、时间及候选起源；provider 不支持一方向时明确限制，不编边。

先去重和识别版本，再按接受的标准给纳入/排除/待判断原因。新路线可能需要目标性阅读；只为待判问题取得材料，不默认精读所有候选。范围标准发生实质变化须记录并检查受影响决定。

交付当前轮真实集合、来源边、筛选和新覆盖/缺线。协调者接受后按预算/覆盖决定真实下一轮 `repeat` 或进入谱系/覆盖裁决；一轮返回零项不自动宣称全部研究饱和。结束交回控制，不自行无界扩展。

当前扩展工具：`uv run python .agents/skills/paper/scripts/research.py --root . query expand --trace <trace> --provider openalex --seed <真实检索record/work-ID> --direction both`。标识和方向以实际 provider 能力为准，Crossref 仅后向；占位符必须替换。
