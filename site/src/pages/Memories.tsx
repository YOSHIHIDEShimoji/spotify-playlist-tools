import { useJson } from "../lib/data";
import type { ArchiveWeekly } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";
import { EmbedPlayer } from "../components/EmbedPlayer";

// 現在の ISO 週（JST）を "YYYY-Www" で返す。1年前の同じ週を archive_weekly から探す。
function isoWeekLabel(d: Date): string {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

export function Memories() {
  const weekly = useJson<ArchiveWeekly>("archive_weekly");

  const lastYear = new Date();
  lastYear.setFullYear(lastYear.getFullYear() - 1);
  const targetWeek = isoWeekLabel(lastYear);

  const match = weekly.data?.weeks.find((w) => w.iso_week === targetWeek);
  const recent = [...(weekly.data?.weeks ?? [])].reverse().slice(0, 6);

  return (
    <>
      <Section title={`1年前の今週（${targetWeek}）`}>
        {weekly.loading ? (
          <Loading />
        ) : !match ? (
          <Empty>該当する週のデータがまだありません（Top50 アーカイブが1年分たまると出ます）。</Empty>
        ) : (
          <div className="card">
            {match.tracks.map((t) => (
              <div className="list-row" key={t.id}>
                <span className="list-main">
                  <div className="name">{t.name}</div>
                  <div className="t-small">{t.artists.join(", ")}</div>
                </span>
              </div>
            ))}
            {match.tracks[0] && <EmbedPlayer trackId={match.tracks[0].id} />}
          </div>
        )}
      </Section>

      <Section title="最近アーカイブ入りした週">
        {weekly.loading ? (
          <Loading />
        ) : recent.length === 0 ? (
          <Empty>まだアーカイブ週がありません。</Empty>
        ) : (
          recent.map((w) => (
            <div className="card" key={w.iso_week} style={{ marginBottom: "var(--sp-3)" }}>
              <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>{w.iso_week}（{w.tracks.length}曲）</div>
              {w.tracks.slice(0, 8).map((t) => (
                <div className="list-row" key={t.id}>
                  <span className="list-main">
                    <div className="name">{t.name}</div>
                    <div className="t-small">{t.artists.join(", ")}</div>
                  </span>
                </div>
              ))}
            </div>
          ))
        )}
      </Section>

      <Section title="月間 Wrapped">
        <Empty>毎月末に自動生成されます（今月のTop曲・新規追加・ピーク時間帯）。</Empty>
      </Section>
    </>
  );
}
