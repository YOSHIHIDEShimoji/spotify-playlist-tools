import { useEffect, useState } from "react";
import { useJson } from "../lib/data";
import type { Dupes, DupeGroup, KeepGroup, KeepIndex, Unknown, UndoIndex } from "../lib/types";
import { Empty, Loading, Section, Duration } from "../components/ui";
import { usePat } from "../lib/pat";
import { dispatchOp, runsUrl } from "../lib/github";
import { clearProcessing, markProcessing, stuckIds, useProcessing } from "../lib/processing";
import { PlayButton } from "../lib/player";

export function Organize() {
  const dupes = useJson<Dupes>("dupes");
  const unknown = useJson<Unknown>("unknown");
  const undoIdx = useJson<UndoIndex>("undo_index");
  const keep = useJson<KeepIndex>("dedupe_keep");
  const pat = usePat();
  const processing = useProcessing();
  const anyProcessing = Object.keys(processing).length > 0; // M-2: 実行中は他の dispatch を止める
  const [tab, setTab] = useState<"dupes" | "unknown" | "keep">("dupes");

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
  const keepCount = keep.data?.groups.length ?? 0;

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
        <button role="tab" aria-selected={tab === "keep"} className={tab === "keep" ? "is-active" : ""} onClick={() => setTab("keep")}>
          保留{keepCount > 0 && <span className="seg-count">{keepCount}</span>}
        </button>
      </div>

      {tab === "keep" ? (
        <Section title="保留中（両方残す）">
          {keep.loading ? (
            <Loading />
          ) : keepCount === 0 ? (
            <Empty>「両方残す」にした重複はありません。ここに移すと、いつでも重複チェックに戻せます。</Empty>
          ) : (
            (keep.data?.groups ?? []).map((g) => <KeepCard key={g.group_id} g={g} pat={pat} />)
          )}
        </Section>
      ) : tab === "dupes" ? (
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
  const [del, setDel] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (g.tier === "A") {
    return <TierACard g={g} pat={pat} processing={processing} blocked={blocked} />;
  }

  const tracks = g.tracks ?? [];
  // チェックした曲を「削除対象」にする（グレーアウト）。未チェックは残る。全消しは禁止（1曲は残す）。
  const remove = [...del];
  const canApply = !!pat && !processing && !blocked && del.size > 0 && del.size < tracks.length;
  const tooMany = del.size > 0 && del.size >= tracks.length;

  async function apply() {
    setBusy(true);
    setStatus(null);
    const keep = tracks.filter((t) => !del.has(t.id)).map((t) => t.id);
    const res = await dispatchOp(pat!, "dedupe-apply", {
      decisions: [{ group_id: g.id, keep, remove }],
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
        <label className={"dupe-cand cand-pick" + (del.has(t.id) ? " is-del" : "")} key={t.id}>
          <input
            type="checkbox"
            className="cand-check"
            aria-label={`${t.name} を削除対象にする`}
            checked={del.has(t.id)}
            onChange={(e) => {
              const next = new Set(del);
              e.target.checked ? next.add(t.id) : next.delete(t.id);
              setDel(next);
            }}
          />
          <Art image={t.image} />
          <div className="cand-main">
            <div className="cand-name">
              <span className="txt">{t.name}</span>
              {i === 0 && <span className="badge badge-b">推奨で残す</span>}
            </div>
            <div className="cand-meta">
              {t.album} · {t.release_date} · <Duration ms={t.duration_ms} /> · 人気 {t.popularity ?? "—"}
            </div>
          </div>
          <PlayButton uri={`spotify:track:${t.id}`} />
        </label>
      ))}
      <div className="dupe-actions">
        <button className="pill pill-green" disabled={!canApply || busy} onClick={apply}>
          選んだ曲を削除{del.size > 0 && `（${del.size}）`}
        </button>
        <button className="pill" disabled={!pat || busy || processing || blocked} onClick={keepBoth}>
          両方残す
        </button>
        {!pat && <span className="action-hint">🔒 操作トークン未設定で実行できません</span>}
        {pat && !processing && tooMany && (
          <span className="action-hint">全部は削除できません（1曲は残してください）</span>
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

// アルバムのサムネイル。新データにしか image が無いので、無いときはプレースホルダを出す。
function Art({ image }: { image?: string | null }) {
  return image ? (
    <img className="cand-art" src={image} alt="" loading="lazy" width={44} height={44} />
  ) : (
    <span className="cand-art cand-art--ph" aria-hidden />
  );
}

// Tier A（同一プレイリスト内に同じ曲が複数）: 1つだけ残して余分を削除する。
function TierACard(
  { g, pat, processing, blocked }: { g: DupeGroup; pat: string | null; processing: boolean; blocked: boolean },
) {
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const disabled = !pat || busy || processing || blocked;

  async function trim() {
    setBusy(true);
    setStatus(null);
    const res = await dispatchOp(pat!, "dedupe-trim", { group_id: g.id });
    setBusy(false);
    if (res.ok) markProcessing(g.id);
    setStatus(res.ok ? "余分な1つを削除中… 数分後に反映されます。" : `失敗: ${res.message}`);
  }

  return (
    <div className="card dupe-group" style={processing ? { opacity: 0.6 } : undefined}>
      <Header g={g} />
      <div className="dupe-cand">
        <Art image={g.track?.image} />
        <div className="cand-main">
          <div className="cand-name"><span className="txt">{g.track?.name}</span></div>
          <div className="cand-meta">{g.track?.artists.join(", ")} — {g.playlist?.name} に {g.count} 回</div>
        </div>
        {g.track && <PlayButton uri={`spotify:track:${g.track.id}`} />}
      </div>
      <p className="t-small" style={{ margin: "var(--sp-2) 0 0" }}>
        同じ曲がこのプレイリストに {g.count} 回入っています。1つだけ残して余分を削除します（位置指定なので他の曲には触れません・取り消し可）。
      </p>
      <div className="dupe-actions">
        <button className="pill pill-green" disabled={disabled} onClick={trim}>
          余分を削除（1つ残す）
        </button>
        {!pat && <span className="action-hint">🔒 操作トークン未設定で実行できません</span>}
        {(processing || status) && (
          <span className="t-small" style={{ alignSelf: "center" }}>{processing ? "処理中…" : status}</span>
        )}
      </div>
    </div>
  );
}

// 「両方残す」で保留にした重複。ここから重複チェックに戻せる（keep-apply の remove → 再スキャンで復活）。
function KeepCard({ g, pat }: { g: KeepGroup; pat: string | null }) {
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const tracks = g.tracks ?? [];

  async function restore() {
    setBusy(true);
    setStatus(null);
    const res = await dispatchOp(pat!, "keep-apply", { add: [], remove: [g.group_id] });
    setBusy(false);
    setStatus(res.ok ? "重複チェックに戻しています… 数分後に反映されます。" : `失敗: ${res.message}`);
  }

  return (
    <div className="card dupe-group">
      <div className="t-small" style={{ marginBottom: "var(--sp-2)" }}>
        両方残す{g.decided_at ? ` · ${g.decided_at}` : ""}
      </div>
      {tracks.length ? (
        tracks.map((t) => (
          <div className="dupe-cand" key={t.id}>
            <Art image={t.image} />
            <div className="cand-main">
              <div className="cand-name"><span className="txt">{t.name}</span></div>
              <div className="cand-meta">{t.artists.join(", ")}</div>
            </div>
            <PlayButton uri={`spotify:track:${t.id}`} />
          </div>
        ))
      ) : (
        <div className="t-small" style={{ marginBottom: "var(--sp-2)" }}>曲 {g.track_ids.length} 件（詳細は次回更新後に表示）</div>
      )}
      <div className="dupe-actions">
        <button className="pill pill-green" disabled={!pat || busy} onClick={restore}>
          重複チェックに戻す
        </button>
        {!pat && <span className="action-hint">🔒 操作トークン未設定で実行できません</span>}
        {status && <span className="t-small" style={{ alignSelf: "center" }}>{status}</span>}
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
