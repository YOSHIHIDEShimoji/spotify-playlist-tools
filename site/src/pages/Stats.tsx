import { Fragment, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useJson, useJsonl } from "../lib/data";
import type { Heatmap, ListeningStats, Stats, StatsGroup, StatsHistoryRow } from "../lib/types";
import { Empty, Loading, ScrollRow, Section, StatCard } from "../components/ui";
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
  // 統計の対象プレイリスト選択（null = 3つ全部）。分布・年代の両方に効く。
  const [sel, setSel] = useState<string | null>(null);

  const dist = stats.data?.dist;
  const group: StatsGroup | null = dist
    ? (sel && dist.by[sel] ? dist.by[sel] : dist.all)
    : (stats.data ?? null);
  const selName = dist && sel ? dist.playlists.find((p) => p.id === sel)?.name : null;

  // ユニーク曲数の履歴が2点以上あって初めて「成長」グラフになる（それまでは「規模」）。
  const growthRows = (history.data ?? []).filter((r) => r.playlist_id === LIB_ROW);
  const growthTitle = growthRows.length > 1 ? "ライブラリの成長" : "ライブラリの規模";
  // 聴取ログが有効か（再認証前は since=null・total=0 のダミーが来るので実数ゼロと区別する）。
  const listenActive = !!listen.data && (listen.data.since != null || listen.data.milestone.total > 0);

  return (
    <>
      <Section title={growthTitle}>
        {history.loading || stats.loading ? <Loading /> : <Growth rows={history.data ?? []} stats={stats.data} />}
      </Section>

      <Section
        title="アーティスト分布"
        aside={group && <span className="t-small">{selName ?? "3プレイリスト合算"} · {group.total.toLocaleString()}曲</span>}
      >
        {stats.loading ? (
          <Loading />
        ) : (
          <>
            {dist && <PlaylistPicker playlists={dist.playlists} sel={sel} onSel={setSel} />}
            <p className="t-small" style={{ margin: "0 0 var(--sp-3)" }}>
              対象プレイリストを選ぶと、その中での分布に切り替わります（未選択＝Western・Japanese・1900's の合算）。タップで再生。
            </p>
            <ArtistBars group={group} />
          </>
        )}
      </Section>

      <Section title="リリース年代分布">
        {stats.loading ? <Loading /> : <DecadeBars key={sel ?? "all"} group={group} />}
      </Section>

      <Section title="連続聴取">
        {listenActive ? (
          <div className="row">
            <StatCard label="現在の streak" value={`${listen.data!.streak}日`} />
            <StatCard label="累計再生" value={listen.data!.milestone.total.toLocaleString()} sub={listen.data!.milestone.next ? `次のマイルストーン ${listen.data!.milestone.next.toLocaleString()}回` : ""} />
          </div>
        ) : (
          <Empty>聴取ログはまだ有効化されていません。再認証すると、連続聴取と累計再生の計測が始まります。</Empty>
        )}
      </Section>

      <Section title="聴取ヒートマップ（曜日 × 時間帯）">
        {heat.loading ? <Loading /> : <HeatGrid heat={heat.data} />}
      </Section>
    </>
  );
}

function PlaylistPicker(
  { playlists, sel, onSel }:
    { playlists: { id: string; name: string }[]; sel: string | null; onSel: (id: string | null) => void },
) {
  return (
    <ScrollRow className="pl-picker" role="tablist" ariaLabel="統計の対象プレイリスト">
      <button role="tab" aria-selected={!sel} className={!sel ? "is-active" : ""} onClick={() => onSel(null)}>すべて</button>
      {playlists.map((p) => (
        <button role="tab" aria-selected={sel === p.id} key={p.id} className={sel === p.id ? "is-active" : ""} onClick={() => onSel(p.id)}>
          {p.name}
        </button>
      ))}
    </ScrollRow>
  );
}

function Growth({ rows, stats }: { rows: StatsHistoryRow[]; stats: Stats | null }) {
  // 番兵行（ユニーク曲数）だけを時系列に使う。延べ合計（プレイリスト横断）は二重計上になるため使わない。
  const uniqueRows = rows.filter((r) => r.playlist_id === LIB_ROW);
  const byDate = new Map<string, number>();
  for (const r of uniqueRows) byDate.set(r.date, r.count);
  const data = [...byDate.entries()].sort().map(([date, total]) => ({ date, total }));
  // total（正規のユニーク数）→ 番兵履歴の最新 → 年代分布の合計（≒ユニーク数）の順にフォールバック。
  const decadeSum = stats?.decades?.reduce((s, d) => s + d.count, 0) ?? 0;
  const current = stats?.total ?? (data.length ? data[data.length - 1].total : decadeSum || null);

  return (
    <div className="card">
      {current != null && (
        <div style={{ marginBottom: "var(--sp-3)" }}>
          <div className="t-small" style={{ textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 700 }}>
            夜間管理ライブラリ（ユニーク曲数）
          </div>
          <div className="t-display num" style={{ fontSize: "2.4rem", marginTop: 2 }}>{current.toLocaleString()}</div>
          <div className="t-small" style={{ marginTop: 2 }}>
            毎晩 sort が整える邦・洋プレイリストの重複なし曲数。下の分布は Western・Japanese・1900's を合算するため数が異なります。
          </div>
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
            <Line type="monotone" dataKey="total" stroke={GREEN} strokeWidth={2} dot={false} name="ユニーク曲数" isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ArtistBars({ group }: { group: StatsGroup | null }) {
  const { play } = usePlayer();
  if (!group || !group.artists_top.length) return <Empty>データなし</Empty>;
  const data = group.artists_top.slice(0, 15);
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

function DecadeBars({ group }: { group: StatsGroup | null }) {
  if (!group || !group.decades.length) return <Empty>データなし</Empty>;
  const data = group.decades.map((d) => ({ label: `${d.decade}s`, count: d.count }));
  return (
    <div className="card">
      {/* 初回に「軸だけでバー0本」になる recharts のアニメ由来の描画抜けを止める（isAnimationActive=false）。 */}
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ left: -10, right: 8 }}>
          <XAxis dataKey="label" stroke={AXIS} fontSize={11} />
          <YAxis stroke={AXIS} fontSize={11} />
          <Tooltip contentStyle={TIP} labelStyle={{ color: "#fff" }} cursor={{ fill: "#ffffff10" }} />
          <Bar dataKey="count" fill={GREEN} radius={[4, 4, 0, 0]} name="曲数" isAnimationActive={false} />
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
