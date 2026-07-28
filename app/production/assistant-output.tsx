"use client";

import type {
  AssistantOutputEnvelope,
  ChairmanAnswer,
  ChairmanAnswerMetric,
  ExecutiveGeneralAnswer,
} from "./types";

const templateLabels: Record<ChairmanAnswer["template_id"], string> = {
  executive_pulse: "经营结论快报",
  target_gap: "目标差距与兑现路径",
  risk_action: "异常风险作战卡",
  top_opportunities: "Top 机会与客户清单",
  decision_memo: "对比与决策备忘录",
};

const modeLabels: Record<ExecutiveGeneralAnswer["mode"], string> = {
  direct_answer: "直接回答",
  analysis_memo: "分析备忘录",
  action_plan: "行动方案",
  writing_draft: "写作草稿",
};

function professionalSourceLabel(value: string) {
  return value
    .replaceAll("飞书经营三表", "飞书经营数据源")
    .replaceAll("飞书三表", "飞书经营数据源")
    .replaceAll("三表批次", "经营数据批次");
}

export function parseAssistantOutput(value: unknown): AssistantOutputEnvelope | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const envelope = value as Record<string, unknown>;
  if (envelope.schema_version !== "1.0") return null;
  if (!["data", "general", "clarification"].includes(String(envelope.kind))) return null;
  if (!envelope.body || typeof envelope.body !== "object" || Array.isArray(envelope.body)) return null;
  return envelope as AssistantOutputEnvelope;
}

