import { useEffect, useState } from "react";
import { useJson } from "../lib/data";
import type { Dupes, DupeGroup, Unknown, UndoIndex } from "../lib/types";
import { Empty, Loading, Section, Duration } from "../components/ui";
import { usePat } from "../lib/pat";
import { dispatchOp, runsUrl } from "../lib/github";
import { clearProcessing, markProcessing, stuckIds, useProcessing } from "../lib/processing";
import { PlayButton } from "../lib/player";

export function Organize() {
  const dupes = useJson<Dupes>("dupes");
  const unknown = useJson<Unknown>("unknown");
  const undoIdx = useJson<UndoIndex>("undo_index");
  const pat = usePat();
  const processing = useProcessing();
  const anyProcessing = Object.keys(processing).length > 0; // M-2: 実行中は他の dispatch を止める
  const [tab, setTab] = useState<"dupes" | "unknown">("dupes");

  // 処理中の解消（M2）: データ更新で対象が消えた／30分経っても反映されない（M-1）ものをクリア
  useEffect(() => {
    const toClear: string[] = [];
    if (dupes.data) {
      const present = new Set(dupes.data.groups.map((g) => g.id));
      toClear.push(...Object.keys(processing).filter((id) => id.startsWith("g-") && !present.has(id)));
    }
    if (unknown.data) {
      const present = new Set(unknown.data.tracks.map((t) => t.id));
      toClear.push(
        ...Object.keys(processing).filter((id) => !id.startsWith("g-") && !id.startsWith("undo-") && !present.has(id)),
      );
    }
    if (undoIdx.data) {
      const present = new Set(undoIdx.data.entries.map((e) => `undo-${e.id}`));
      toClear.push(...Object.keys(processing).filter((id) => id.startsWith("undo-") && !present.has(id)));
    }
    toClear.push(...stuckIds(processing, Date.now()));
    const uniq = [...new Set(toClear)];
    if (uniq.length) clearProcessing(uniq);
  }, [dupes.data, unknown.data, undoIdx.data, processing]);

  // タイムアウト解消を定期的に走らせる（M-1: 反映が来なくても30分で操作可能に戻す）
  useEffect(() => {
    const id = setInterval(() => {
      const stuck = stuckIds(processing, Date.now());
      if (stuck.length) clearProcessing(stuck);
    }, 60_000);
    return () => clearInterval(id);
  }, [processing]);

  const dupeCount = dupes.data?.groups.length ?? 0;
  const unknownCount = unknown.data?.tracks.length ?? 0;

  return (
    <>
      {!pat && (
        <div className="auth-banner auth-banner--warn auth-banner--compact viewonly-note">
          <span>👁 閲覧のみ</span>
          <span className="muted">— 削除・振り分けはヘッダ「操作 OFF」から有効化</span>
        </div>
      )}

      <UndoSection pat={pat} anyProcessing={anyProcessing} processing={processing} />

      <div className="seg" role="tablist" aria-label="整理の種類">
        <button role="tab" aria-selected={tab === "dupes"} className={tab === "dupes" ? "is-active" : ""} onClick={() => setTab("dupes")}>
          重複{dupeCount > 0 && <span className="seg-count">{dupeCount}</span>}
        </button>
        <button role="tab" aria-selected={tab === "unknown"} className={tab === "unknown" ? "is-active" : ""} onClick={() => setTab("unknown")}>
          判定できなかった曲{unknownCount > 0 && <span className="seg-count">{unknownCount}</span>}
        </button>
      </div>

      {tab === "dupes" ? (
        <Section title="重複・別バージョン" aside={dupes.data && <Counts d={dupes.data} />}>
          {dupes.loading ? (
            <Loading />
          ) : !dupes.data || dupeCount === 0 ? (
            <Empty>重複は見つかっていません。きれいな状態です。</Empty>
          ) : (
            dupes.data.groups.map((g) => (
              <GroupCard
                key={g.id}
                g={g}
                pat={pat}
                processing={!!processing[g.id]}
                blocked={anyProcessing && !processing[g.id]}
              />
            ))
          )}
        </Section>
      ) : (
        <Section title="判定できなかった曲">
          {unknown.loading ? (
            <Loading />
          ) : !unknown.data || unknownCount === 0 ? (
            <Empty>未判定の曲はありません。</Empty>
          ) : (
            unknown.data.tracks.map((t) => (
              <div className="card" key={t.id} style={{ marginBottom: "var(--sp-3)" }}>
                <div className="dupe-cand" style={{ background: "transparent", padding: 0, marginBottom: "var(--sp-2)" }}>
                  <div className="cand-main">
                    <div className="cand-name"><span className="txt">{t.name}</span></div>
                    <div className="cand-meta">{t.artists.join(", ")}</div>
                  </div>
                  <PlayButton uri={`spotify:track:${t.id}`} />
                </div>
                <ClassifyActions
                  trackId={t.id}
                  pat={pat}
                  processing={!!processing[t.id]}
                  blocked={anyProcessing && !processing[t.id]}
                />
              </div>
            ))
          )}
        </Section>
      )}
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

// 判定理由スラッグ（データ由来）を日本語に。未知の値はそのまま出す。
const REASON_LABEL: Record<string, string> = {
  "same-id-in-playlist": "同じ曲がプレイリスト内に重複",
  isrc: "録音が同一（ISRC 一致）",
  title: "曲名が一致（別バージョンの可能性）",
};
function humanizeReason(reason: string): string {
  return REASON_LABEL[reason] ?? reason;
}

function GroupCard(
  { g, pat, processing, blocked }: { g: DupeGroup; pat: string | null; processing: boolean; blocked: boolean },
) {
  const [keep, setKeep] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (g.tier === "A") {
    return (
      <div className="card dupe-group">
        <Header g={g} />
        <div className="dupe-cand">
          <div className="cand-main">
            <div className="cand-name"><span className="txt">{g.track?.name}</span></div>
            <div className="cand-meta">{g.track?.artists.join(", ")} — {g.playlist?.name} に {g.count} 回</div>
          </div>
          {g.track && <PlayButton uri={`spotify:track:${g.track.id}`} />}
        </div>
        <div className="t-small" style={{ marginTop: "var(--sp-2)" }}>
          同じ曲がこのプレイリストに {g.count} 回入っています。片方だけ残す操作はここにはありません
          （全消しを避けるため）。余分な1つは Spotify アプリ側で削除してください。
        </div>
      </div>
    );
  }

  const tracks = g.tracks ?? [];
  const remove = tracks.filter((t) => !keep.has(t.id)).map((t) => t.id);
  const canApply =
    !!pat && !processing && !blocked && keep.size > 0 && remove.length > 0 && remove.length < tracks.length;

  async function apply() {
    setBusy(true);
    setStatus(null);
    const res = await dispatchOp(pat!, "dedupe-apply", {
      decisions: [{ group_id: g.id, keep: [...keep], remove }],
    });
    setBusy(false);
    if (res.ok) markProcessing(g.id);
    setStatus(res.ok ? "処理中… 数分後にサイトへ反映されます。" : `失敗: ${res.message}`);
  }

  async function keepBoth() {
    setBusy(true);
    const res = await dispatchOp(pat!, "keep-apply", {
      add: [{ group_id: g.id, track_ids: tracks.map((t) => t.id) }],
      remove: [],
    });
    setBusy(false);
    if (res.ok) markProcessing(g.id);
    setStatus(res.ok ? "「両方残す」を記録しました。" : `失敗: ${res.message}`);
  }

  return (
    <div className="card dupe-group" style={processing ? { opacity: 0.6 } : undefined}>
      <Header g={g} />
      {processing && (
        <div className="t-small" style={{ marginBottom: "var(--sp-2)" }}>
          処理中… 反映まで数分（30分で自動解除）{" "}
          <a className="muted" href={runsUrl()} target="_blank" rel="noreferrer">Actions で確認</a>
        </div>
      )}
      {tracks.map((t, i) => (
        <div className={"dupe-cand" + (keep.has(t.id) ? " is-keep" : "")} key={t.id}>
          <label className="keep-toggle">
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
          <div className="cand-main">
            <div className="cand-name">
              <span className="txt">{t.name}</span>
              {i === 0 && <span className="badge badge-b">推奨</span>}
            </div>
            <div className="cand-meta">
              {t.album} · {t.release_date} · <Duration ms={t.duration_ms} /> · 人気 {t.popularity ?? "—"}
            </div>
          </div>
          <PlayButton uri={`spotify:track:${t.id}`} />
        </div>
      ))}
      <div className="dupe-actions">
        <button className="pill pill-green" disabled={!canApply || busy} onClick={apply}>
          選んだ方を残して削除
        </button>
        <button className="pill" disabled={!pat || busy || processing || blocked} onClick={keepBoth}>
          両方残す
        </button>
        {!pat && <span className="action-hint">🔒 操作トークン未設定で実行できません</span>}
        {pat && !processing && keep.size === 0 && (
          <span className="action-hint">残す方にチェックを付けてください</span>
        )}
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
    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: "var(--sp-3)" }}>
      <span className={"badge badge-" + g.tier.toLowerCase()}>{TIER_LABEL[g.tier]}</span>
      <span className="t-small">{humanizeReason(g.reason)}</span>
    </div>
  );
}

