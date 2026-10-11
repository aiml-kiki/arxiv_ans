# 知识增量、未解问题与研究记录

按当前任务读相应节。每次完整精读、对比或实质追溯结束均做短知识机会/影响检查；无增量记录具体不变依据与必要关系后退出，有增量才展开语义查重/影响及候选；有实际变化才修改，不按论文篇数、关系条数或页面年龄升级真理。用户原话和独特解释始终保留。

## 未解问题的稳定单元

长期问题放在最合适的论文/知识/专题正文中。直接说明未知项、竞争解释、现有证据为什么不能区分、最小区分观察及残余混杂；保留稳定问题块，不另复制问题记录。只有跨主题持续追踪不能由现有页承载时才提议独立问题页。

状态：`未解 / 已解决 / 已澄清为不同条件 / 暂缓`。缺证据不自动构成论文矛盾；暂缓不等于解决。新证据真正影响答案时可重开，并说明依据。需要索引的核心问题按 [正文绑定协议](protocol-claim-evidence.md) 给状态和参与命题定位；范围、竞争解释、区分观察、进展都指向已有可见正文，而非另存摘要。

## 认知更新单元

先查同一读者问题和定义，再查标题别名。处理可为新建、补充实例、澄清条件、修正判断、拆分、合并、留在论文、待核验、不变。已接受实例不能替代知识页的独立解释。

现行知识页直接解释当前原理和边界；只有改变理解的重要修订才在相关正文简述旧→新与依据，不为每次运行追加更新卡或整套审计字段。完整批准历史留持久更新/提交记录，保留用户原话与旧有效解释。正文绑定的增量类型、受影响定位是技术信息；旧判断、新判断和处理决定指向实际可见文字，实际变化必须有固定来源证据。`不变` 记录具体理由，不创造空正式页。

## 影响候选与依赖循环

```sh
uv run python .agents/skills/paper/scripts/research.py --root . derive build
uv run python .agents/skills/paper/scripts/research.py --root . derive impact --event source-v2 --source paper-example
uv run python .agents/skills/paper/scripts/research.py --root . derive resolve --event source-v2 --target '知识/状态复用.md#^c-cache-validity' --decision 需修订 --reason '新参数条件影响此结论，待同组审阅'
```

`build` 比较实际注册正文/字段及来源版本集合变化，生成持久影响候选；`impact --since <旧索引相对路径>` 比较指定旧快照，也可对真实新版本来源用 `--source`。事件绑定当前正文哈希，候选被用户改动后先重建并建立新变化事件，不能沿用旧复核结果。

传播使用明确判断依赖、visited 集合和实际变更，环不会反复唤醒。候选处理状态是 `待处理/已核/需修订/不变`，与科学裁决分开。来源更新只要求复核，不宣布旧结论错误；“已核”还需记录理由与证据。脚本永不自动改 Markdown，模型逐候选核语义；没有变化不改，真正变化才接续下一轮传播。

## 可操作的检索留痕

局部前驱核验、路线追踪、系统综述分别定范围与预算。只有系统综述需要完整系统报告；任何模式都保留真正查询、实际列表、日期、选择依据和限制。

```sh
uv run python .agents/skills/paper/scripts/research.py --root . query init --trace retrieval-lineage --question '状态复用如何改变检索接口？' --scope '候选进入时机和参数刷新' --budget '最多两轮雪球，优先必要前驱' --queries-budget 4 --pages-budget 4 --candidates-budget 200 --fulltexts-budget 8 --expansions-budget 2 --mode lineage --include '接口变化相关' --exclude '只改展示而未改机制'
uv run python .agents/skills/paper/scripts/research.py --root . query search --trace retrieval-lineage --provider arxiv --query 'all:"recommendation" AND all:"cache"' --limit 50 --pages 1
uv run python .agents/skills/paper/scripts/research.py --root . query status --trace retrieval-lineage --offset 0 --limit 30 --selection pending
uv run python .agents/skills/paper/scripts/research.py --root . query screen --trace retrieval-lineage --input .research/work/screen-decisions.json
uv run python .agents/skills/paper/scripts/research.py --root . query expand --trace retrieval-lineage --provider openalex --seed '<实际已取得的record/work ID>' --direction both --limit 50 --pages 1
uv run python .agents/skills/paper/scripts/research.py --root . query graph --trace retrieval-lineage --projection included
uv run python .agents/skills/paper/scripts/research.py --root . query delta --trace retrieval-lineage --checkpoint
uv run python .agents/skills/paper/scripts/research.py --root . query stop --trace retrieval-lineage --reason '预算已用完' --coverage '已覆盖两条接口路线，刷新实验仍缺' --missing-route '非检索系统的状态复用' --next-question '后续工作是否测完整候选总体？' --saturation 未饱和
```

