export type ExecutiveView =
  | "home"
  | "chat"
  | "history"
  | "memory"
  | "daily"
  | "weekly"
  | "capabilities"
  | "account";

export type AdminSection =
  | "overview"
  | "account"
  | "model"
  | "source"
  | "automation"
  | "feishu"
  | "capability"
  | "runtime";

export type RouteKind = "data" | "file" | "general" | "research" | "failure";
export type AnswerLayout = "overview" | "trend" | "comparison" | "diagnosis";
export type Tone = "positive" | "attention" | "risk" | "neutral";

export type MetricItem = {
  label: string;
  value: string;
  note: string;
  tone: Tone;
};

export type ChartDatum = {
  label: string;
  value: number;
  display: string;
};

export type AnswerSection = {
  title: string;
  body: string;
  tone?: Tone;
};

export type TableColumn = {
  key: string;
  label: string;
};

export type AnswerConfig = {
  id: string;
  layout: AnswerLayout;
  label: string;
  title: string;
  summary: string;
  asOf: string;
  metrics: MetricItem[];
  sections: AnswerSection[];
  chart?: {
    title: string;
    unit: string;
    kind: "line" | "bars" | "progress" | "stacked";
    data: ChartDatum[];
  };
  columns?: TableColumn[];
  rows?: Array<Record<string, string>>;
  evidence: string[];
  actions: string[];
  sources: string[];
  scope: string;
  definition: string;
  followups: string[];
};

export type ConversationItem = {
  id: number;
  title: string;
  preview: string;
  question?: string;
  route?: RouteKind;
  answerId?: string;
  time: string;
  group: "今天" | "昨天" | "更早";
  type: "数据" | "文件" | "泛化" | "每日摘要" | "每周简报";
  searchable: string;
};

export type MemoryItem = {
  id: number;
  content: string;
  category: string;
  createdAt: string;
  usedAt: string;
  source: string;
};

export const executiveNavigation: Array<{
  id: "home" | "chat" | "history" | "memory";
  label: string;
  short: string;
}> = [
  { id: "home", label: "首页", short: "首页" },
  { id: "chat", label: "会话", short: "会话" },
  { id: "history", label: "历史", short: "历史" },
  { id: "memory", label: "记忆", short: "记忆" },
];

export const adminNavigation: Array<{
  id: Exclude<AdminSection, "account">;
  label: string;
  short: string;
}> = [
  { id: "overview", label: "总览", short: "总览" },
  { id: "model", label: "模型", short: "模型" },
  { id: "source", label: "数据源", short: "数据" },
  { id: "automation", label: "自动任务", short: "任务" },
  { id: "feishu", label: "飞书", short: "飞书" },
  { id: "capability", label: "能力白名单", short: "能力" },
  { id: "runtime", label: "运行状态", short: "状态" },
];

export const homeSuggestions = [
  "两个延期项目分别卡在哪个里程碑？",
  "本月回款差距主要来自哪些客户？",
  "把今日结论整理成三分钟经营会汇报。",
];

export const dailyChanges = [
  {
    state: "需关注",
    tone: "risk" as Tone,
    title: "回款进度落后计划 8.6 个百分点",
    detail: "三家客户贡献主要缺口，其中一笔 420 万元应收已逾期。",
  },
  {
    state: "有变化",
    tone: "attention" as Tone,
    title: "两个重点项目进入延期关注",
    detail: "偏差集中在客户验收确认和交付资源排期。",
  },
  {
    state: "改善",
    tone: "positive" as Tone,
    title: "加权商机金额较上月同期提升 5.1%",
    detail: "华东新增两笔高概率商机，预计签约时间均在本季度。",
  },
];