function UndoSection(
  { pat, anyProcessing, processing }:
    { pat: string | null; anyProcessing: boolean; processing: Record<string, string> },
) {
  const undo = useJson<UndoIndex>("undo_index");
  const [status, setStatus] = useState<Record<string, string>>({});
  const entries = undo.data?.entries ?? [];
  if (!entries.length) return null;

  async function run(id: string) {
    setStatus((s) => ({ ...s, [id]: "取り消し中…" }));
    const res = await dispatchOp(pat!, "undo", { undo_id: id });
    if (res.ok) markProcessing(`undo-${id}`); // M-A: undo も直列化の枠内に入れる
    setStatus((s) => ({ ...s, [id]: res.ok ? "取り消し中… 数分後に反映" : `失敗: ${res.message}` }));
  }

  return (
    <Section title="最近の操作（取り消し可）">
      <div className="card">
        {entries.slice(0, 8).map((e) => {
          const busy = !!processing[`undo-${e.id}`];
          return (
            <div className="list-row" key={e.id}>
              <span className="list-main">
                <div className="name">{e.op} — {e.count}曲</div>
                <div className="t-small">{e.created_at?.slice(0, 16).replace("T", " ")} {e.tracks.slice(0, 2).join(", ")}</div>
              </span>
              {e.op === "dedupe-apply" && (
                <button
                  className="pill"
                  disabled={!pat || busy || (anyProcessing && !busy)}
                  onClick={() => run(e.id)}
                >
                  取り消し
                </button>
              )}
              {busy ? <span className="t-small">取り消し中…</span> : status[e.id] && <span className="t-small">{status[e.id]}</span>}
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function ClassifyActions(
  { trackId, pat, processing, blocked }: { trackId: string; pat: string | null; processing: boolean; blocked: boolean },
) {
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const disabled = !pat || busy || processing || blocked;
  async function classify(cls: "japanese" | "western") {
    setBusy(true);
    const res = await dispatchOp(pat!, "classify-apply", { decisions: [{ track_id: trackId, class: cls }] });
    setBusy(false);
    if (res.ok) markProcessing(trackId); // L-6: 連打で2本目が「unknown に無い」失敗 Issue を防ぐ
    setStatus(res.ok ? `${cls === "japanese" ? "邦楽" : "洋楽"}へ振り分け中…` : `失敗: ${res.message}`);
  }
  return (
    <div className="dupe-actions">
      <button className="pill pill-green" disabled={disabled} onClick={() => classify("japanese")}>邦楽</button>
      <button className="pill" disabled={disabled} onClick={() => classify("western")}>洋楽</button>
      {!pat && <span className="action-hint">🔒 操作トークン未設定で実行できません</span>}
      {(processing || status) && (
        <span className="t-small" style={{ alignSelf: "center" }}>{processing ? "振り分け中…" : status}</span>
      )}
    </div>
  );
}
