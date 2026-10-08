# T2 · 实际检索与语义筛选

本轮 D2/D3。输入为接受范围和标准、实际 provider。查询工具只提供真实集合；相关性和范围裁决由追溯者完成。

1. 真正执行问题导向 query，记录原查询、provider、时间、返回列表/限制；不能填未执行查询或虚构召回总数。
2. 按标识/版本/标题作者辅助去重，再区分同一 work、不同 report/版本与实质独立 study。不把同一研究多个版本算多份证据。
3. 对标准作纳入/排除/待判断决定，给具体原因与依据窗口。未知关键条件则待判断补源，不能自动排除；相关摘要不等于已阅读全文。

交付：持久真实查询集合、版本/工作分组、筛选决定及原因、待判断补证、真实覆盖与能力限制。接受条件：列表/计数来自实际返回，语义决定可回查且范围一致；不能用工具去重代替相关性判断。结束交回控制。

实际 API 取得走 `query search`；已有真实返回走 `query import`，明确其实际来源而非冒充本次 API 请求。筛选决定采用 query 契约由协调写者登记。例如（占位符必须替换）：

```sh
uv run python .agents/skills/paper/scripts/research.py --root . query search --trace <trace> --provider openalex --query <实际查询> --pages 1 --page-size 50
uv run python .agents/skills/paper/scripts/research.py --root . query screen --trace <trace> --input <实际筛选决定.json>
```