export const initialConversations: ConversationItem[] = [
  {
    id: 1,
    title: "本月整体经营情况",
    preview: "回款进度需要优先关注，项目交付整体可控。",
    time: "今天 08:42",
    group: "今天",
    type: "数据",
    searchable: "整体经营 全部事业部 目标 收入 回款",
  },
  {
    id: 2,
    title: "每日经营变化｜2026-07-26",
    preview: "回款计划差距扩大，两个项目进入延期关注。",
    time: "今天 05:03",
    group: "今天",
    type: "每日摘要",
    searchable: "每日变化 回款 项目 商机",
  },
  {
    id: 3,
    title: "重点项目延期原因",
    preview: "两个项目的偏差集中在客户确认和资源排期。",
    time: "昨天 17:16",
    group: "昨天",
    type: "数据",
    searchable: "项目 交付 延期 云海 智造 城商",
  },
  {
    id: 4,
    title: "项目复盘报告要点",
    preview: "从项目复盘 PDF 中提取三个主要问题。",
    time: "昨天 14:28",
    group: "昨天",
    type: "文件",
    searchable: "项目复盘 文件 PDF 三个问题",
  },
  {
    id: 5,
    title: "每周高层经营简报｜第30周",
    preview: "签约质量改善，但回款与交付节奏仍需校准。",
    time: "7月20日 06:02",
    group: "更早",
    type: "每周简报",
    searchable: "每周简报 第30周 签约 回款 交付",
  },
  {
    id: 6,
    title: "行业近三个月变化",
    preview: "整理公开事实，并分开标注对企业的分析判断。",
    time: "7月18日 11:35",
    group: "更早",
    type: "泛化",
    searchable: "行业 公开研究 竞争对手",
  },
  {
    id: 7,
    title: "本月回款差距",
    preview: "梳理主要客户回款差距与最新承诺节点。",
    time: "7月17日 09:24",
    group: "更早",
    type: "数据",
    searchable: "回款 差距 客户 逾期 现金流",
  },
  {
    id: 8,
    title: "逾期应收处置建议",
    preview: "整理逾期应收的处置顺序、责任人与确认时间。",
    time: "7月16日 16:08",
    group: "更早",
    type: "数据",
    searchable: "逾期 应收 处置 回款 责任节点",
  },
  {
    id: 9,
    title: "两个延期项目卡在哪里",
    preview: "定位客户确认与交付资源排期的具体偏差。",
    time: "7月15日 10:36",
    group: "更早",
    type: "数据",
    searchable: "项目 延期 里程碑 客户确认 资源排期",
  },
  {
    id: 10,
    title: "重点项目复盘",
    preview: "汇总重点项目延期原因和下一步动作。",
    time: "7月14日 18:12",
    group: "更早",
    type: "数据",
    searchable: "重点项目 复盘 延期 动作",
  },
];

export const initialMemories: MemoryItem[] = [
  {
    id: 1,
    content: "经营问题默认查看全部事业部。",
    category: "默认范围",
    createdAt: "2026-06-18",
    usedAt: "今天 08:42",
    source: "本月经营复盘",
  },
  {
    id: 2,
    content: "金额统一使用万元，保留一位小数。",
    category: "数字偏好",
    createdAt: "2026-06-22",
    usedAt: "今天 08:42",
    source: "经营口径确认",
  },
  {
    id: 3,
    content: "回答先给结论，再给依据和行动。",
    category: "表达偏好",
    createdAt: "2026-06-22",
    usedAt: "昨天 17:16",
    source: "经营口径确认",
  },
  {
    id: 4,
    content: "优先关注回款，其次关注开票进度。",
    category: "长期关注",
    createdAt: "2026-07-03",
    usedAt: "昨天 17:16",
    source: "财务专项分析",
  },
];

const commonSources = [
  "飞书商机主表（真实连接状态演示）",
  "项目交付标准表（模拟数据 V2026.07）",
  "经营财务与回款表（模拟数据 V2026.07）",
];

