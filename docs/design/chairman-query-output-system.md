# 董事长问数输出系统

日期：2026-07-28
对象：董事长 AI 秘书的经营问数回答链路

## 结论

当前体验一般的主要原因不是配色或卡片样式，而是没有“董事长回答契约”。后端只要求模型用自然语言写“结论、关键发现、建议”；前端再从任意 `structured_data` 中自动提取数字、选择首个可用数值字段画柱图。结果是所有问题共享同一种报告骨架，证据形态由数据结构偶然决定，无法稳定命中董事长的决策任务。

应把回答链路改为：

`问题 → QuerySpec → 受控工具 → 数据质量门 → 模板路由 → ChairmanAnswer Schema → 服务端证据校验 → 专用渲染`

首批固化五类模板：经营结论快报、目标差距与兑现路径、异常风险作战卡、Top 机会与客户清单、对比与决策备忘录。

## 现有链路的三个结构性问题

1. **模型只被要求“写得像高管报告”，没有被要求产出固定结构。** `data_answer` 只约束数据一致、先结论、再关键数字和建议，无法保证责任人、截止时间、比较基准、结论置信度和数据质量进入回答。
2. **前端在替模型猜展示意图。** `MessageDetails` 会从 `structured_data` 中找第一组数组，再挑一个数值字段生成通用柱图；它不知道本次问题是目标差距、风险处置还是资源取舍。
3. **来源被做成了末尾折叠项，数据质量没有参与结论。** 这对当前商机表尤其危险：138 条记录中 137 条缺预计签单时间，93 条阶段为归档，行业空白或“待补充”合计 71 条。

## 全球对标提炼

| 对标 | 可吸收的规则 | 不直接复制的部分 |
| --- | --- | --- |
| [Tableau Agent in Pulse](https://www.tableau.com/blog/tableau-pulse-enhanced-qa) | 结构化自然语言摘要、每次只显示最有力的可视化、逐项证据、可继续追问 | 通用指标产品的信息架构 |
| [ThoughtSpot Spotter](https://www.thoughtspot.com/blog/introducing-spotter-ai-analyst) | 回答可解释、可修正、可下钻，图表没有死胡同 | 搜索 token 与 SQL 交互方式 |
| [IBCS](https://www.ibcs.com/ibcs-/) | 先表达消息，再选择证据；统一语义；删除装饰和冗余；保证比例与尺度诚实 | 完整财务报告符号体系 |
| [NIAO 董事会材料指南](https://www.niauditoffice.gov.uk/publications/html-document/niao-board-effectiveness-good-practice-guide-2022) | 每次明确“供知悉/讨论/决策/批准”，摘要简短，写清风险、建议、选项和责任 | 会议公文形式 |
| [UK Government Data Quality Framework](https://www.gov.uk/government/publications/the-government-data-quality-framework/the-government-data-quality-framework) | 把完整性、一致性、时效性等质量维度与适用目的绑定，并显式说明取舍 | 政府治理流程 |
| [Microsoft Data Formulator](https://github.com/microsoft/data-formulator) | 问题、澄清、解释、表格和图表进入同一数据线程，可分支、可追溯 | 面向分析师的全量探索界面 |
| [Evidence](https://github.com/evidence-dev/evidence) | 叙事与数据组件在同一阅读流中，模板可复用 | SQL-in-Markdown 的开发方式 |
| [Bright Data output templates](https://github.com/brightdata/skills/blob/main/skills/competitive-intel/references/output-templates.md) | 按任务类型维护多个精确模板，固定日期、来源和行动建议 | 竞争情报的具体字段 |

## 五类模板

| ID | 模板 | 回答的董事长问题 | 主证据 |
| --- | --- | --- | --- |
| `executive_pulse` | 经营结论快报 | 现在是否偏离、是否需要介入 | 目标进度、离散期间柱形、异常排行 |
| `target_gap` | 目标差距与兑现路径 | 缺口多大、靠什么补、什么条件会失败 | 差距桥、目标进度、兑现清单 |
| `risk_action` | 异常风险作战卡 | 哪个风险最危险、暴露多少、谁关闭 | 风险排行、时间线、责任清单 |
| `top_opportunities` | Top 机会与客户清单 | 哪些对象值得亲自跟、集中度如何 | 排序表或排行 |
| `decision_memo` | 对比与决策备忘录 | 资源投给谁、选哪个方案、代价是什么 | 对比矩阵或差异条 |

所有模板共用 `data_quality` 区块。它不是免责声明；它负责决定回答能否给出方向、幅度或预测。

## 第一屏信息层级

1. 一句话判断：直接回答要不要介入。
2. 2–3 个数字：只保留证明判断的数字。
3. 一个主图或主表：不出现第二个竞争视觉焦点。
4. 最大风险/机会：最多三项，按决策影响排序。
5. 责任动作：谁、何时、做什么、以何指标复核。
6. 数据质量：截止时间、范围、就绪度和关键缺口常显；源记录明细折叠。

## 视觉系统

- 单列文档式阅读流，保持现有工作台聊天形态，不新增独立问数大屏。
- 暖白/暖灰背景、深炭文字、单一低饱和强调色；风险不用大面积红色。
- 只在表达层级时使用容器；不用多层嵌套卡片、渐变、玻璃、彩色图例和装饰图标。
- 数字使用 tabular figures；主要判断 18–22px，指标 20–28px，正文 14–15px。
- 每次最多一个主图；横向条形处理长标签，表格用于精确查阅，差距桥只用于可加总驱动。
- 状态色必须有文字或符号冗余，不能只靠颜色。

## 接入顺序

### P0：先改回答契约

1. 在 Hermes `data` profile 中要求只输出 `ChairmanAnswer` JSON。
2. 服务端按 JSON Schema 校验；校验失败只允许一次受控修复。
3. 模板路由由 QuerySpec 意图、可用工具和数据就绪度共同决定。
4. 关键事实数字必须逐项绑定已有证据引用。

### P1：再改渲染器

1. 删除“从任意结构化数组自动选图”的生产路径。
2. 新增五个轻量模板渲染器，共用指标、主证据、责任动作和数据质量原语。
3. 数据截至、范围和就绪度常显；完整来源和源记录进入展开层。

### P2：最后做评测闭环

1. 将董事长高频问题整理成至少 20 条回归题。
2. 自动断言模板选择、数字可追溯、首句方向、图表数量、动作完整性和拒绝伪预测。
3. 人工做五秒测试：只看第一屏能否复述结论、介入点、责任人和下一步。

## 已固化资源

- Skill：`skills/chairman-query-output/SKILL.md`
- 模板详解：`skills/chairman-query-output/references/templates.md`
- 评测标准：`skills/chairman-query-output/references/evaluation.md`
- JSON Schema：`skills/chairman-query-output/assets/chairman-answer.schema.json`
- 路由注册表：`skills/chairman-query-output/assets/template-registry.json`

## 当前边界

本轮固化的是回答系统与可执行契约，不直接替换当前生产渲染器。现有工作树同时有其他未完成改动；应在独立实现轮次中按 P0 → P1 → P2 接入，避免与管理端和 MCP 工具改动交叉。
