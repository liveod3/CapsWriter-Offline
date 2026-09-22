# 查询 LLM 费用

客户端按月保存每次 LLM 请求的费用记录。默认目录是项目根目录下的 `llm-costs/`，每月一个 `YYYY-MM.sqlite3` 数据库；其中 `requests` 表每次请求一行。一次请求只占一行，开始前建立记录，结束后更新。默认开启费用记录；LLM 总开关仍默认关闭。请求处理只写入本条记录，不汇总整月费用，也不显示费用摘要或预算提醒。需要统计时运行查询脚本。

记录包括请求 ID、本地时区起止时间、配置中的提供方和模型、预设、HTTP 状态、耗时、成功/失败/取消状态、token 用量、费用来源和请求时的费率快照。不会保存提示词、转写、光标参考、响应正文、密钥或完整请求地址。费用记录与录音、转写、LLM 内容档案分别控制。

## 查询本月消费

在项目根目录使用 `capswriter` 环境运行：

```powershell
conda run -n capswriter python scripts/llm_costs.py
```

指定月份，或导出包含逐次明细的 JSON：

```powershell
conda run -n capswriter python scripts/llm_costs.py --month 2026-09
conda run -n capswriter python scripts/llm_costs.py --month 2026-09 --details > llm-costs/2026-09-details.json
```

脚本只读取本地账本，不调用提供方，不启动麦克风或界面。默认读取 `LLM/costs.toml`，不存在时读取公开模板。自定义 LLM 配置目录使用 `--config-dir`；也可以使用 `--directory` 直接指定账本目录。`--language en` 或 `--language zh-CN` 可指定显示语言；`--json` 输出汇总 JSON，`--details` 输出汇总及全部明细 JSON。

统计按币种分别展示提供方返回金额、按费率估算、按估算 token 计费、未完成请求的可能费用，以及无法确定费用的请求数。JSON 还包含按提供方/模型分组的金额。不同币种不换算、不相加。

## 理解费用与未完成记录

| 来源 | 含义 |
| --- | --- |
| `provider_reported` | 以提供方返回的有效总金额为准，包括明确返回的零金额；不与估算重复相加 |
| `rate_estimate` | 有提供方 token 用量，按请求时的配置费率计算；仍是估算 |
| `token_estimate` | 完成请求但缺少完整用量，使用文本 UTF-8 字节数约除以 3、加消息结构开销估算；不能准确预测隐藏推理用量 |
| `possible_cost` | 失败、取消或尚无结束记录；缺失输入用量时使用输入估算，缺失输出用量时按请求的 `max_tokens` 计算预算情景。不是已确认支出，也不是严格的费用上限 |
| `unknown` | 无匹配费率、费率过期、缺少缓存价格等，不能得出金额；不是零费用 |
| `not_sent` | 已确认未发送，例如本机缺少密钥；没有本次 API 费用 |

`status` 与费用来源分别保存。失败响应即使返回有效金额或用量，仍保持 `failed`；取消保持 `cancelled`。`unfinished` 表示没有完成记录：可能仍在运行，也可能程序退出或最后写入失败，不能认作成功或零消费。取消和超时不保证提供方停止生成。

“参考合计”包含已知金额、估算及未完成请求的可能费用，不含无法估价的请求。它供手动查询参考，不是提供方账单。仅统计当前目录中的本客户端记录，不包含其他应用、关闭记录期间的请求、税费、充值费、缓存存储或本机电费。最终结算以提供方账单为准。

月份按请求开始时的本地时区确定。跨月完成仍计入开始月份；改价不会重新计算旧记录。程序不自动删除账本。目录不可写时会告警，文本处理继续，统计可能不完整；不会为了补账重试 LLM 请求。

## 设置官方费率

首次定制时复制模板；已有本机文件不要覆盖：

```powershell
if (!(Test-Path LLM/costs.toml)) { Copy-Item LLM/costs.template.toml LLM/costs.toml }
```

每个 `[[rates]]` 项按**完整基础地址和模型名精确匹配**，不按提供方名称猜测价格。`kind = "openai"` 只是协议类型。代理、自定义地址和 Ollama 都不会自动套用同名云模型费率或当作免费。

公开模板于 2026-09-22 核实了 [Google 官方定价](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite)：`gemini-3.5-flash-lite` 标准付费文本请求每百万输入 token 为 USD 0.30，输出（含推理）为 USD 2.50，缓存读取为 USD 0.03。模板不推断账号的免费额度。若明确使用免费层，可复制该条目后将费率改为零，并在 `assumption` 写明依据；同一地址/模型只能配置一个条目。

必填字段为 `endpoint`、`model`、`currency`、`input`、`output`、`source`、`updated`、`assumption`。费率均为每百万 token 的币种金额，建议使用带引号的十进制字符串。`source` 填官方文档链接，`updated` 填核实日期，`assumption` 说明套餐、免费层、服务等级等适用条件，不要放密钥或私人内容。

可选字段：

- `cached_input`：缓存读取费率。用量报告了缓存命中但未配置该费率时，费用保持未知。
- `cache_write`：缓存写入费率；支持兼容协议 `prompt_tokens_details.cache_write_tokens`。输入、缓存读取、缓存写入互不重复计费。
- `reasoning`：推理 token 单独费率；省略时按输出费率。推理 token 是输出总量的子集，不再额外叠加一次。
- `valid_until = "YYYY-MM-DD"`：此日期后停用估算费率，等待核实更新；提供方返回金额仍可采用。
- `reported_cost_field = "usage.cost"`：自定义提供方明确记载的响应总金额字段路径，币种使用该条目的 `currency`。不要配置 token 字段、上游成本或余额。

[OpenRouter 官方用量文档](https://openrouter.ai/docs/cookbook/administration/usage-accounting)定义 `usage.cost` 为账号扣款总额。对其官方地址 `https://openrouter.ai/api/v1`，程序自动优先采用该字段，并按其[美元积分规则](https://openrouter.ai/support/)记录为 USD；不叠加 `upstream_inference_cost`。其他地址的金额字段必须显式配置，避免把不同单位或成本字段误当作账单。

原生 Ollama 使用 `prompt_eval_count` 和 `eval_count`；Gemini 原生用量如存在则合并候选与思考 token。兼容协议优先使用标准输入/输出及明细字段；矛盾、负数或非法用量不能作为完整可信用量计费。程序不会额外联网查询价格或账单，费率变动需手动核实官方文档并更新。

## 设置或关闭费用记录

以下为 `LLM/costs.toml` 中的设置示例，费率条目继续保留：

```toml
[tracking]
enabled = true
directory = "llm-costs"
```

费用摘要、预算提醒和对应配置已移除。旧配置中的 `show_summary`、`alerts_enabled` 和 `[budgets]` 会被忽略，可自行删除；其他本机设置和历史记录保持原样。已有数据库中的旧 `alerts` 表不再使用，也不会被删除；新数据库只建立请求记录表。

`enabled = false` 关闭费用记录。也可以在 `ClientConfig` 中设置 `llm_cost_tracking = False`，与其他内容保存开关无关。关闭不会删除旧账本，查询工具仍可读取。

费用 TOML 在每次请求前重读。无效或编辑到一半的配置会告警，并沿用本进程最后有效配置；没有有效配置时仍记录请求，但金额可能未知。每次请求使用开始时的费率快照。修改目录后旧记录留在原目录，可用 `--directory` 查询旧账本。
