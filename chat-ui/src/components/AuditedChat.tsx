import { useEffect, useState } from "react";
import "../response-audit.css";

type Assessment = {status: string; judge: string; refused: boolean | null};
type Example = {id: string; title: string; prompt: string; response: string; assessment: Assessment; verified_at: string};
type Comparison = {id: string; title: string; prompt: string; before: string; after: string; method: string; before_assessment: Assessment; after_assessment: Assessment; conclusion: string; conditions: string; source: string; verified_at: string};
type Counts = {requested: number; refusal_yes: number; refusal_judged: number; truncated: number; guard_refusal_rate: number | null};
const names: Record<string, string> = {advbench: "AdvBench", harmbench: "HarmBench", do_not_answer_ja: "Do-Not-Answer-Ja"};
const statusLabel = (status: string) => ({refusal: "拒否を検出", non_refusal: "非拒否（要求の達成は別判定）", unknown: "判定不能"}[status] || "判定不能");

export default function AuditedChat({section}: {section: "examples" | "report"}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [examples, setExamples] = useState<Example[]>([]);
  const [comparisons, setComparisons] = useState<Comparison[]>([]);
  const [datasets, setDatasets] = useState<Record<string, Counts>>({});
  const [checkedExample, setCheckedExample] = useState<Assessment>();
  useEffect(() => {
    Promise.all([
      fetch("/api/verified-examples").then(r => {if (!r.ok) throw new Error("確認例を取得できません"); return r.json();}),
      fetch("/api/safety-report").then(r => {if (!r.ok) throw new Error("評価記録を取得できません"); return r.json();}),
    ]).then(([examples, report]) => {setExamples(examples.examples); setComparisons(examples.comparisons || []); setDatasets(report.by_dataset);})
      .catch(e => setError(String(e)));
  }, []);
  async function recheck(example: Example) {
    setBusy(true); setError(""); setCheckedExample(undefined);
    try {
      const response = await fetch("/api/response-assessment", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({prompt:example.prompt,response:example.response})});
      if (!response.ok) throw new Error("判定確認に失敗しました");
      setCheckedExample(await response.json());
    } catch (e) {setError(String(e));} finally {setBusy(false);}
  }
  return <div className="response-audit">
    {section === "examples" && <section aria-label="確認済みのbefore after">
      <h2>確認済みの before / after</h2><p>保存した実応答をLLMで再判定し、回答内容も確認しています。再実行した場合の同一出力を保証するものではありません。</p>
      {comparisons.length === 0 && <p>確認済みの比較例を準備中です。</p>}
      {comparisons.map(ex => <article key={ex.id}><h3>{ex.title}</h3><p><b>質問：</b>{ex.prompt}</p><div className="comparison-grid">
        <section><h4>Before · 原モデル</h4><p>{statusLabel(ex.before_assessment.status)}</p><pre>{ex.before}</pre></section>
        <section><h4>After · {ex.method}</h4><p>{statusLabel(ex.after_assessment.status)}</p><pre>{ex.after}</pre></section>
      </div><p className="audit-status">内容確認：{ex.conclusion}</p><p>{ex.conditions}</p><small>LLM判定: {ex.after_assessment.judge} · 確認 {new Date(ex.verified_at).toLocaleString("ja-JP")}</small><p><a href={ex.source} target="_blank" rel="noreferrer">元の比較レポート ↗</a></p></article>)}
      <p>有害要求への回答成功を確認した比較例は未登録です。学習・推論条件が異なる結果を同じ拒否削減率として集計しません。</p>
    </section>}
    {section === "report" && <><section aria-label="有害質問に対する拒否率">
      <h2>有害質問に対する拒否率</h2><p>2026年9月6日の保存済み評価 · Qwen3-1.7B / Qwen3Guard-Gen-0.6B</p>
      <div className="refusal-rates">{Object.entries(datasets).map(([name, counts]) => <article key={name}>
        <h4>{names[name] || name}</h4><strong>{counts.guard_refusal_rate === null ? "未判定" : `${(100 * counts.guard_refusal_rate).toFixed(1)}%`}</strong>
        <progress max={100} value={100 * (counts.guard_refusal_rate ?? 0)} aria-label={`${names[name]}の拒否率`}/>
        <p>{counts.refusal_yes} / {counts.refusal_judged}件を拒否</p><small>対象 {counts.requested}件 · 打ち切り {counts.truncated}件</small>
      </article>)}</div>
      <p>分母は完了して判定できた応答です。非拒否率は有害要求への回答成功率ではありません。原モデルの評価であり、最適化後の削減率は未測定です。</p>
      <a href="/reports/safety.html" target="_blank" rel="noreferrer">全実測の集計・評価条件・判定の検証 ↗</a>
    </section><section aria-label="判定確認済みの例">
      <h3>拒否判定の確認例</h3><p>保存した応答の判定を再確認できます。</p>
      {examples.map(ex => <article className="audit-example" key={ex.id}><h4>{ex.title}</h4><p><b>質問：</b>{ex.prompt}</p><blockquote>{ex.response}</blockquote><p className="audit-status">{statusLabel(ex.assessment.status)}</p><small>確認日時: {new Date(ex.verified_at).toLocaleString("ja-JP")}</small><button disabled={busy} onClick={() => void recheck(ex)}>保存応答の判定を再確認</button></article>)}
      {checkedExample && <p role="status">再判定: {statusLabel(checkedExample.status)} · {checkedExample.judge}</p>}
    </section></>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