上述命令是操作说明，不代表已经发起查询；`<...>` 必须换实际 ID。`screen` 输入为 `{"decisions":[{"record_id":"实际ID","status":"纳入","reason":"具体依据"}]}`；也可用 `report_id` 批量处理同一报告。工作/报告/独立研究的关联只有人工核实后提供 `work_id / report_id_override / study_id` 与 `identity_reason`，未知身份不自动猜。强 ID 精确去重，标题相似仅供模型核验。

外部列表可用 `query import --provider manual --input <实际JSON> --observed-at <实际取得ISO日期> --source <真实来源说明> --query <原查询>` 导入；文件形如 `{"records":[{"title":"...","doi":"...","version":null}],"reported_total":null}`。可附 `citation_edges:[{"source":"doi:...","target":"doi:...","location":"实际引用定位"}]`，原文与身份必须真实。导入保存原文件，不假装程序执行过网络 API。API 格式的原响应则选择其真实 provider；额外参数可用 `--parameters <相对JSON>` 保存。

网络只运行明确所选 provider：arXiv 用于元数据检索；Crossref 用于元数据及实际 DOI 后向引用；OpenAlex 用于实际前后向引用。Crossref 此适配器不能前向，arXiv API 不提供引用邻居，遇到限制明确报告，不自动启动其他来源。OpenAlex 后向候选只有实际引用身份时标题仍未知，需后续真实取得和阅读。密钥可用 `PAPER_OPENALEX_API_KEY` 环境变量，仅进 Bearer 请求头；接收联系信息 `PAPER_API_CONTACT` 可进 User-Agent，不打印密钥或写进参数。

默认查询输出当前新增/待判的有界分页及短身份/关键条件/持久引用，不回显所有 events；显式审计才读取历史集合。查询、页、候选、全文及扩展轮次预算按实际消耗累计，越界明确停止或请求扩范围；不能只写自由文本预算便称已执行限额。

请求页、实际参数、UTC 取得时间、原始字节/哈希、响应状态及可用分页信息持久保存在 `.research/queries/<trace>/`。限速依据 provider 间隔和实际响应，失败/429/部分取得保留证据并接续，不声称空结果；实际 `Retry-After` 未结束不再次请求。匿名可用性及限制依赖当前官方规则与实际响应。

事实图保留全部已取得引用边；SCC 凝聚图或注明纳入集合的投影另生成，不能为造 DAG 删除原始边。引用事实不自动成为承继、改进或独立支持。数量从实际列表生成，检索记录、报告、工作和已确认独立研究分开；版本和同一研究的多报告不能重复计支持。

增量从上次范围、标准、日期和集合继续，`delta --checkpoint` 固定基线后明确选择重跑查询或雪球，不自动联网。新增/修订报告先固定来源、核身份，再检查判断影响。零新增只适用于当前来源、种子和标准，不证明领域穷尽。

API 参考（当前规则仍以响应为准）：[arXiv API](https://info.arxiv.org/help/api/user-manual.html)、[Crossref 官方 API](https://github.com/CrossRef/rest-api-doc)、[OpenAlex 认证](https://help.openalex.org/api/authentication/)、[OpenAlex 引用操作](https://help.openalex.org/how-to/api-recipes/)。

## 持久边界

固定来源/全文哈希、正式 Markdown、定位依据、原始查询响应/实际列表/筛选历史、重要判断增量和提交记录是研究资产。派生索引/事实图可由这些资产重建。过期执行日志、重复解析和阶段冗余工作可以清理；完成任务时不能连检索集合和认知更新一起当缓存删除。
