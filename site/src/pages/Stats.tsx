import { Fragment, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useJson, useJsonl } from "../lib/data";
import type { Heatmap, ListeningStats, RankedTrack, SearchIndex, SearchTrack, Stats, StatsGroup, StatsHistoryRow } from "../lib/types";
import { Empty, Loading, ScrollRow, Section, StatCard } from "../components/ui";
import { PlayButton, PlayIcon, usePlayer } from "../lib/player";
import { ArtistModal, Modal } from "../components/Modal";
import type { ModalArtist } from "../components/Modal";
import { dowLabels, monthDay, useLang, useT } from "../lib/i18n";

const GREEN = "#1ed760";
const AXIS = "#b3b3b3";
const GRID = "#2a2a2a";
const TIP = { background: "#282828", border: "1px solid #4d4d4d", borderRadius: 8 };
const LIB_ROW = "__library__"; // ユニーク曲数の番兵行（延べ合計ではない）

export function StatsPage() {
  const tx = useT();
  const stats = useJson<Stats>("stats");
  const history = useJsonl<StatsHistoryRow>("stats_history");
  const heat = useJson<Heatmap>("heatmap");
  const listen = useJson<ListeningStats>("listening_stats");
  const search = useJson<SearchIndex>("search_index"); // 年代モーダルの曲一覧に使う
  // 統計の対象プレイリスト選択（null = 3つ全部）。アーティスト分布と年代分布で別々に選べる。
  const [sel, setSel] = useState<string | null>(null);
  const [decSel, setDecSel] = useState<string | null>(null);

  const dist = stats.data?.dist;
  // 選択（null=全部）から、その分布(group)・年代モーダルで曲を絞る名前(names)・表示名(name)を求める。
  function pick(selection: string | null): { group: StatsGroup | null; names: string[] | null; name: string | null } {
    if (!dist) return { group: stats.data ?? null, names: null, name: null };
    const group = selection && dist.by[selection] ? dist.by[selection] : dist.all;
    const name = selection ? (dist.playlists.find((p) => p.id === selection)?.name ?? null) : null;
    const names = selection ? ([name].filter(Boolean) as string[]) : dist.playlists.map((p) => p.name);
    return { group, names, name };
  }
  const { group, name: selName } = pick(sel);
  const { group: decGroup, names: decGroupNames, name: decSelName } = pick(decSel);
  const combined = tx("3 playlists combined", "3プレイリスト合算");

  // ユニーク曲数の履歴が2点以上あって初めて「成長」グラフになる（それまでは「規模」）。
  const growthRows = (history.data ?? []).filter((r) => r.playlist_id === LIB_ROW);
  const growthTitle = growthRows.length > 1 ? tx("Library growth", "ライブラリの成長") : tx("Library size", "ライブラリの規模");
  // 聴取ログが有効か（再認証前は since=null・total=0 のダミーが来るので実数ゼロと区別する）。
  const listenActive = !!listen.data && (listen.data.since != null || listen.data.milestone.total > 0);

  return (
    <>
      <Section title={growthTitle}>
        {history.loading || stats.loading ? <Loading /> : <Growth rows={history.data ?? []} stats={stats.data} />}
      </Section>

      <Section
        title={tx("Artist distribution", "アーティスト分布")}
        aside={group && <span className="t-small">{selName ?? combined} · {tx(`${group.total.toLocaleString()} songs`, `${group.total.toLocaleString()}曲`)}</span>}
      >
        {stats.loading ? (
          <Loading />
        ) : (
          <>
            {dist && <PlaylistPicker playlists={dist.playlists} sel={sel} onSel={setSel} />}
            <p className="t-small" style={{ margin: "0 0 var(--sp-3)" }}>
              {tx(
                "Pick a playlist to switch to its distribution (unselected = Western, Japanese and 1900's combined). Tap a bar for info, ▶ on the right to play.",
                "対象プレイリストを選ぶと、その中での分布に切り替わります（未選択＝Western・Japanese・1900's の合算）。バーをタップで情報、右の ▶ で再生。",
              )}
            </p>
            <ArtistBars group={group} />
          </>
        )}
      </Section>

      <Section
        title={tx("Release-decade distribution", "リリース年代分布")}
        aside={decGroup && <span className="t-small">{decSelName ?? combined} · {tx(`${decGroup.total.toLocaleString()} songs`, `${decGroup.total.toLocaleString()}曲`)}</span>}
      >
        {stats.loading ? (
          <Loading />
        ) : (
          <>
            {dist && <PlaylistPicker playlists={dist.playlists} sel={decSel} onSel={setDecSel} />}
            <DecadeBars
              key={decSel ?? "all"}
              group={decGroup}
              searchTracks={search.data?.tracks ?? []}
              groupNames={decGroupNames}
            />
          </>
        )}
      </Section>

      <Section title={tx("Listening streak", "連続聴取")}>
        {listenActive ? (
          <div className="row">
            <StatCard label={tx("Current streak", "現在の streak")} value={tx(`${listen.data!.streak} days`, `${listen.data!.streak}日`)} />
            <StatCard
              label={tx("Total plays", "累計再生")}
              value={listen.data!.milestone.total.toLocaleString()}
              sub={listen.data!.milestone.next ? tx(`Next milestone: ${listen.data!.milestone.next.toLocaleString()}`, `次のマイルストーン ${listen.data!.milestone.next.toLocaleString()}回`) : ""}
            />
          </div>
        ) : (
          <Empty>
            {tx(
              "Listening log isn't enabled yet. Re-authenticate to start measuring streak and total plays.",
              "聴取ログはまだ有効化されていません。再認証すると、連続聴取と累計再生の計測が始まります。",
            )}
          </Empty>
        )}
      </Section>

      <Section title={tx("Most played", "よく聴いた曲")}>
        <MostPlayed listen={listen.data} loading={listen.loading} />
      </Section>

      <Section title={tx("Listening heatmap (day × hour)", "聴取ヒートマップ（曜日 × 時間帯）")}>
        {heat.loading ? <Loading /> : <HeatGrid heat={heat.data} />}
      </Section>
    </>
  );
}

