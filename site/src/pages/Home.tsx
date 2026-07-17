import { useJson, useJsonl } from "../lib/data";
import type { ListeningStats, RunRecord } from "../lib/types";
import { Empty, Loading, Section, StatCard } from "../components/ui";

export function Home() {
  const runs = useJsonl<RunRecord>("runs");
  const listen = useJson<ListeningStats>("listening_stats");

  return (
    <>
      <Section title="昨晩のサマリ">
        {runs.loading ? <Loading /> : <SummaryCards runs={runs.data ?? []} listen={listen.data} />}
      </Section>

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

function SummaryCards({ runs, listen }: { runs: RunRecord[]; listen: ListeningStats | null }) {
  const real = realRuns(runs);
  const latest = real[real.length - 1];
  const successRate = real.length
    ? Math.round((real.filter((r) => r.status === "success").length / real.length) * 100)
    : 0;
  let streak = 0;
  for (let i = real.length - 1; i >= 0; i--) {
    if (real[i].status === "success") streak++;
    else break;
  }
  const s = latest?.steps;
  return (
    <div className="row">
      <StatCard
        label="inbox 振り分け"
        value={s ? s.inbox.processed : "—"}
        sub={s ? `邦 ${s.inbox.japanese} / 洋 ${s.inbox.western} / 不明 ${s.inbox.unknown}` : "実行待ち"}
      />
      <StatCard label="sync" value={s ? `+${s.sync.added}` : "—"} sub={s ? `-${s.sync.removed} / 新規AP ${s.sync.new_playlists}` : ""} />
      <StatCard label="sort" value={s ? s.sort.playlists : "—"} sub={s ? `見送り ${s.sort.skipped}` : ""} />
      <StatCard label="連続成功" value={`${streak}日`} sub={`成功率 ${successRate}%`} />
      <StatCard
        label="累計再生"
        value={listen ? listen.milestone.total.toLocaleString() : "—"}
        sub={listen?.milestone.next ? `次の節目 ${listen.milestone.next.toLocaleString()}` : "聴取ログ蓄積中"}
      />
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
              <td>{r.steps.inbox.processed}</td>
              <td>+{r.steps.sync.added}/-{r.steps.sync.removed}</td>
              <td>{r.steps.sort.playlists}</td>
              <td>+{r.steps.archive.added}</td>
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