function formatMetricValue(value: string | number, unit: string) {
  if (typeof value === "number") {
    return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${unit}`;
  }
  return `${value}${unit && !value.endsWith(unit) ? unit : ""}`;
}

function metricNumber(metric: ChairmanAnswerMetric) {
  if (typeof metric.value === "number") return Number.isFinite(metric.value) ? metric.value : null;
  const normalized = metric.value.replaceAll(",", "").match(/-?\d+(?:\.\d+)?/);
  return normalized ? Number(normalized[0]) : null;
}

function PrimaryEvidenceView({ answer }: { answer: ChairmanAnswer }) {
  const evidence = answer.primary_evidence;
  if (!evidence) return null;
  const numericMetrics = answer.metrics
    .map((metric) => ({ metric, value: metricNumber(metric) }))
    .filter((item): item is { metric: ChairmanAnswerMetric; value: number } => item.value !== null);
  const ceiling = Math.max(...numericMetrics.map((item) => Math.abs(item.value)), 1);
  const chartKinds = new Set(["progress", "bar", "ranked_bar", "waterfall", "timeline"]);

  return (
    <figure className={`chairman-primary-evidence evidence-${evidence.kind}`}>
      <figcaption><div><span>主证据</span><h3>{evidence.title}</h3></div><p>{evidence.reason}</p></figcaption>
      {chartKinds.has(evidence.kind) && numericMetrics.length > 0 ? (
        <div className="chairman-evidence-bars" role="img" aria-label={evidence.title}>
          {numericMetrics.slice(0, 3).map(({ metric, value }) => {
            const percentage = evidence.kind === "progress" && metric.unit.includes("%")
              ? Math.min(Math.max(Math.abs(value), 2), 100)
              : Math.max(4, Math.abs(value) / ceiling * 100);
            return <div key={`${evidence.dataset_ref}-${metric.label}`}><span>{metric.label}</span><i><b style={{ width: `${percentage}%` }} /></i><strong>{formatMetricValue(metric.value, metric.unit)}</strong></div>;
          })}
        </div>
      ) : (
        <div className="chairman-evidence-table" role="table" aria-label={evidence.title}>
          {answer.metrics.slice(0, 3).map((metric) => <div role="row" key={`${evidence.dataset_ref}-${metric.label}`}><span role="cell">{metric.label}</span><strong role="cell">{formatMetricValue(metric.value, metric.unit)}</strong><small role="cell">{metric.context}</small></div>)}
        </div>
      )}
      <small className="chairman-evidence-ref">依据：{evidence.dataset_ref}</small>
    </figure>
  );
}

function FollowUps({
  questions,
  onSelect,
}: {
  questions: string[];
  onSelect?: (question: string) => void;
}) {
  if (!questions.length) return null;
  return (
    <section className="executive-follow-ups" aria-label="继续追问">
      <small>继续追问</small>
      <div>
        {questions.slice(0, 3).map((question) => (
          <button type="button" key={question} onClick={() => onSelect?.(question)}>
            <span>{question}</span><b aria-hidden="true">›</b>
          </button>
        ))}
      </div>
    </section>
  );
}

function DataAnswer({
  answer,
  onFollowUp,
}: {
  answer: ChairmanAnswer;
  onFollowUp?: (question: string) => void;
}) {
  return (
    <div className={`chairman-answer template-${answer.template_id}`}>
      <header className="chairman-decision">
        <div>
          <span>{templateLabels[answer.template_id]}</span>
          <i className={`decision-readiness ${answer.decision_readiness}`}>
            {answer.decision_readiness === "ready" ? "可决策" : answer.decision_readiness === "conditional" ? "有条件判断" : "暂不足以判断"}
          </i>
        </div>
        <h2>{answer.decision_line}</h2>
        <p>{answer.confidence.reason}</p>
      </header>

      {answer.metrics.length > 0 && (
        <dl className="chairman-metrics" aria-label="关键指标">
          {answer.metrics.slice(0, 3).map((metric) => (
            <div key={`${metric.label}-${metric.evidence_refs.join("-")}`}>
              <dt>{metric.label}</dt>
              <dd>{formatMetricValue(metric.value, metric.unit)}</dd>
              <small>{metric.context}</small>
            </div>
          ))}
        </dl>
      )}

      <PrimaryEvidenceView answer={answer} />

      {answer.risks_or_opportunities.length > 0 && (
        <section className="chairman-signals">
          <header><h3>需要关注</h3><span>风险与机会</span></header>
          <div>
            {answer.risks_or_opportunities.slice(0, 3).map((item, index) => (
              <article className={item.type} key={`${item.title}-${index}`}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div><strong>{item.title}</strong><p>{item.impact}</p></div>
              </article>
            ))}
          </div>
        </section>
      )}

      {answer.actions.length > 0 && (
        <section className="chairman-actions">
          <header><h3>建议动作</h3><span>责任闭环</span></header>
          <ol>
            {answer.actions.slice(0, 4).map((item, index) => (
              <li key={`${item.owner}-${item.action}-${index}`}>
                <span>{item.owner}</span>
                <div><strong>{item.action}</strong><small>{item.due_at} · {item.success_metric}</small></div>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section className={`chairman-quality ${answer.data_quality.readiness}`}>
        <div>
          <span>数据就绪度</span>
          <strong>{answer.data_quality.readiness === "ready" ? "数据可用于当前判断" : answer.data_quality.decision_impact}</strong>
        </div>
        <dl>
          <div><dt>范围</dt><dd>{answer.data_quality.scope}</dd></div>
          <div><dt>数据截至</dt><dd>{answer.data_quality.as_of}</dd></div>
        </dl>
        {answer.data_quality.issues.length > 0 && (
          <ul>{answer.data_quality.issues.map((issue, index) => <li key={`${issue.dimension}-${index}`}>{issue.detail}</li>)}</ul>
        )}
      </section>

      <details className="chairman-sources">
        <summary>来源详情</summary>
        <div>{answer.sources.map((source) => (
          <p key={source.id}><strong>{professionalSourceLabel(source.label)}</strong><span>{source.as_of}{source.dataset_version ? ` · ${source.dataset_version}` : ""}</span></p>
        ))}</div>
      </details>
      <FollowUps questions={answer.follow_up_questions} onSelect={onFollowUp} />
    </div>
  );
}

function GeneralAnswer({
  answer,
  onFollowUp,
}: {
  answer: ExecutiveGeneralAnswer;
  onFollowUp?: (question: string) => void;
}) {
  return (
    <div className={`executive-general-answer mode-${answer.mode}`}>
      <header>
        <span>{modeLabels[answer.mode]}</span>
        <h2>{answer.headline}</h2>
        <p>{answer.direct_answer}</p>
      </header>
      {answer.sections.length > 0 && <div className="executive-general-sections">{answer.sections.slice(0, 4).map((section, index) => (
        <section key={`${section.title}-${index}`}><h3>{section.title}</h3><p>{section.content}</p></section>
      ))}</div>}
      {answer.action_items.length > 0 && <section className="executive-general-actions"><h3>下一步</h3><ol>{answer.action_items.slice(0, 4).map((item, index) => (
        <li key={`${item.action}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{item.action}</strong>{item.rationale && <p>{item.rationale}</p>}</div></li>
      ))}</ol></section>}
      {answer.draft_markdown && <section className="executive-writing-draft"><header><h3>正文草稿</h3><button type="button" onClick={() => void navigator.clipboard.writeText(answer.draft_markdown || "")}>复制</button></header><pre>{answer.draft_markdown}</pre></section>}
      {(answer.capability_notice || answer.caveats.length > 0) && <aside className="executive-caveats">{answer.capability_notice && <p>{answer.capability_notice}</p>}{answer.caveats.map((item) => <p key={item}>{item}</p>)}</aside>}
      <FollowUps questions={answer.follow_up_questions} onSelect={onFollowUp} />
    </div>
  );
}

export function AssistantOutputRenderer({
  envelope,
  onFollowUp,
}: {
  envelope: AssistantOutputEnvelope;
  onFollowUp?: (question: string) => void;
}) {
  if (envelope.kind === "data") return <DataAnswer answer={envelope.body} onFollowUp={onFollowUp} />;
  if (envelope.kind === "general") return <GeneralAnswer answer={envelope.body} onFollowUp={onFollowUp} />;
  return <p className="assistant-clarification">{envelope.body.question}</p>;
}