/** よく聴いた曲: 今週 / 累計（計測開始から）をトグルで切り替える。累計は cumulative_top。
 * アルバムアートは search_index から曲IDで補完する（管理プレイリストに在る曲は必ず出る）。 */
function MostPlayed({ listen, loading }: { listen: ListeningStats | null; loading: boolean }) {
  const tx = useT();
  const { lang } = useLang();
  const [range, setRange] = useState<"week" | "all">("week");
  const search = useJson<SearchIndex>("search_index");
  const byId = useMemo(
    () => new Map((search.data?.tracks ?? []).map((t) => [t.id, t] as const)),
    [search.data],
  );
  if (loading) return <Loading />;
  const rows: RankedTrack[] = range === "week" ? (listen?.weekly_top ?? []) : (listen?.cumulative_top ?? []);
  const since = range === "all" && listen?.since ? monthDay(listen.since, lang) : null;

  return (
    <>
      <div className="seg" role="tablist" aria-label={tx("Most played range", "よく聴いた期間")}>
        <button role="tab" aria-selected={range === "week"} className={range === "week" ? "is-active" : ""} onClick={() => setRange("week")}>
          {tx("This week", "今週")}
        </button>
        <button role="tab" aria-selected={range === "all"} className={range === "all" ? "is-active" : ""} onClick={() => setRange("all")}>
          {tx("All time", "累計")}
        </button>
      </div>
      {since && (
        <p className="t-small" style={{ margin: "0 0 var(--sp-3)" }}>
          {tx(`Since ${since} (when logging started).`, `${since}（計測開始）からの累計。`)}
        </p>
      )}
      {rows.length === 0 ? (
        <Empty>
          {range === "week"
            ? tx(
                "As listening data accumulates, your most-played tracks this week appear here (collected every 3 hours).",
                "聴取ログが貯まると、今週よく聴いた曲がここに出ます（3時間ごとに収集）。",
              )
            : tx(
                "Your all-time most-played tracks build up here as listening data accumulates (collected every 3 hours).",
                "聴取ログが貯まるほど、計測開始からの累計でよく聴いた曲がここに出ます（3時間ごとに収集）。",
              )}
        </Empty>
      ) : (
        <div className="card">
          {rows.slice(0, 20).map((t, i) => {
            const img = byId.get(t.track_id)?.image;
            return (
              <div className="list-row" key={t.track_id}>
                <span className="list-rank">{i + 1}</span>
                {img ? (
                  <img className="cand-art top-art" src={img} alt="" loading="lazy" width={40} height={40} />
                ) : (
                  <span className="cand-art cand-art--ph top-art" aria-hidden />
                )}
                <span className="list-main">
                  <div className="name">{t.name}</div>
                  <div className="t-small">{t.artists.join(", ")}</div>
                </span>
                <span className="list-count">{tx(`${t.count} plays`, `${t.count}回`)}</span>
                <PlayButton uri={`spotify:track:${t.track_id}`} label={tx(`Play ${t.name}`, `${t.name} を再生`)} />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

function PlaylistPicker(
  { playlists, sel, onSel }:
    { playlists: { id: string; name: string }[]; sel: string | null; onSel: (id: string | null) => void },
) {
  const tx = useT();
  return (
    <ScrollRow className="pl-picker" role="tablist" ariaLabel={tx("Stats target playlist", "統計の対象プレイリスト")}>
      <button role="tab" aria-selected={!sel} className={!sel ? "is-active" : ""} onClick={() => onSel(null)}>{tx("All", "すべて")}</button>
      {playlists.map((p) => (
        <button role="tab" aria-selected={sel === p.id} key={p.id} className={sel === p.id ? "is-active" : ""} onClick={() => onSel(p.id)}>
          {p.name}
        </button>
      ))}
    </ScrollRow>
  );
}

function Growth({ rows, stats }: { rows: StatsHistoryRow[]; stats: Stats | null }) {
  const tx = useT();
  // 番兵行（ユニーク曲数）だけを時系列に使う。延べ合計（プレイリスト横断）は二重計上になるため使わない。
  const uniqueRows = rows.filter((r) => r.playlist_id === LIB_ROW);
  const byDate = new Map<string, number>();
  for (const r of uniqueRows) byDate.set(r.date, r.count);
  const data = [...byDate.entries()].sort().map(([date, total]) => ({ date, total }));
  // total（正規のユニーク数）→ 番兵履歴の最新 → 年代分布の合計（≒ユニーク数）の順にフォールバック。
  const decadeSum = stats?.decades?.reduce((s, d) => s + d.count, 0) ?? 0;
  const current = stats?.total ?? (data.length ? data[data.length - 1].total : decadeSum || null);
  const uniqueLabel = tx("unique tracks", "ユニーク曲数");

  return (
    <div className="card">
      {current != null && (
        <div style={{ marginBottom: "var(--sp-3)" }}>
          <div className="t-small" style={{ textTransform: "uppercase", letterSpacing: "0.1em", fontWeight: 700 }}>
            {tx("Nightly-managed library (unique tracks)", "夜間管理ライブラリ（ユニーク曲数）")}
          </div>
          <div className="t-display num" style={{ fontSize: "2.4rem", marginTop: 2 }}>{current.toLocaleString()}</div>
          <div className="t-small" style={{ marginTop: 2 }}>
            {tx(
              "Deduplicated track count of the Western/Japanese playlists that sort tidies nightly. The distribution below combines Western, Japanese and 1900's, so the number differs.",
              "毎晩 sort が整える邦・洋プレイリストの重複なし曲数。下の分布は Western・Japanese・1900's を合算するため数が異なります。",
            )}
          </div>
        </div>
      )}
      {data.length === 0 ? (
        <Empty>
          {tx(
            "Unique-track history starts recording from the next nightly update (one point per night).",
            "ユニーク曲数の履歴は次回の夜間更新から記録します（毎晩1点ずつ増えます）。",
          )}
        </Empty>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={data} margin={{ left: -10, right: 8, top: 8 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" stroke={AXIS} fontSize={11} />
            <YAxis stroke={AXIS} fontSize={11} />
            <Tooltip contentStyle={TIP} labelStyle={{ color: "#fff" }} formatter={(v: number) => [v.toLocaleString(), uniqueLabel]} />
            <Line type="monotone" dataKey="total" stroke={GREEN} strokeWidth={2} dot={false} name={uniqueLabel} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ArtistBars({ group }: { group: StatsGroup | null }) {
  const tx = useT();
  const { play } = usePlayer();
  const [modal, setModal] = useState<ModalArtist | null>(null);
  if (!group || !group.artists_top.length) return <Empty>{tx("No data", "データなし")}</Empty>;
  const data = group.artists_top.slice(0, 15);
  const max = Math.max(...data.map((a) => a.count), 1);
  // 右の▶だけが再生。バー本体はアーティスト情報モーダル（自動再生しない）。
  function playArtist(a: { name: string; id?: string }) {
    if (a.id) play(`spotify:artist:${a.id}`);
    else window.open(`https://open.spotify.com/search/${encodeURIComponent(a.name)}`, "_blank", "noopener");
  }
  return (
    <div className="card">
      {data.map((a) => (
        <div className="art-row" key={a.name}>
          <button
            className="art-body"
            onClick={() => setModal({ name: a.name, count: a.count, id: a.id })}
            title={tx(`${a.name} info`, `${a.name} の情報`)}
          >
            <span className="art-name">{a.name}</span>
            <span className="art-bar"><i style={{ width: `${(a.count / max) * 100}%` }} /></span>
            <span className="art-count num">{a.count}</span>
          </button>
          <button
            className="play-btn"
            aria-label={tx(`Play ${a.name}`, `${a.name} を再生`)}
            title={tx(`Play ${a.name}`, `${a.name} を再生`)}
            onClick={() => playArtist(a)}
          >
            <PlayIcon />
          </button>
        </div>
      ))}
      {modal && <ArtistModal artist={modal} onClose={() => setModal(null)} />}
    </div>
  );
}

// search_index のトラックを「対象グループ内・その年代・古い順」に絞る（バーの数と一致させる）。
function decadeTracks(tracks: SearchTrack[], groupNames: string[] | null, decade: number): SearchTrack[] {
  return tracks
    .filter((t) => {
      const rd = t.release_date || "";
      if (rd.length < 4 || !/^\d{4}/.test(rd)) return false;
      if (Math.floor(parseInt(rd.slice(0, 4), 10) / 10) * 10 !== decade) return false;
      return groupNames === null || groupNames.some((n) => t.playlists.includes(n));
    })
    .sort((a, b) => (a.release_date || "").localeCompare(b.release_date || ""));
}

function DecadeBars(
  { group, searchTracks, groupNames }:
    { group: StatsGroup | null; searchTracks: SearchTrack[]; groupNames: string[] | null },
) {
  const tx = useT();
  const [decade, setDecade] = useState<number | null>(null);
  if (!group || !group.decades.length) return <Empty>{tx("No data", "データなし")}</Empty>;
  const data = group.decades.map((d) => ({ label: `${d.decade}s`, count: d.count, decade: d.decade }));
  const tracks = decade != null ? decadeTracks(searchTracks, groupNames, decade) : [];
  return (
    <div className="card">
      {/* 初回に「軸だけでバー0本」になる recharts のアニメ由来の描画抜けを止める（isAnimationActive=false）。 */}
      {/* 棒だけでなく、その年代の列（灰色の余白）を押しても開くよう BarChart 全体で onClick を拾う。 */}
      <div className="decade-chart">
        <ResponsiveContainer width="100%" height={200}>
          <BarChart
            data={data}
            margin={{ left: -10, right: 8 }}
            onClick={(s: { activePayload?: { payload?: { decade?: number } }[] }) => {
              const dec = s?.activePayload?.[0]?.payload?.decade;
              if (dec != null) setDecade(dec);
            }}
          >
            <XAxis dataKey="label" stroke={AXIS} fontSize={11} />
            <YAxis stroke={AXIS} fontSize={11} />
            <Tooltip contentStyle={TIP} labelStyle={{ color: "#fff" }} cursor={{ fill: "#ffffff10" }} />
            <Bar dataKey="count" fill={GREEN} radius={[4, 4, 0, 0]} name={tx("songs", "曲数")} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {decade != null && (
        <Modal title={tx(`${decade}s tracks`, `${decade}年代の曲`)} subtitle={tx(`${tracks.length} songs · oldest first`, `${tracks.length}曲 · 古い順`)} onClose={() => setDecade(null)}>
          {tracks.length === 0 ? (
            <p className="t-small" style={{ padding: "var(--sp-2) 0" }}>
              {tx(
                "This decade's track list appears after the next nightly update (release-date data is being imported).",
                "この年代の曲一覧は次回の夜間更新後に表示されます（リリース日データを取り込み中）。",
              )}
            </p>
          ) : (
            <div className="modal-list">
              {tracks.map((t) => (
                <div className="list-row" key={t.id}>
                  <span className="list-rank num">{(t.release_date || "").slice(0, 4)}</span>
                  <span className="list-main">
                    <div className="name">{t.name}</div>
                    <div className="t-small">{t.artists.join(", ")}</div>
                  </span>
                  <PlayButton uri={`spotify:track:${t.id}`} label={tx(`Play ${t.name}`, `${t.name} を再生`)} />
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}

function HeatGrid({ heat }: { heat: Heatmap | null }) {
  const tx = useT();
  const { lang } = useLang();
  const DOW = dowLabels(lang);
  if (!heat || !heat.cells.length)
    return <Empty>{tx("As listening data accumulates, day × hour trends appear here.", "聴取ログが貯まると、曜日×時間帯の傾向が出ます。")}</Empty>;
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
                  title={tx(`${d} ${h}:00: ${v} plays`, `${d} ${h}時: ${v}回`)}
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
