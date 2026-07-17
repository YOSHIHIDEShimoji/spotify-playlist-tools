import { useJson, useJsonl } from "../lib/data";
import type { ListeningStats, RunRecord } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";

export function Home() {
  const runs = useJsonl<RunRecord>("runs");
  const listen = useJson<ListeningStats>("listening_stats");

  return (
    <>
      {runs.loading ? (
        <div className="nightband">
          <Loading />
        </div>
      ) : (
        <NightBand runs={runs.data ?? []} listen={listen.data} />
      )}

      <Section title="実行履歴">
        {runs.loading ? <Loading /> : <RunTimeline runs={runs.data ?? []} />}
      </Section>

      <Section title="今週よく聴いた曲">
        <WeeklyTop listen={listen.data} loading={listen.loading} />
      </Section>
    </>
  );
}

function realRuns(runs: RunRecord[]): RunRecord[] {
  return runs.filter((r) => !r.dry_run);
}

const STATUS_BADGE: Record<string, { cls: string; label: string }> = {
  success: { cls: "badge-b", label: "success" },
  partial: { cls: "badge-c", label: "partial" },
  failure: { cls: "badge-a", label: "failed" },
};

function jpDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${Number(m[2])}月${Number(m[3])}日` : iso;
}

/** Home ヒーロー: 昨晩の夜間ランを署名要素として最前面に出す。 */
function NightBand({ runs, listen }: { runs: RunRecord[]; listen: ListeningStats | null }) {
  const real = realRuns(runs);
  const latest = real[real.length - 1];

  let streak = 0;
  for (let i = real.length - 1; i >= 0; i--) {
    if (real[i].status === "success") streak++;
    else break;
  }
  const successRate = real.length
    ? Math.round((real.filter((r) => r.status === "success").length / real.length) * 100)
    : 0;

  if (!latest) {
    return (
      <div className="nightband">
        <div className="nightband-top">
          <span className="nightband-dot" />
          <span className="eyebrow">nightly run · 01:00 JST</span>
        </div>
        <h1 className="t-display">まだ夜間ランの記録がありません</h1>
        <p className="nightband-sub">
          毎晩 01:00（JST）に inbox → sync → sort → archive が自動で走り、その結果が翌朝ここに出ます。
        </p>
      </div>
    );
  }

  const s = latest.steps;
  const badge = STATUS_BADGE[latest.status] ?? STATUS_BADGE.partial;
  const touched = s.inbox.processed + s.sync.added + s.sync.removed + s.archive.added;
  const sub =
    touched === 0
      ? "静かな夜でした。ライブラリに変更はありません。"
      : `inbox ${s.inbox.processed}件を振り分け、同期 +${s.sync.added}/−${s.sync.removed}、` +
        `${s.sort.playlists} プレイリストを整えました。`;

  return (
    <div className="nightband">
      <div className="nightband-top">
        <span className="nightband-dot" />
        <span className="eyebrow">nightly run · 01:00 JST</span>
        <span className={"badge status " + badge.cls}>{badge.label}</span>
      </div>

      <h1 className="t-display">{jpDate(latest.date)}の記録</h1>
      <p className="nightband-sub">{sub}</p>

      <div className="pipe">
        <PipeStep n="01" name="inbox" value={s.inbox.processed} detail={`邦 ${s.inbox.japanese} · 洋 ${s.inbox.western} · 不明 ${s.inbox.unknown}`} />
        <PipeStep n="02" name="sync" value={s.sync.added} prefix="+" detail={`−${s.sync.removed} · 新規AP ${s.sync.new_playlists}`} />
        <PipeStep n="03" name="sort" value={s.sort.playlists} detail={`見送り ${s.sort.skipped}`} />
        <PipeStep n="04" name="archive" value={s.archive.added} prefix="+" detail="Top50 追加" />
      </div>

      <div className="nightband-foot">
        <div className="foot-stat">
          <span className="k">連続成功</span>
          <span className="v">{streak}<span className="muted"> 日</span> · 成功率 {successRate}%</span>
        </div>
        <div className="foot-stat">
          <span className="k">累計再生</span>
          <span className="v">
            <span className="dawn">{listen ? listen.milestone.total.toLocaleString() : "—"}</span>
            {listen?.milestone.next ? <span className="muted"> / 次 {listen.milestone.next.toLocaleString()}</span> : null}
          </span>
        </div>
      </div>
    </div>
  );
}

function PipeStep(
  { n, name, value, prefix, detail }: { n: string; name: string; value: number; prefix?: string; detail: string },
) {
  const cls = value > 0 ? "v pos" : "v zero";
  return (
    <div className="pipe-step">
      <span className="k">{n} · {name}</span>
      <span className={cls}>{prefix && value > 0 ? prefix : ""}{value}</span>
      <span className="d">{detail}</span>
    </div>
  );
}

function RunTimeline({ runs }: { runs: RunRecord[] }) {
  if (!runs.length) return <Empty>まだ実行記録がありません。</Empty>;
  const recent = [...runs].slice(-14).reverse();
  return (
    <div className="card" style={{ overflowX: "auto" }}>
      <table className="data-table">
        <thead>
          <tr><th>日付</th><th>状態</th><th>inbox</th><th>sync</th><th>sort</th><th>archive</th></tr>
        </thead>
        <tbody>
          {recent.map((r, i) => (
            <tr key={`${r.run_id}-${i}`}>
              <td>{r.date}{r.dry_run && <span className="badge" style={{ marginLeft: 6 }}>dry</span>}</td>
              <td>
                <span className={"badge " + (r.status === "success" ? "badge-b" : "badge-c")}>{r.status}</span>
              </td>
              <td className="num">{r.steps.inbox.processed}</td>
              <td className="num">+{r.steps.sync.added}/-{r.steps.sync.removed}</td>
              <td className="num">{r.steps.sort.playlists}</td>
              <td className="num">+{r.steps.archive.added}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WeeklyTop({ listen, loading }: { listen: ListeningStats | null; loading: boolean }) {
  if (loading) return <Loading />;
  if (!listen || listen.weekly_top.length === 0)
    return <Empty>聴取ログが貯まると、今週よく聴いた曲がここに出ます（3時間ごとに収集）。</Empty>;
  return (
    <div className="card">
      {listen.weekly_top.slice(0, 15).map((t, i) => (
        <div className="list-row" key={t.track_id}>
          <span className="list-rank">{i + 1}</span>
          <span className="list-main">
            <div className="name">{t.name}</div>
            <div className="t-small">{t.artists.join(", ")}</div>
          </span>
          <span className="list-count">{t.count}回</span>
        </div>
      ))}
    </div>
  );
}
