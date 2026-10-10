# 宿主事件：按需填写的 runtime 契约

主会话登记实际宿主能力、owner 预留/绑定/结束助手时才加载。本页只解释事件字段；委派收益、科学责任与短交接沿用 [owner 契约](unit-owner.md#有收益的跨模型助手)。脚本记录事实与容量，**预留不会 spawn，结束记录不会停止或关闭线程**。

## 命令与公共字段

```sh
uv run python .agents/skills/paper/scripts/research.py --root . runtime configure --run <run> --event <库根相对能力.json>
uv run python .agents/skills/paper/scripts/research.py --root . runtime reserve --run <run> --unit <unit> --agent-id <父ownerID> --event <库根相对预留.json>
```

`bind/complete/cancel` 与 reserve 使用相同命令参数；`status` 只需 `--run`。`--event` 是实际 JSON **文件路径**，不是内联 JSON。除 cancel 外，事件都必须有 `observed:true` 与非空 `actual_record`（真实工具回执或运行元数据定位）；不能用请求配置代替实际观察。CLI 的 `--agent-id` 始终是管理该助手的真实父 owner，事件内 `agent_id` 是助手本人。

## configure：主会话登记能力

| 字段 | 填写依据 |
|---|---|
| `observed`、`actual_record` | 公共真实观察字段 |
| `root_agent_id` | 当前根代理真实 ID |
| `active_limit_including_root` | 实际宿主全树活跃正整数上限，含根；当前接口为 4，后续重新核对 |
| `spawn_model`、`spawn_effort`、`fork_none`、`nested_delegation` | 四个实际能力布尔值，必须显式提供 |
| `available_models` | 非空实际可请求模型名数组，按当前工具能力填写 |
| `open_subagent_limit` | 可观测的打开子线程正整数上限，不含根；未暴露填 `null` |
| `close_thread` | 可选实际关闭能力布尔值；没有接口不能声称能关闭 |

工具未支持独立跨模型嵌套委派时如实填 false，由 owner 完成；不能为通过预留编造 true。能力配置不切换任何已运行实例。

全部科学 checkpoint 已接受且尚未交 final report 的窗口仍允许本人核证；真实物理中断须恢复，交付后不能以此窗口再委派。未完成助手须先真实结束并登记，最终 report 不能留下运行/预留的助手。

## reserve：owner 预留小任务

下面字段全部来自当前问题、固定资料、路由及**本次真实全树 snapshot**；尖括号须替换，数值按实际预算填写。

```json
{
  "observed": true,
  "actual_record": "<本次宿主 live tree 回执定位>",
  "observed_at": "<带时区的本次观察时间>",
  "active_agent_ids": ["<根ID>", "<所有活跃owner/helper ID>"],
  "open_agent_ids": ["<所有仍打开的子线程ID>"],
  "helper": "<本unit唯一helper标识>",
  "profile": "<routing.yaml中的实际可用profile>",
  "question": "<唯一问题>",
  "consumer": "<结果怎样用于当前owner判断>",
  "acceptance": "<可核对的接受条件>",
  "benefit_reason": "<整树成本与质量收益为何成立>",
  "source_refs": [{"path": "<库根相对固定来源或接受版本>", "locator": "<具体页/节/块/窗口>"}],
  "token_budget": 1200
}
```

`helper` 使用安全短标识；`profile` 决定请求 model/effort，须在当前可用模型表中。`token_budget` 是正整数的**助手全流程先验预算**，含启动、输入/输出、owner 接收/整合/复核及重试，不只是助手输出；示例 1200 不是默认配额或实际 usage。

snapshot 包含整个活跃树，不能只报当前 unit 或把未知填空数组。`observed_at` 须带时区，脚本接受距当前不超过 90 秒的观察；过期就重新取得实际树。打开上限已登记时必须给全树 `open_agent_ids`；上限未暴露时可填 `null`，不能声称打开容量已核验。`source_refs.path` 必须已绑定本 unit 固定 inputs 或已接受科学版本，当前字节哈希仍一致；脚本补固定 SHA，owner 不能引用任意未登记草稿。先 append 输入或取得接受版本，再预留。

成功返回 `mode/model/reasoning_effort/fork_turns/result_path`。mode=spawn 才实际创建独立 `fork_turns:none` 助手；mode=followup 只续派返回的已完成打开实例，不能热切模型。预留失败不创建；创建失败且未绑定时 cancel。

## bind：登记真实创建或续派

```json
{
  "observed": true,
  "actual_record": "<真实spawn/followup回执定位>",
  "helper": "<预留helper>",
  "agent_id": "<真实助手ID>",
  "parent_agent_id": "<CLI所给父ownerID>",
  "mode": "spawn",
  "fork_turns": "none",
  "call_id": null,
  "actual_model": null,
  "actual_effort": null
}
```

mode 必须与预留一致；新 spawn 必须 `fork_turns:none`，followup 必须是预留返回的同一 ID。`call_id` 有真实值才填；**actual model/effort 未暴露填 null**，不把请求档位抄成已核事实。已知实际档位与请求不符会记录 mismatch；缺测时 route_verified 不成立，owner 据此核质量/预算或降级。

## 助手结果与 complete

助手只将最终 JSON 写到预留返回的 `result_path`；最小结果如下，说明文字须替换为真实内容：

```json
{
  "answer": "<短回答及必要数值/配置/关系>",
  "limitations": "<条件、不确定、冲突或已核范围；无额外限制也具体说明范围>",
  "evidence": [{"path": "<任务给定固定来源path>", "locator": "<回答依据的具体位置>"}]
}
```

三字段均必填；answer/limitations 是非空文本，evidence 是非空定位数组，只能引用任务 source_refs 的固定路径。解释须满足问题的接受条件，JSON 合法不等于科学成立。owner 定向核证后整合，助手短结果不逐层转发主会话；详尽局部结果可留本人独占区。

```json
{
  "observed": true,
  "actual_record": "<真实完成/失败/中断回执定位>",
  "helper": "<已绑定helper>",
  "agent_id": "<同一真实助手ID>",
  "host_status": "completed",
  "thread_status": "open",
  "close_requested": false
}
```

`host_status` 仅为实际 `completed/interrupted/failed`；完成才核固定来源及接受结果版本，失败/中断不能称科学完成。`thread_status` 为实际 `open/closed/unknown`，不因代理已结束就填 closed；关闭未发生或不可观测须保持真实状态。`close_requested:true` 仅在配置确有关闭能力且实际请求后使用，仍须回执确认是否 closed。已经绑定的运行助手必须真实停止/结束后 complete，不能 cancel 绕过生命周期。

cancel 仅释放**尚未绑定**的预留：`{"helper":"<预留helper>","reason":"<实际取消或创建失败原因>"}`。已完成且仍打开的助手仅在同 owner、同 model/effort、可复用问题且有收益时再次 reserve；占用活跃容量时不能同时续派。
