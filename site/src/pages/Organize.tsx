import { useState } from "react";
import { useJson } from "../lib/data";
import type { Dupes, DupeGroup, Unknown } from "../lib/types";
import { Empty, Loading, Section, Duration } from "../components/ui";
import { EmbedPlayer } from "../components/EmbedPlayer";
import { usePat } from "../lib/pat";
import { dispatchOp, runsUrl } from "../lib/github";

export function Organize() {
  const dupes = useJson<Dupes>("dupes");
  const unknown = useJson<Unknown>("unknown");
  const pat = usePat();

  return (
    <>
      {!pat && (
        <div className="auth-banner auth-banner--warn">
          閲覧のみモードです。削除・振り分けを実行するにはヘッダの「操作 OFF」から PAT を設定してください。
        </div>
      )}

      <Section title="重複・別バージョン" aside={dupes.data && <Counts d={dupes.data} />}>
        {dupes.loading ? (
          <Loading />
        ) : !dupes.data || dupes.data.groups.length === 0 ? (
          <Empty>重複は見つかっていません。きれいな状態です。</Empty>
        ) : (
          dupes.data.groups.map((g) => <GroupCard key={g.id} g={g} pat={pat} />)
        )}
      </Section>

      <Section title="判定できなかった曲">
        {unknown.loading ? (
          <Loading />
        ) : !unknown.data || unknown.data.tracks.length === 0 ? (
          <Empty>未判定の曲はありません。</Empty>
        ) : (
          unknown.data.tracks.map((t) => (
            <div className="card" key={t.id} style={{ marginBottom: "var(--sp-3)" }}>
              <div className="t-body-bold">{t.name}</div>
              <div className="t-small" style={{ marginBottom: "var(--sp-2)" }}>{t.artists.join(", ")}</div>
              <EmbedPlayer trackId={t.id} />
              <ClassifyActions trackId={t.id} pat={pat} />
            </div>
          ))
        )}
      </Section>
    </>
  );
}

function Counts({ d }: { d: Dupes }) {
  return (
    <span className="t-small">
      完全 {d.counts.A} / 同一録音 {d.counts.B} / 別バージョン {d.counts.C}
    </span>
  );
}

const TIER_LABEL: Record<string, string> = { A: "完全重複", B: "同一録音", C: "別バージョン候補" };

function GroupCard({ g, pat }: { g: DupeGroup; pat: string | null }) {
  const [keep, setKeep] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (g.tier === "A") {
    return (
      <div className="card dupe-group">
        <Header g={g} />
        <div className="t-body-bold">{g.track?.name}</div>
        <div className="t-small">{g.track?.artists.join(", ")} — {g.playlist?.name} に {g.count} 回</div>
        {g.track && <EmbedPlayer trackId={g.track.id} />}
      </div>
    );
  }

  const tracks = g.tracks ?? [];
  const remove = tracks.filter((t) => !keep.has(t.id)).map((t) => t.id);
  const canApply = !!pat && keep.size > 0 && remove.length > 0 && remove.length < tracks.length;

  async function apply() {
    setBusy(true);
    setStatus(null);
    const res = await dispatchOp(pat!, "dedupe-apply", {
      decisions: [{ group_id: g.id, keep: [...keep], remove }],
    });
    setBusy(false);
    setStatus(res.ok ? "処理中… 数分後にサイトへ反映されます。" : `失敗: ${res.message}`);
  }

  async function keepBoth() {
    setBusy(true);
    const res = await dispatchOp(pat!, "keep-apply", {
      add: [{ group_id: g.id, track_ids: tracks.map((t) => t.id) }],
      remove: [],
    });
    setBusy(false);
    setStatus(res.ok ? "「両方残す」を記録しました。" : `失敗: ${res.message}`);
  }

  return (
    <div className="card dupe-group">
      <Header g={g} />
      {tracks.map((t, i) => (
        <div className="dupe-cand" key={t.id}>
          <div className="meta">
            <label className="t-body-bold" style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={keep.has(t.id)}
                onChange={(e) => {
                  const next = new Set(keep);
                  e.target.checked ? next.add(t.id) : next.delete(t.id);
                  setKeep(next);
                }}
              />
              残す
            </label>
            <span className="name">{t.name}</span>
            {i === 0 && <span className="badge badge-b">推奨</span>}
          </div>
          <div className="t-small">
            {t.album} · {t.album_type} · {t.release_date} · <Duration ms={t.duration_ms} /> · 人気 {t.popularity ?? "—"}
            {" · "}
            {t.playlists.map((p) => p.name).join(" / ")}
          </div>
          <EmbedPlayer trackId={t.id} />
        </div>
      ))}
      <div className="dupe-actions">
        <button className="pill pill-green" disabled={!canApply || busy} onClick={apply}>
          選んだ方を残して削除
        </button>
        <button className="pill" disabled={!pat || busy} onClick={keepBoth}>
          両方残す
        </button>
        {status && (
          <span className="t-small" style={{ alignSelf: "center" }}>
            {status} <a className="muted" href={runsUrl()} target="_blank" rel="noreferrer">Actions</a>
          </span>
        )}
      </div>
    </div>
  );
}

function Header({ g }: { g: DupeGroup }) {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: "var(--sp-2)" }}>
      <span className={"badge badge-" + g.tier.toLowerCase()}>{TIER_LABEL[g.tier]}</span>
      <span className="t-small">{g.reason}</span>
    </div>
  );
}

function ClassifyActions({ trackId, pat }: { trackId: string; pat: string | null }) {
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function classify(cls: "japanese" | "western") {
    setBusy(true);
    const res = await dispatchOp(pat!, "classify-apply", { decisions: [{ track_id: trackId, class: cls }] });
    setBusy(false);
    setStatus(res.ok ? `${cls === "japanese" ? "邦楽" : "洋楽"}へ振り分け中…` : `失敗: ${res.message}`);
  }
  return (
    <div className="dupe-actions">
      <button className="pill pill-green" disabled={!pat || busy} onClick={() => classify("japanese")}>邦楽</button>
      <button className="pill" disabled={!pat || busy} onClick={() => classify("western")}>洋楽</button>
      {status && <span className="t-small" style={{ alignSelf: "center" }}>{status}</span>}
    </div>
  );
}