export const answerConfigs: Record<string, AnswerConfig> = {
  overview: {
    id: "overview",
    layout: "overview",
    label: "经营总览",
    title: "本月整体接近计划，回款和两个重点项目需要优先关注",
    summary:
      "收入与商机质量保持改善，回款完成率低于计划，两个重点项目的里程碑偏差可能影响本月收入确认。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "收入目标完成", value: "82.4%", note: "较上月同期 +5.1 个百分点", tone: "positive" },
      { label: "已确认收入", value: "6,280万", note: "距本月目标 1,340 万", tone: "neutral" },
      { label: "回款完成", value: "68.7%", note: "低于计划 8.6 个百分点", tone: "risk" },
      { label: "延期关注项目", value: "2个", note: "预计影响本月收入确认", tone: "attention" },
    ],
    sections: [
      { title: "目标完成", body: "收入完成 82.4%，按当前节奏接近计划，但仍需补足 1,340 万元。" },
      { title: "商机与签约", body: "加权商机 9,560 万元，较上月同期提升 5.1%，华东新增两笔高概率商机。", tone: "positive" },
      { title: "项目交付", body: "17 个在途项目中，2 个进入延期关注，其余里程碑处于计划区间。", tone: "attention" },
      { title: "收入与毛利", body: "已确认收入 6,280 万元，综合毛利率 31.8%，未见异常波动。" },
      { title: "回款", body: "回款完成率 68.7%，三家客户形成主要差距，其中一笔逾期 12 天。", tone: "risk" },
    ],
    chart: {
      title: "近八周收入目标完成走势",
      unit: "%",
      kind: "line",
      data: [
        { label: "6/8", value: 48, display: "48%" },
        { label: "6/15", value: 56, display: "56%" },
        { label: "6/22", value: 52, display: "52%" },
        { label: "6/29", value: 63, display: "63%" },
        { label: "7/6", value: 68, display: "68%" },
        { label: "7/13", value: 74, display: "74%" },
        { label: "7/20", value: 71, display: "71%" },
        { label: "7/25", value: 82.4, display: "82.4%" },
      ],
    },
    evidence: [
      "回款缺口的 71% 来自云海智造、澄川零售和北陆能源三家客户。",
      "云海智造升级项目等待客户确认验收窗口，当前计划偏差 9 天。",
      "华东新增 2 笔高概率商机，合计加权金额 1,180 万元。",
    ],
    actions: [
      "本周确认三家主要欠款客户的回收节点与责任人。",
      "要求两个延期关注项目同步客户确认记录和资源排期。",
      "保持华东高概率商机的关键人会谈节奏。",
    ],
    sources: commonSources,
    scope: "2026-07-01 至 2026-07-25，全部事业部",
    definition: "收入目标完成率 = 已确认收入 ÷ 本月收入目标；商机采用已配置概率的加权金额。",
    followups: homeSuggestions,
  },
  target: {
    id: "target",
    layout: "trend",
    label: "目标与差距",
    title: "本月收入已完成 82.4%，剩余 1,340 万元需要在 6 天内确认",
    summary: "完成率较上月同期高 5.1 个百分点。若两个待验收项目按计划确认，预计可覆盖约 63% 的剩余差距。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "本月目标", value: "7,620万", note: "目标表已确认", tone: "neutral" },
      { label: "实际收入", value: "6,280万", note: "截至 7月25日", tone: "positive" },
      { label: "完成率", value: "82.4%", note: "上月同期 77.3%", tone: "positive" },
      { label: "剩余差距", value: "1,340万", note: "日均需确认 223.3 万", tone: "attention" },
    ],
    sections: [
      { title: "可确认部分", body: "两个待验收项目对应收入 842 万元，前提是客户在本月完成验收确认。" },
      { title: "待补足部分", body: "其余 498 万元尚未形成明确确认节点，需要从已交付未验收项目中逐项核对。", tone: "attention" },
    ],
    chart: {
      title: "目标与实际",
      unit: "万元",
      kind: "progress",
      data: [
        { label: "实际", value: 6280, display: "6,280" },
        { label: "目标", value: 7620, display: "7,620" },
      ],
    },
    evidence: ["7月已确认收入明细共 28 条。", "两个待验收项目已完成交付，但尚未取得客户验收记录。"],
    actions: ["在 7月28日前确认两个项目的验收窗口。", "逐项核对 498 万元未明确确认节点的交付记录。"],
    sources: [commonSources[1], commonSources[2], "2026年7月经营目标表（模拟数据）"],
    scope: "2026-07-01 至 2026-07-25，全部事业部",
    definition: "实际收入仅包含已经确认的收入记录，不将预测商机计入。",
    followups: ["剩余差距由哪些项目构成？", "与上月同期相比，收入结构有什么变化？", "整理一份本周收入确认行动清单。"],
  },
  change: {
    id: "change",
    layout: "diagnosis",
    label: "变化诊断",
    title: "本周加权商机金额下降 7.8%，主要由一笔延后和两笔概率下调造成",
    summary: "下降可由商机记录直接解释 86%，剩余变化来自新旧记录时间差，不能据此判断市场需求整体走弱。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "本周加权商机", value: "9,560万", note: "较上周 -7.8%", tone: "risk" },
      { label: "新增贡献", value: "+1,180万", note: "2 笔高概率商机", tone: "positive" },
      { label: "减少影响", value: "-1,987万", note: "4 笔主要变化", tone: "risk" },
      { label: "可直接解释", value: "86%", note: "其余为记录时点差", tone: "neutral" },
    ],
    sections: [
      { title: "主要减少项", body: "北陆能源二期预计签约日延后至 8月，减少本周加权金额 920 万元。", tone: "risk" },
      { title: "概率调整", body: "两笔商机因客户预算确认未完成，概率由 70% 调整为 45%，合计影响 647 万元。", tone: "attention" },
      { title: "主要增加项", body: "华东新增两笔商机，按现有概率贡献 1,180 万元。", tone: "positive" },
      { title: "可能原因", body: "预算审批节奏可能影响签约日期，该判断需要业务负责人确认。", tone: "attention" },
    ],
    chart: {
      title: "主要变化项对加权金额的影响",
      unit: "万元",
      kind: "bars",
      data: [
        { label: "新增", value: 1180, display: "+1,180" },
        { label: "日期延后", value: 920, display: "-920" },
        { label: "概率下调", value: 647, display: "-647" },
        { label: "其他", value: 420, display: "-420" },
      ],
    },
    evidence: ["商机变更记录显示 1 笔预计签约日延后。", "2 笔商机概率由 70% 下调至 45%。", "新增 2 笔商机已完成负责人和客户关联。"],
    actions: ["请北陆能源负责人确认新签约节点是否稳定。", "补充两笔预算待确认商机的客户反馈记录。"],
    sources: [commonSources[0], "商机每日变化快照（演示数据）"],
    scope: "2026-07-19 至 2026-07-25，全部事业部",
    definition: "加权商机金额使用业务记录中的当前概率，系统不自行修改概率。",
    followups: ["哪几笔商机的预计签约日发生变化？", "华东新增商机的负责人是谁？", "整理一份需要负责人确认的商机清单。"],
  },
  forecast: {
    id: "forecast",
    layout: "trend",
    label: "签约预测",
    title: "本季度预计签约 7,200 至 8,060 万元，区间仍受三笔关键商机影响",
    summary: "已赢单与高概率商机形成预测下限，中概率商机按当前记录概率形成上限。该结果不是承诺值。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "已赢单", value: "2,860万", note: "已确认 9 笔", tone: "positive" },
      { label: "进行中加权", value: "5,190万", note: "共 31 笔", tone: "neutral" },
      { label: "预测下限", value: "7,200万", note: "已赢单 + 高概率", tone: "neutral" },
      { label: "预测上限", value: "8,060万", note: "包含中概率贡献", tone: "attention" },
    ],
    sections: [
      { title: "关键贡献", body: "北陆能源二期、澄川门店升级和启岳数据平台三笔商机贡献预测区间的 38%。" },
      { title: "关键假设", body: "沿用业务系统记录的阶段概率与预计签约日，不对概率做模型调整。", tone: "attention" },
    ],
    chart: {
      title: "季度签约构成",
      unit: "万元",
      kind: "stacked",
      data: [
        { label: "已赢单", value: 2860, display: "2,860" },
        { label: "高概率", value: 4340, display: "4,340" },
        { label: "中概率", value: 860, display: "860" },
      ],
    },
    evidence: ["9 笔赢单已形成有效签约记录。", "高概率商机预计签约日均处于本季度。", "3 笔关键商机尚缺最终商务确认。"],
    actions: ["每周复核三笔关键商机的预计签约日。", "要求负责人补齐最终商务确认记录。"],
    sources: [commonSources[0]],
    scope: "2026年第三季度，全部事业部",
    definition: "预测区间基于已赢单和当前概率加权商机，模型不修改业务概率。",
    followups: ["三笔关键商机分别卡在哪里？", "按事业部拆开看预测区间。", "生成本季度签约推进清单。"],
  },
  customers: {
    id: "customers",
    layout: "comparison",
    label: "重点客户",
    title: "五家重点客户中，云海智造与北陆能源需要同时关注交付和回款",
    summary: "澄川零售商机推进较好，云海智造存在验收与逾期回款叠加问题，北陆能源签约日期已延后。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "重点客户", value: "5家", note: "按本月经营影响筛选", tone: "neutral" },
      { label: "在途项目", value: "8个", note: "2 个需关注", tone: "attention" },
      { label: "未回金额", value: "1,286万", note: "其中逾期 420 万", tone: "risk" },
      { label: "加权商机", value: "4,780万", note: "占全部 50.0%", tone: "positive" },
    ],
    sections: [
      { title: "云海智造", body: "升级项目等待验收，未回 420 万元已逾期 12 天。", tone: "risk" },
      { title: "北陆能源", body: "二期商机预计签约日延后至 8月，现有项目交付正常。", tone: "attention" },
      { title: "澄川零售", body: "新增商机推进至方案确认，已有项目回款计划内。", tone: "positive" },
    ],
    columns: [
      { key: "customer", label: "客户" },
      { key: "opportunity", label: "加权商机" },
      { key: "delivery", label: "交付" },
      { key: "collection", label: "未回金额" },
      { key: "attention", label: "当前关注" },
    ],
    rows: [
      { customer: "云海智造", opportunity: "860万", delivery: "偏差 9 天", collection: "420万", attention: "验收与逾期" },
      { customer: "北陆能源", opportunity: "1,540万", delivery: "正常", collection: "286万", attention: "签约日延后" },
      { customer: "澄川零售", opportunity: "1,120万", delivery: "正常", collection: "180万", attention: "保持推进" },
      { customer: "启岳科技", opportunity: "740万", delivery: "正常", collection: "240万", attention: "回款节点" },
      { customer: "安浦医疗", opportunity: "520万", delivery: "正常", collection: "160万", attention: "无特别事项" },
    ],
    evidence: ["客户汇总来自商机、项目和回款的标准客户关联。", "云海智造一笔应收超过计划回款日 12 天。"],
    actions: ["由云海智造项目负责人与回款责任人共同确认本周节点。", "复核北陆能源二期的新签约日期。"],
    sources: commonSources,
    scope: "2026-07-01 至 2026-07-25，全部事业部，五家重点客户",
    definition: "重点客户按当月收入、商机、交付和回款综合影响筛选，仅用于演示。",
    followups: ["云海智造现在卡在哪一步？", "重点客户未回款按逾期天数排序。", "生成重点客户经营会提纲。"],
  },
  delivery: {
    id: "delivery",
    layout: "diagnosis",
    label: "交付与回款联动",
    title: "两个项目存在里程碑偏差，其中云海智造项目同时有 420 万元逾期未回",
    summary: "延期判断只基于计划进度、当前进度和已记录问题。未记录的原因不会作为正式风险结论。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "在途项目", value: "17个", note: "15 个处于计划区间", tone: "neutral" },
      { label: "延期关注", value: "2个", note: "平均偏差 7.5 天", tone: "risk" },
      { label: "关联未回", value: "706万", note: "逾期 420 万", tone: "risk" },
      { label: "本月待验收", value: "3个", note: "对应收入 1,126 万", tone: "attention" },
    ],
    sections: [
      { title: "云海智造升级项目", body: "当前完成 78%，计划 92%，等待客户确认验收窗口。负责人陈岚，预计完成 8月6日。", tone: "risk" },
      { title: "启岳数据平台", body: "当前完成 64%，计划 72%，资源排期已记录调整。负责人林序，预计完成 8月2日。", tone: "attention" },
    ],
    columns: [
      { key: "project", label: "项目 / 客户" },
      { key: "progress", label: "当前 / 计划" },
      { key: "milestone", label: "里程碑" },
      { key: "owner", label: "负责人" },
      { key: "receivable", label: "未回金额" },
      { key: "next", label: "下一步" },
    ],
    rows: [
      { project: "升级项目 / 云海智造", progress: "78% / 92%", milestone: "验收确认", owner: "陈岚", receivable: "420万，逾期12天", next: "确认验收窗口" },
      { project: "数据平台 / 启岳科技", progress: "64% / 72%", milestone: "联调测试", owner: "林序", receivable: "286万，计划内", next: "锁定测试资源" },
    ],
    evidence: ["项目计划和当前进度均来自 7月25日成功快照。", "问题原因只引用项目记录中已填写的内容。", "回款通过项目编号完成关联。"],
    actions: ["今天确认云海智造验收窗口与逾期回款节点。", "在 7月28日前锁定启岳联调测试资源。"],
    sources: [commonSources[1], commonSources[2]],
    scope: "全部事业部，在途项目，截至 2026-07-25",
    definition: "延期关注 = 当前进度低于计划进度且存在已记录的里程碑偏差，不等同于正式风险定级。",
    followups: ["云海智造最近一次客户反馈是什么？", "把两个项目按影响收入排序。", "生成项目推进会行动清单。"],
  },
  collection: {
    id: "collection",
    layout: "comparison",
    label: "回款与现金",
    title: "本月未回金额 2,374 万元，其中 3 笔共 706 万元已经逾期",
    summary: "逾期金额集中在云海智造、启岳科技和汇川供应链，回款责任与计划日期均有记录。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "本月应收", value: "4,186万", note: "计划内全部应收", tone: "neutral" },
      { label: "已回款", value: "2,874万", note: "完成 68.7%", tone: "positive" },
      { label: "未回金额", value: "2,374万", note: "含后续计划回款", tone: "attention" },
      { label: "逾期金额", value: "706万", note: "3 笔，最长 18 天", tone: "risk" },
    ],
    sections: [
      { title: "逾期集中度", body: "前三笔逾期占本月未回金额 29.7%，其中云海智造单笔 420 万元。", tone: "risk" },
      { title: "计划内未回", body: "其余未回款尚未超过计划日期，不应直接标记为风险。" },
    ],
    columns: [
      { key: "customer", label: "客户 / 项目" },
      { key: "due", label: "应收 / 未回" },
      { key: "date", label: "计划回款日" },
      { key: "overdue", label: "逾期" },
      { key: "owner", label: "责任人" },
    ],
    rows: [
      { customer: "云海智造 / 升级项目", due: "560万 / 420万", date: "7月13日", overdue: "12天", owner: "唐昱" },
      { customer: "启岳科技 / 数据平台", due: "286万 / 186万", date: "7月7日", overdue: "18天", owner: "顾宁" },
      { customer: "汇川供应链 / 协同平台", due: "160万 / 100万", date: "7月21日", overdue: "4天", owner: "周衡" },
      { customer: "北陆能源 / 运营一期", due: "480万 / 286万", date: "7月29日", overdue: "未逾期", owner: "沈澜" },
    ],
    evidence: ["应收金额、计划回款日和责任人来自回款标准表。", "未超过计划回款日的记录未计入逾期。"],
    actions: ["今天确认三笔逾期回款的最新承诺日期。", "在经营会上单独核对 18 天逾期记录。"],
    sources: [commonSources[2]],
    scope: "2026-07-01 至 2026-07-25，全部事业部",
    definition: "逾期天数按计划回款日到数据截止日计算；未回金额包含逾期与计划内未回。",
    followups: ["三笔逾期最近一次跟进是什么？", "按事业部比较回款完成率。", "生成本周回款责任清单。"],
  },
  organization: {
    id: "organization",
    layout: "comparison",
    label: "组织表现",
    title: "华东收入完成领先，华南回款差距最大，北区项目交付最稳定",
    summary: "组织比较同时考虑目标、商机、交付、收入和回款，不以单一指标给出人员排名。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "华东收入完成", value: "91.2%", note: "三组最高", tone: "positive" },
      { label: "华南回款完成", value: "59.6%", note: "低于计划 14.2 个百分点", tone: "risk" },
      { label: "北区按期项目", value: "94.1%", note: "三组最高", tone: "positive" },
      { label: "全部加权商机", value: "9,560万", note: "华东占 42.8%", tone: "neutral" },
    ],
    sections: [
      { title: "华东事业部", body: "收入和商机均领先，新增两笔高概率商机，回款略低于计划。", tone: "positive" },
      { title: "华南事业部", body: "收入完成 76.8%，回款完成 59.6%，主要差距来自两家客户。", tone: "risk" },
      { title: "北区事业部", body: "交付稳定，商机新增不足，需要核对下月储备。", tone: "attention" },
    ],
    chart: {
      title: "各事业部收入目标完成率",
      unit: "%",
      kind: "bars",
      data: [
        { label: "华东", value: 91.2, display: "91.2%" },
        { label: "华南", value: 76.8, display: "76.8%" },
        { label: "北区", value: 73.5, display: "73.5%" },
      ],
    },
    columns: [
      { key: "org", label: "组织" },
      { key: "target", label: "收入完成" },
      { key: "opportunity", label: "商机变化" },
      { key: "delivery", label: "交付" },
      { key: "collection", label: "回款完成" },
    ],
    rows: [
      { org: "华东事业部", target: "91.2%", opportunity: "+12.6%", delivery: "1 项关注", collection: "72.4%" },
      { org: "华南事业部", target: "76.8%", opportunity: "+2.1%", delivery: "1 项关注", collection: "59.6%" },
      { org: "北区事业部", target: "73.5%", opportunity: "-4.7%", delivery: "全部正常", collection: "75.8%" },
    ],
    evidence: ["组织汇总只使用已建立组织关联的记录。", "人员层级未在本轮展开。"],
    actions: ["华南优先核对两家客户的回款节点。", "北区补充下月商机储备与负责人计划。"],
    sources: commonSources,
    scope: "2026-07-01 至 2026-07-25，全部事业部",
    definition: "组织表现为多指标并列比较，不生成单一综合评分。",
    followups: ["只看华南的回款差距。", "华东新增商机由谁负责？", "整理三家事业部经营会要点。"],
  },
  people: {
    id: "people",
    layout: "comparison",
    label: "负责人比较",
    title: "唐昱负责的商机推进相对最慢，3 笔商机连续 14 天未发生阶段变化",
    summary: "比较范围为全部事业部的四名负责人，只使用商机阶段更新时间、预计签约日和已记录跟进，不以单一金额给人员做综合排名。",
    asOf: "2026-07-25 02:06",
    metrics: [
      { label: "唐昱停滞商机", value: "3笔", note: "最长 21 天未推进", tone: "risk" },
      { label: "陈岚阶段推进", value: "5笔", note: "四人中最多", tone: "positive" },
      { label: "林序按期更新", value: "88.6%", note: "高于团队均值", tone: "positive" },
      { label: "比较负责人", value: "4人", note: "全部事业部", tone: "neutral" },
    ],
    sections: [
      { title: "唐昱", body: "3 笔商机连续 14 天未发生阶段变化，其中云海智造扩容已超过计划跟进日。", tone: "risk" },
      { title: "顾宁", body: "2 笔商机处于停滞观察，最近一次客户反馈已经记录。", tone: "attention" },
      { title: "陈岚与林序", body: "阶段推进和记录更新均处于团队较好区间。", tone: "positive" },
    ],
    chart: {
      title: "连续 14 天未推进的商机数量",
      unit: "笔",
      kind: "bars",
      data: [
        { label: "唐昱", value: 3, display: "3笔" },
        { label: "顾宁", value: 2, display: "2笔" },
        { label: "林序", value: 1, display: "1笔" },
        { label: "陈岚", value: 0.2, display: "0笔" },
      ],
    },
    columns: [
      { key: "owner", label: "负责人" },
      { key: "active", label: "进行中商机" },
      { key: "stalled", label: "停滞 14 天以上" },
      { key: "latest", label: "最近更新" },
      { key: "attention", label: "需要确认" },
    ],
    rows: [
      { owner: "唐昱", active: "8笔", stalled: "3笔", latest: "7月22日", attention: "云海扩容跟进节点" },
      { owner: "顾宁", active: "7笔", stalled: "2笔", latest: "7月24日", attention: "两笔预算确认" },
      { owner: "林序", active: "6笔", stalled: "1笔", latest: "7月25日", attention: "一笔技术确认" },
      { owner: "陈岚", active: "10笔", stalled: "0笔", latest: "7月25日", attention: "无特别事项" },
    ],
    evidence: [
      "停滞天数只按商机阶段更新时间计算。",
      "唐昱名下 3 笔商机的最近阶段更新时间均早于 7月12日。",
      "没有将模型推断作为人员绩效结论。",
    ],
    actions: [
      "请唐昱确认三笔停滞商机的客户反馈与下一次动作日期。",
      "一周后复核阶段更新时间，不自动修改商机概率。",
    ],
    sources: [commonSources[0], "商机阶段变化快照（演示数据）"],
    scope: "2026-07-01 至 2026-07-25，全部事业部，四名负责人",
    definition: "推进最慢仅指连续 14 天未发生阶段变化的商机数量，不代表综合绩效排名。",
    followups: ["唐昱的三笔停滞商机分别是什么？", "只比较华东事业部的负责人。", "整理一份负责人确认清单。"],
  },
};

export const demoScenarios = [
  { id: 1, title: "打开首页", description: "摘要、建议与主输入" },
  { id: 2, title: "整体经营", description: "五段式经营总览" },
  { id: 3, title: "范围澄清", description: "最多两轮范围选择" },
  { id: 4, title: "连续追问", description: "继承时间与指标" },
  { id: 5, title: "项目与回款", description: "跨域关联明细" },
  { id: 6, title: "文件问答", description: "解析状态与位置引用" },
  { id: 7, title: "公开研究", description: "事实、判断与来源" },
  { id: 8, title: "数字失败", description: "不猜测并给出恢复动作" },
  { id: 9, title: "记忆控制", description: "确认后保存偏好" },
  { id: 10, title: "飞书提醒", description: "摘要深链接样例" },
];
