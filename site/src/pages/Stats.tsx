import { Fragment } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useJson, useJsonl } from "../lib/data";
import type { Heatmap, ListeningStats, Stats, StatsHistoryRow } from "../lib/types";
import { Empty, Loading, Section, StatCard } from "../components/ui";

const GREEN = "#1ed760";
const DOW = ["月", "火", "水", "木", "金", "土", "日"];

export function StatsPage() {
  const stats = useJson<Stats>("stats");
  const history = useJsonl<StatsHistoryRow>("stats_history");
  const heat = useJson<Heatmap>("heatmap");
  const listen = useJson<ListeningStats>("listening_stats");

  return (
    <>
      <Section title="ライブラリの成長">
        {history.loading ? <Loading /> : <Growth rows={history.data ?? []} />}
      </Section>

      <Section title="アーティスト分布">
        {stats.loading ? <Loading /> : <ArtistBars stats={stats.data} />}
      </Section>

      <Section title="リリース年代分布">
        {stats.loading ? <Loading /> : <DecadeBars stats={stats.data} />}
      </Section>

      <Section title="連続聴取">
        {listen.data ? (
          <div className="row">
            <StatCard label="現在の streak" value={`${listen.data.streak}日`} />
            <StatCard label="累計再生" value={listen.data.milestone.total.toLocaleString()} sub={listen.data.milestone.next ? `次 ${listen.data.milestone.next.toLocaleString()}` : ""} />
          </div>
        ) : (
          <Empty>聴取ログ蓄積後に表示されます。</Empty>
        )}
      </Section>

      <Section title="聴取ヒートマップ（曜日 × 時間帯）">
        {heat.loading ? <Loading /> : <HeatGrid heat={heat.data} />}
      </Section>
    </>
  );
}

function Growth({ rows }: { rows: StatsHistoryRow[] }) {
  if (!rows.length) return <Empty>まだ履歴がありません（毎晩1点ずつ増えます）。</Empty>;
  const byDate = new Map<string, number>();
  for (const r of rows) byDate.set(r.date, (byDate.get(r.date) ?? 0) + r.count);
  const data = [...byDate.entries()].sort().map(([date, total]) => ({ date, total }));
  return (
    <div className="card">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ left: -10, right: 8, top: 8 }}>
          <CartesianGrid stroke="#232838" vertical={false} />
          <XAxis dataKey="date" stroke="#9aa4b6" fontSize={11} />
          <YAxis stroke="#9aa4b6" fontSize={11} />
          <Tooltip contentStyle={{ background: "#1b1f2b", border: "1px solid rgba(255,255,255,0.14)", borderRadius: 10 }} />
          <Line type="monotone" dataKey="total" stroke={GREEN} strokeWidth={2} dot={false} name="総曲数" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ArtistBars({ stats }: { stats: Stats | null }) {
  if (!stats || !stats.artists_top.length) return <Empty>データなし</Empty>;
  const data = stats.artists_top.slice(0, 15);
  return (
    <div className="card">
      <ResponsiveContainer width="100%" height={Math.max(220, data.length * 26)}>
        <BarChart data={data} layout="vertical" margin={{ left: 20, right: 16 }}>
          <XAxis type="number" stroke="#9aa4b6" fontSize={11} />
          <YAxis type="category" dataKey="name" width={110} stroke="#9aa4b6" fontSize={11} />
          <Tooltip contentStyle={{ background: "#1b1f2b", border: "1px solid rgba(255,255,255,0.14)", borderRadius: 10 }} cursor={{ fill: "#ffffff10" }} />
          <Bar dataKey="count" fill={GREEN} radius={[0, 4, 4, 0]} name="曲数" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function DecadeBars({ stats }: { stats: Stats | null }) {
  if (!stats || !stats.decades.length) return <Empty>データなし</Empty>;
  const data = stats.decades.map((d) => ({ label: `${d.decade}s`, count: d.count }));
  return (
    <div className="card">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ left: -10, right: 8 }}>
          <XAxis dataKey="label" stroke="#9aa4b6" fontSize={11} />
          <YAxis stroke="#9aa4b6" fontSize={11} />
          <Tooltip contentStyle={{ background: "#1b1f2b", border: "1px solid rgba(255,255,255,0.14)", borderRadius: 10 }} cursor={{ fill: "#ffffff10" }} />
          <Bar dataKey="count" fill={GREEN} radius={[4, 4, 0, 0]} name="曲数" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function HeatGrid({ heat }: { heat: Heatmap | null }) {
  if (!heat || !heat.cells.length)
    return <Empty>聴取ログが貯まると、曜日×時間帯の傾向が出ます。</Empty>;
  const max = Math.max(...heat.cells.map((c) => c.count), 1);
  const lookup = new Map(heat.cells.map((c) => [`${c.dow}-${c.hour}`, c.count]));
  return (
    <div className="card" style={{ overflowX: "auto" }}>
      <div style={{ display: "grid", gridTemplateColumns: `28px repeat(24, 1fr)`, gap: 2, minWidth: 560 }}>
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} className="t-small" style={{ textAlign: "center" }}>{h % 6 === 0 ? h : ""}</span>
        ))}
        {DOW.map((d, dow) => (
          <Fragment key={dow}>
            <span className="t-small" style={{ alignSelf: "center" }}>{d}</span>
            {Array.from({ length: 24 }, (_, h) => {
              const v = lookup.get(`${dow}-${h}`) ?? 0;
              return (
                <div
                  key={`${dow}-${h}`}
                  title={`${d} ${h}時: ${v}回`}
                  style={{
                    aspectRatio: "1", borderRadius: 2,
                    background: v ? `rgba(30,215,96,${0.15 + 0.85 * (v / max)})` : "#1b1f2b",
                  }}
                />
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
