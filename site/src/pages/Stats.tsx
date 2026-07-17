import { Fragment } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useJson, useJsonl } from "../lib/data";
import type { Heatmap, ListeningStats, Stats, StatsHistoryRow } from "../lib/types";
import { Empty, Loading, Section, StatCard } from "../components/ui";
import { PlayIcon, usePlayer } from "../lib/player";

const GREEN = "#1ed760";
const AXIS = "#b3b3b3";
const GRID = "#2a2a2a";
const TIP = { background: "#282828", border: "1px solid #4d4d4d", borderRadius: 8 };
const DOW = ["月", "火", "水", "木", "金", "土", "日"];
const LIB_ROW = "__library__"; // ユニーク曲数の番兵行（延べ合計ではない）

export function StatsPage() {
  const stats = useJson<Stats>("stats");
  const history = useJsonl<StatsHistoryRow>("stats_history");
  const heat = useJson<Heatmap>("heatmap");
  const listen = useJson<ListeningStats>("listening_stats");

  return (
    <>
      <Section title="ライブラリの成長">
        {history.loading || stats.loading ? <Loading /> : <Growth rows={history.data ?? []} stats={stats.data} />}
      </Section>

      <Section title="アーティスト分布" aside={<span className="t-small">棒をタップで開く</span>}>
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

function Growth({ rows, stats }: { rows: StatsHistoryRow[]; stats: Stats | null }) {
  // 番兵行（ユニーク曲数）だけを時系列に使う。延べ合計（プレイリスト横断）は二重計上になるため使わない。
  const uniqueRows = rows.filter((r) => r.playlist_id === LIB_ROW);
  const byDate = new Map<string, number>();
  for (const r of uniqueRows) byDate.set(r.date, r.count);
  const data = [...byDate.entries()].sort().map(([date, total]) => ({ date, total }));
  // total（正規のユニーク数）→ 番兵履歴の最新 → 年代分布の合計（≒ユニーク数）の順にフォールバック。
  // 旧データ（total 未生成）でも誤った延べ合計ではなく妥当な数を出す。
  const decadeSum = stats?.decades?.reduce((s, d) => s + d.count, 0) ?? 0;
  const current = stats?.total ?? (data.length ? data[data.length - 1].total : decadeSum || null);

  return (
    <div className="card">
      {current != null && (
        <div style={{ marginBottom: "var(--sp-3)" }}>
          <div className="t-small" style={{ textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 700 }}>
            ライブラリ（ユニーク曲数）
          </div>
          <div className="t-display num" style={{ fontSize: "2.4rem", marginTop: 2 }}>{current.toLocaleString()}</div>
        </div>
      )}
      {data.length === 0 ? (
        <Empty>ユニーク曲数の履歴は次回の夜間更新から記録します（毎晩1点ずつ増えます）。</Empty>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data} margin={{ left: -10, right: 8, top: 8 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" stroke={AXIS} fontSize={11} />
            <YAxis stroke={AXIS} fontSize={11} />
            <Tooltip contentStyle={TIP} labelStyle={{ color: "#fff" }} formatter={(v: number) => [v.toLocaleString(), "ユニーク曲数"]} />
            <Line type="monotone" dataKey="total" stroke={GREEN} strokeWidth={2} dot={false} name="ユニーク曲数" />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ArtistBars({ stats }: { stats: Stats | null }) {
  const { play } = usePlayer();
  if (!stats || !stats.artists_top.length) return <Empty>データなし</Empty>;
  const data = stats.artists_top.slice(0, 15);
  const max = Math.max(...data.map((a) => a.count), 1);
  // id があれば常駐プレイヤーでそのアーティストを再生、無ければ（旧データ）名前で Spotify 検索。
  function open(a: { name: string; id?: string }) {
    if (a.id) play(`spotify:artist:${a.id}`);
    else window.open(`https://open.spotify.com/search/${encodeURIComponent(a.name)}`, "_blank", "noopener");
  }
  return (
    <div className="card">
      {data.map((a) => (
        <button className="art-row" key={a.name} onClick={() => open(a)} title={`${a.name} を再生`}>
          <span className="art-name">{a.name}</span>
          <span className="art-bar"><i style={{ width: `${(a.count / max) * 100}%` }} /></span>
          <span className="art-count num">{a.count}</span>
          <span className="art-play" aria-hidden><PlayIcon /></span>
        </button>
      ))}
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
          <XAxis dataKey="label" stroke={AXIS} fontSize={11} />
          <YAxis stroke={AXIS} fontSize={11} />
          <Tooltip contentStyle={TIP} labelStyle={{ color: "#fff" }} cursor={{ fill: "#ffffff10" }} />
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
                    background: v ? `rgba(30,215,96,${0.15 + 0.85 * (v / max)})` : "#2a2a2a",
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
