import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useJson } from "../lib/data";
import type { Dupes, DupeGroup, KeepGroup, KeepIndex, SearchIndex, SearchTrack, Unknown, UndoIndex } from "../lib/types";
import { Empty, Loading, Section, Duration } from "../components/ui";
import { Modal } from "../components/Modal";
import { usePat } from "../lib/pat";
import { dispatchOp, runsUrl } from "../lib/github";
import { clearProcessing, markProcessing, stuckIds, useProcessing } from "../lib/processing";
import { PlayButton } from "../lib/player";
import { clearQueue, dequeue, enqueue, SEND_DELAY_MS, snapshot, toPayloads, useQueue } from "../lib/queue";
import type { Pending } from "../lib/queue";
import { useT } from "../lib/i18n";

type Tx = (en: string, ja: string) => string;
const noPatHint = (tx: Tx) => tx("🔒 No operation token set", "🔒 操作トークン未設定で実行できません");
const failMsg = (tx: Tx, m: string) => tx(`Failed: ${m}`, `失敗: ${m}`);

export function Organize() {
  const tx = useT();
  const dupes = useJson<Dupes>("dupes");
  const unknown = useJson<Unknown>("unknown");
  const undoIdx = useJson<UndoIndex>("undo_index");
  const keep = useJson<KeepIndex>("dedupe_keep");
  const pat = usePat();
  const processing = useProcessing();
  const anyProcessing = Object.keys(processing).length > 0; // M-2: 実行中は他の dispatch を止める
  const [tab, setTab] = useState<"dupes" | "unknown" | "keep">("dupes");
  // 各グループで「残す1曲」の選択（group_id → track_id）。未設定は各グループの推奨(先頭)を既定に。
  const [keepSel, setKeepSel] = useState<Record<string, string>>({});
  const [confirmBulk, setConfirmBulk] = useState(false);
  const keepFor = (g: DupeGroup) => keepSel[g.id] ?? g.tracks?.[0]?.id ?? "";

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

  // 一括対象＝B/C グループ（Tier A の trim は別操作）で、まだ処理中でないもの。
  const bulkGroups = (dupes.data?.groups ?? []).filter(
    (g) => g.tier !== "A" && (g.tracks?.length ?? 0) >= 2 && !processing[g.id],
  );

  async function applyBulk() {
    const decisions = bulkGroups
      .map((g) => {
        const kid = keepFor(g);
        return { group_id: g.id, keep: [kid], remove: (g.tracks ?? []).filter((t) => t.id !== kid).map((t) => t.id) };
      })
      .filter((d) => d.remove.length > 0);
    if (!decisions.length || !pat) return;
    const res = await dispatchOp(pat, "dedupe-apply", { decisions });
    if (res.ok) decisions.forEach((d) => markProcessing(d.group_id));
    setConfirmBulk(false);
  }

  // ── 決定キュー（issue #5）──
  // ボタンを押した瞬間はローカルに積むだけ。最後の操作から SEND_DELAY_MS 経ったら
  // まとめて1回 dispatch する。連打しても待たされず、送信前ならいつでも取り消せる。
  const queued = useQueue();
  const queuedIds = useMemo(() => new Set(queued.map((q) => q.groupId)), [queued]);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const sendQueue = useCallback(async () => {
    const items = snapshot();
    if (!items.length || !pat) return;
    setSending(true);
    setSendError(null);
    const { decisions, keeps, classify } = toPayloads(items);
    // op ごとに1回ずつ。同じ op を複数回に分けると site-ops の直列化待ちで後続が
    // 追い出されるため、必ず1回にまとめる。
    const sends: { op: string; payload: unknown; ids: string[] }[] = [];
    if (decisions.length) sends.push({ op: "dedupe-apply", payload: { decisions }, ids: decisions.map((d) => d.group_id) });
    if (keeps.length) sends.push({ op: "keep-apply", payload: { add: keeps, remove: [] }, ids: keeps.map((k) => k.group_id) });
    if (classify.length) sends.push({ op: "classify-apply", payload: { decisions: classify }, ids: classify.map((c) => c.track_id) });

    let failed: string | null = null;
    for (const s of sends) {
      const res = await dispatchOp(pat, s.op, s.payload);
      if (res.ok) {
        s.ids.forEach(markProcessing);
        // 送れたぶんはキューから外す。残したまま再送すると、既に適用済みの決定を
        // もう一度送ることになり site-ops 側が「対象が無い」で失敗する。
        s.ids.forEach(dequeue);
      } else {
        failed = res.message;
      }
    }
    setSending(false);
    setSendError(failed);
  }, [pat]);

  useEffect(() => {
    // 失敗が残っている間は自動送信しない。ここを見ないと、PAT 失効のような恒久的な失敗で
    // 8秒ごとに dispatch を投げ続ける無限リトライになる。復帰は「今すぐ送信」の明示操作で行う。
    if (!queued.length || !pat || sending || sendError) return;
    const id = window.setTimeout(() => { void sendQueue(); }, SEND_DELAY_MS);
    return () => window.clearTimeout(id); // 押すたびに猶予がリセットされる
  }, [queued, pat, sending, sendError, sendQueue]);

  return (
    <>
      {!pat && (
        <div className="auth-banner auth-banner--warn auth-banner--compact viewonly-note">
          <span>{tx("👁 View only", "👁 閲覧のみ")}</span>
          <span className="muted">{tx("— enable delete/sort from “Ops OFF” in the header", "— 削除・振り分けはヘッダ「操作 OFF」から有効化")}</span>
        </div>
      )}

      <UndoSection pat={pat} anyProcessing={anyProcessing} processing={processing} />

      <div className="seg" role="tablist" aria-label={tx("Organize type", "整理の種類")}>
        <button role="tab" aria-selected={tab === "dupes"} className={tab === "dupes" ? "is-active" : ""} onClick={() => setTab("dupes")}>
          {tx("Duplicates", "重複")}{dupeCount > 0 && <span className="seg-count">{dupeCount}</span>}
        </button>
        <button role="tab" aria-selected={tab === "unknown"} className={tab === "unknown" ? "is-active" : ""} onClick={() => setTab("unknown")}>
          {tx("Unclassified", "判定できなかった曲")}{unknownCount > 0 && <span className="seg-count">{unknownCount}</span>}
        </button>
        <button role="tab" aria-selected={tab === "keep"} className={tab === "keep" ? "is-active" : ""} onClick={() => setTab("keep")}>
          {tx("On hold", "保留")}{keepCount > 0 && <span className="seg-count">{keepCount}</span>}
        </button>
      </div>

      {tab === "keep" ? (
        <Section title={tx("On hold (keep both)", "保留中（両方残す）")}>
          {keep.loading ? (
            <Loading />
          ) : keepCount === 0 ? (
            <Empty>{tx("No duplicates set to “keep both”. Items moved here can always be sent back to the dupe check.", "「両方残す」にした重複はありません。ここに移すと、いつでも重複チェックに戻せます。")}</Empty>
          ) : (
            <KeepSection groups={keep.data!.groups} pat={pat} />
          )}
        </Section>
      ) : tab === "dupes" ? (
        <Section title={tx("Duplicates & alternate versions", "重複・別バージョン")} aside={dupes.data && <Counts d={dupes.data} />}>
          {dupes.loading ? (
            <Loading />
          ) : !dupes.data || dupeCount === 0 ? (
            <Empty>{tx("No duplicates found. All clean.", "重複は見つかっていません。きれいな状態です。")}</Empty>
          ) : (
            <>
              {pat && bulkGroups.length > 0 && (
                <div className="bulk-bar">
                  <button className="pill pill-green" disabled={anyProcessing} onClick={() => setConfirmBulk(true)}>
                    {tx(`Apply selection to all (${bulkGroups.length})`, `選択を一括で反映（${bulkGroups.length}）`)}
                  </button>
                  <span className="t-small muted">
                    {tx("Keeps the selected track in each group and deletes the rest.", "各グループで選んだ1曲を残し、他を削除します。")}
                  </span>
                </div>
              )}
              {dupes.data.groups.map((g) => (
                <GroupCard
                  key={g.id}
                  g={g}
                  pat={pat}
                  processing={!!processing[g.id]}
                  blocked={anyProcessing && !processing[g.id]}
                  queued={queuedIds.has(g.id)}
                  keepId={keepFor(g)}
                  onKeep={(id) => setKeepSel((s) => ({ ...s, [g.id]: id }))}
                />
              ))}
            </>
          )}
        </Section>
      ) : (
        <Section title={tx("Unclassified tracks", "判定できなかった曲")}>
          {unknown.loading ? (
            <Loading />
          ) : !unknown.data || unknownCount === 0 ? (
            <Empty>{tx("No unclassified tracks.", "未判定の曲はありません。")}</Empty>
          ) : (
            unknown.data.tracks.map((t) => (
              <div className="card" key={t.id} style={{ marginBottom: "var(--sp-3)" }}>
                <div className="dupe-cand" style={{ background: "transparent", padding: 0, marginBottom: "var(--sp-2)" }}>
                  <div className="cand-main">
                    <div className="cand-name"><span className="txt">{t.name}</span></div>
                    <div className="cand-meta">{t.artists.join(", ")}</div>
                  </div>
                  <PlayButton uri={`spotify:track:${t.id}`} label={tx(`Play ${t.name}`, `${t.name} を再生`)} />
                </div>
                <ClassifyActions
                  trackId={t.id}
                  name={t.name}
                  pat={pat}
                  processing={!!processing[t.id]}
                  queued={queuedIds.has(t.id)}
                />
              </div>
            ))
          )}
        </Section>
      )}

      {confirmBulk && (
        <BulkConfirm groups={bulkGroups} keepFor={keepFor} onCancel={() => setConfirmBulk(false)} onApply={applyBulk} />
      )}

      {queued.length > 0 && (
        <QueueBar items={queued} sending={sending} error={sendError}
          onSendNow={() => void sendQueue()} onCancelAll={clearQueue} />
      )}
    </>
  );
}

/** 送信待ちの決定をまとめて出すバー。送信までのカウントダウンと取り消しをここに集約する。 */
function QueueBar(
  { items, sending, error, onSendNow, onCancelAll }:
    { items: Pending[]; sending: boolean; error: string | null; onSendNow: () => void; onCancelAll: () => void },
) {
  const tx = useT();
  const [left, setLeft] = useState(Math.ceil(SEND_DELAY_MS / 1000));

  // キューが変わるたびに猶予を数え直す（親のタイマーと同じ起点にする）
  useEffect(() => {
    setLeft(Math.ceil(SEND_DELAY_MS / 1000));
    const id = window.setInterval(() => setLeft((s) => (s > 0 ? s - 1 : 0)), 1000);
    return () => window.clearInterval(id);
  }, [items]);

  const del = items.filter((i) => i.kind === "delete").length;
  const keep = items.filter((i) => i.kind === "keep-both").length;
  const cls = items.filter((i) => i.kind === "classify").length;
  const parts = [
    del > 0 ? tx(`${del} delete`, `削除 ${del}`) : "",
    keep > 0 ? tx(`${keep} keep both`, `両方残す ${keep}`) : "",
    cls > 0 ? tx(`${cls} sort`, `振り分け ${cls}`) : "",
  ].filter(Boolean);

  return (
    <div className="queue-bar">
      <div className="queue-main">
        <div className="t-body-bold">
          {tx(`${items.length} pending`, `${items.length}件を送信待ち`)}
          <span className="t-small muted"> · {parts.join(" / ")}</span>
        </div>
        <div className="t-small">
          {error
            ? tx(`Failed: ${error} — press Send now to retry.`, `失敗: ${error} — 「今すぐ送信」で再試行できます。`)
            : sending
              ? tx("Sending…", "送信中…")
              : tx(`Sending in ${left}s — you can keep deciding or cancel.`, `${left}秒後に送信します。続けて決めても、取り消してもかまいません。`)}
        </div>
      </div>
      <button className="pill pill-green" disabled={sending} onClick={onSendNow}>
        {tx("Send now", "今すぐ送信")}
      </button>
      <button className="pill" disabled={sending} onClick={onCancelAll}>
        {tx("Cancel all", "すべて取り消し")}
      </button>
    </div>
  );
}

// 一括適用の確認モーダル。各グループの「保持／削除」を並べて見せてから実行する。
function BulkConfirm(
  { groups, keepFor, onCancel, onApply }:
    { groups: DupeGroup[]; keepFor: (g: DupeGroup) => string; onCancel: () => void; onApply: () => void | Promise<void> },
) {
  const tx = useT();
  const [busy, setBusy] = useState(false);
  const delTotal = groups.reduce((n, g) => n + Math.max(0, (g.tracks?.length ?? 0) - 1), 0);
  async function go() {
    setBusy(true);
    await onApply();
    setBusy(false);
  }
  return (
    <Modal
      title={tx("Apply selection to all groups?", "全グループに選択を反映しますか？")}
      subtitle={tx(`${groups.length} groups · keep 1 each, delete ${delTotal}`, `${groups.length} グループ · 各1曲残して ${delTotal} 曲を削除`)}
      onClose={onCancel}
      footer={
        <>
          <button className="pill pill-green" disabled={busy} onClick={go}>
            {tx(`Delete ${delTotal} tracks`, `${delTotal} 曲を削除`)}
          </button>
          <button className="pill" disabled={busy} onClick={onCancel}>{tx("Cancel", "キャンセル")}</button>
        </>
      }
    >
      <div className="modal-list">
        {groups.map((g) => {
          const kid = keepFor(g);
          const tracks = g.tracks ?? [];
          const keepTrack = tracks.find((t) => t.id === kid);
          const del = tracks.filter((t) => t.id !== kid);
          return (
            <div className="keep-preview" key={g.id}>
              <div className="kp-keep">✓ {tx("Keep", "保持")}: {keepTrack?.name ?? kid}</div>
              <div className="kp-del">✕ {tx("Delete", "削除")}: {del.map((t) => t.name).join(", ")}</div>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}

function Counts({ d }: { d: Dupes }) {
  const tx = useT();
  return (
    <span className="t-small">
      {tx("Exact", "完全")} {d.counts.A} / {tx("Same recording", "同一録音")} {d.counts.B} / {tx("Alt version", "別バージョン")} {d.counts.C}
    </span>
  );
}

const TIER_LABEL: Record<string, { en: string; ja: string }> = {
  A: { en: "Exact duplicate", ja: "完全重複" },
  B: { en: "Same recording", ja: "同一録音" },
  C: { en: "Possible alt version", ja: "別バージョン候補" },
};

// 判定理由スラッグ（データ由来）を表示用に。未知の値はそのまま出す。
const REASON_LABEL: Record<string, { en: string; ja: string }> = {
  "same-id-in-playlist": { en: "Same track appears twice in the playlist", ja: "同じ曲がプレイリスト内に重複" },
  isrc: { en: "Same recording (ISRC match)", ja: "録音が同一（ISRC 一致）" },
  title: { en: "Title matches (possibly a different version)", ja: "曲名が一致（別バージョンの可能性）" },
};

// 残す1曲を選ぶラジオ行。選んだ曲＝緑チェック＋緑枠、選ばれなかった曲＝赤枠。
function KeepRow(
  { t, i, radioName, kept, onKeep, meta }:
    { t: { id: string; name: string; image?: string | null }; i: number; radioName: string; kept: boolean; onKeep: () => void; meta: ReactNode },
) {
  const tx = useT();
  return (
    <label className={"dupe-cand cand-pick" + (kept ? " is-keep" : " is-del")}>
      <input
        type="radio"
        className="keep-radio"
        name={radioName}
        aria-label={tx(`Keep ${t.name}`, `${t.name} を残す`)}
        checked={kept}
        onChange={onKeep}
      />
      <Art image={t.image} />
      <div className="cand-main">
        <div className="cand-name">
          <span className="txt">{t.name}</span>
          {i === 0 && <span className="badge badge-b">{tx("Recommended", "推奨")}</span>}
          <span className={"cand-tag" + (kept ? "" : " cand-tag--del")}>{kept ? tx("Keep", "残す") : tx("Delete", "削除")}</span>
        </div>
        <div className="cand-meta">{meta}</div>
      </div>
      <PlayButton uri={`spotify:track:${t.id}`} label={tx(`Play ${t.name}`, `${t.name} を再生`)} />
    </label>
  );
}

function GroupCard(
  { g, pat, processing, blocked, queued, keepId, onKeep }:
    { g: DupeGroup; pat: string | null; processing: boolean; blocked: boolean; queued: boolean;
      keepId: string; onKeep: (id: string) => void },
) {
  const tx = useT();

  if (g.tier === "A") {
    return <TierACard g={g} pat={pat} processing={processing} blocked={blocked} />;
  }

  // ラジオ選択＝「残す1曲」。選択状態は親（Organize）が持つ＝一括ボタンから全グループを読める。
  const tracks = g.tracks ?? [];
  const remove = tracks.filter((t) => t.id !== keepId).map((t) => t.id);
  // 押しても即送信しないので、他のカードの処理中かどうかで無効化しない（issue #5）。
  const canApply = !!pat && !processing && !!keepId && remove.length > 0;
  const keptName = tracks.find((t) => t.id === keepId)?.name ?? g.id;

  return (
    <div className="card dupe-group" style={processing ? { opacity: 0.6 } : undefined}>
      <Header g={g} />
      {processing && <ProcessingBanner />}
      {tracks.map((t, i) => (
        <KeepRow
          key={t.id}
          t={t}
          i={i}
          radioName={`keep-${g.id}`}
          kept={t.id === keepId}
          onKeep={() => onKeep(t.id)}
          meta={<>{t.album} · {t.release_date} · <Duration ms={t.duration_ms} /> · {tx("Popularity", "人気")} {t.popularity ?? "—"}</>}
        />
      ))}
      <div className="dupe-actions">
        {queued ? (
          <>
            <span className="t-small queued-note">{tx("Queued — not sent yet.", "決定をキューに入れました（まだ送っていません）。")}</span>
            <button className="pill" onClick={() => dequeue(g.id)}>{tx("Undo", "取り消す")}</button>
          </>
        ) : (
          <>
            <button className="pill pill-green" disabled={!canApply}
              onClick={() => enqueue({ kind: "delete", groupId: g.id, keep: [keepId], remove, label: keptName })}>
              {tx("Delete the ones not chosen", "選ばなかった曲を削除")}{remove.length > 0 && `（${remove.length}）`}
            </button>
            <button className="pill" disabled={!pat || processing}
              onClick={() => enqueue({ kind: "keep-both", groupId: g.id, trackIds: tracks.map((t) => t.id), label: keptName })}>
              {tx("Keep both", "両方残す")}
            </button>
          </>
        )}
        {!pat && <span className="action-hint">{noPatHint(tx)}</span>}
      </div>
    </div>
  );
}

function ProcessingBanner() {
  const tx = useT();
  return (
    <div className="t-small" style={{ marginBottom: "var(--sp-2)" }}>
      {tx("Processing… a few minutes to reflect (auto-clears in 30 min)", "処理中… 反映まで数分（30分で自動解除）")}{" "}
      <a className="muted" href={runsUrl()} target="_blank" rel="noreferrer">{tx("Check on Actions", "Actions で確認")}</a>
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
  const tx = useT();
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const disabled = !pat || busy || processing || blocked;

  async function trim() {
    setBusy(true);
    setStatus(null);
    const res = await dispatchOp(pat!, "dedupe-trim", { group_id: g.id });
    setBusy(false);
    if (res.ok) markProcessing(g.id);
    setStatus(res.ok ? tx("Removing the extras… reflected in a few minutes.", "余分な1つを削除中… 数分後に反映されます。") : failMsg(tx, res.message));
  }

  return (
    <div className="card dupe-group" style={processing ? { opacity: 0.6 } : undefined}>
      <Header g={g} />
      {processing && <ProcessingBanner />}
      <div className="dupe-cand">
        <Art image={g.track?.image} />
        <div className="cand-main">
          <div className="cand-name"><span className="txt">{g.track?.name}</span></div>
          <div className="cand-meta">{g.track?.artists.join(", ")} — {tx(`${g.count}× in ${g.playlist?.name}`, `${g.playlist?.name} に ${g.count} 回`)}</div>
        </div>
        {g.track && <PlayButton uri={`spotify:track:${g.track.id}`} label={tx(`Play ${g.track.name}`, `${g.track.name} を再生`)} />}
      </div>
      <p className="t-small" style={{ margin: "var(--sp-2) 0 0" }}>
        {tx(
          `This track appears ${g.count}× in this playlist. Keeps one and removes the extras (position-based, so other tracks are untouched; undoable).`,
          `同じ曲がこのプレイリストに ${g.count} 回入っています。1つだけ残して余分を削除します（位置指定なので他の曲には触れません・取り消し可）。`,
        )}
      </p>
      <div className="dupe-actions">
        <button className="pill pill-green" disabled={disabled} onClick={trim}>
          {tx("Remove extras (keep 1)", "余分を削除（1つ残す）")}
        </button>
        {!pat && <span className="action-hint">{noPatHint(tx)}</span>}
        {(processing || status) && (
          <span className="t-small" style={{ alignSelf: "center" }}>
            {processing ? tx("Removing the extras… reflected in a few minutes.", "余分な1つを削除中… 数分後に反映されます。") : status}{" "}
            <a className="muted" href={runsUrl()} target="_blank" rel="noreferrer">Actions</a>
          </span>
        )}
      </div>
    </div>
  );
}

// 保留タブ。曲名スナップショットが無い旧エントリは search_index から名前を補完して表示する。
function KeepSection({ groups, pat }: { groups: KeepGroup[]; pat: string | null }) {
  const search = useJson<SearchIndex>("search_index");
  const byId = useMemo(
    () => new Map((search.data?.tracks ?? []).map((t) => [t.id, t] as const)),
    [search.data],
  );
  return <>{groups.map((g) => <KeepCard key={g.group_id} g={g} pat={pat} byId={byId} />)}</>;
}

// 「両方残す」で保留にした重複。ここで残す1曲を選んでその場で削除もできる（keep-trim）。
// 判断を保留したいときは「重複チェックに戻す」（keep-apply の remove → 再スキャンで復活）。
function KeepCard(
  { g, pat, byId }: { g: KeepGroup; pat: string | null; byId: Map<string, SearchTrack> },
) {
  const tx = useT();
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // スナップショットがあればそれを、無ければ track_ids を search_index で名前解決する。
  const tracks = g.tracks?.length
    ? g.tracks
    : g.track_ids.map((id) => {
        const s = byId.get(id);
        return { id, name: s?.name ?? id, artists: s?.artists ?? [], image: s?.image ?? null };
      });
  const [keepId, setKeepId] = useState<string>(() => tracks[0]?.id ?? "");
  const remove = tracks.filter((t) => t.id !== keepId).map((t) => t.id);
  const canTrim = !!pat && !busy && !!keepId && remove.length > 0;

  async function trim() {
    setBusy(true);
    setStatus(null);
    const res = await dispatchOp(pat!, "keep-trim", { group_id: g.group_id, keep: [keepId], remove });
    setBusy(false);
    setStatus(res.ok ? tx("Deleting the ones not chosen… reflected in a few minutes.", "選ばなかった曲を削除中… 数分後に反映されます。") : failMsg(tx, res.message));
  }

  async function restore() {
    setBusy(true);
    setStatus(null);
    const res = await dispatchOp(pat!, "keep-apply", { add: [], remove: [g.group_id] });
    setBusy(false);
    setStatus(res.ok ? tx("Sending back to the dupe check… reflected in a few minutes.", "重複チェックに戻しています… 数分後に反映されます。") : failMsg(tx, res.message));
  }

  return (
    <div className="card dupe-group">
      <div className="t-small" style={{ marginBottom: "var(--sp-2)" }}>
        {tx("Keep both", "両方残す")}{g.decided_at ? ` · ${g.decided_at}` : ""}
      </div>
      {tracks.map((t, i) => (
        <KeepRow
          key={t.id}
          t={t}
          i={i}
          radioName={`keeptrim-${g.group_id}`}
          kept={t.id === keepId}
          onKeep={() => setKeepId(t.id)}
          meta={t.artists.join(", ")}
        />
      ))}
      <div className="dupe-actions">
        <button className="pill pill-green" disabled={!canTrim} onClick={trim}>
          {tx("Delete the ones not chosen", "選ばなかった曲を削除")}{remove.length > 0 && `（${remove.length}）`}
        </button>
        <button className="pill" disabled={!pat || busy} onClick={restore}>
          {tx("Back to dupe check", "重複チェックに戻す")}
        </button>
        {!pat && <span className="action-hint">{noPatHint(tx)}</span>}
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
  const tx = useT();
  const tier = TIER_LABEL[g.tier];
  const reason = REASON_LABEL[g.reason];
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: "var(--sp-3)" }}>
      <span className={"badge badge-" + g.tier.toLowerCase()}>{tier ? tx(tier.en, tier.ja) : g.tier}</span>
      <span className="t-small">{reason ? tx(reason.en, reason.ja) : g.reason}</span>
    </div>
  );
}

function UndoSection(
  { pat, anyProcessing, processing }:
    { pat: string | null; anyProcessing: boolean; processing: Record<string, string> },
) {
  const tx = useT();
  const undo = useJson<UndoIndex>("undo_index");
  const [status, setStatus] = useState<Record<string, string>>({});
  const entries = undo.data?.entries ?? [];
  if (!entries.length) return null;

  async function run(id: string) {
    setStatus((s) => ({ ...s, [id]: tx("Undoing…", "取り消し中…") }));
    const res = await dispatchOp(pat!, "undo", { undo_id: id });
    if (res.ok) markProcessing(`undo-${id}`); // M-A: undo も直列化の枠内に入れる
    setStatus((s) => ({ ...s, [id]: res.ok ? tx("Undoing… reflected in a few minutes", "取り消し中… 数分後に反映") : failMsg(tx, res.message) }));
  }

  return (
    <Section title={tx("Recent operations (undoable)", "最近の操作（取り消し可）")}>
      <div className="card">
        {entries.slice(0, 8).map((e) => {
          const busy = !!processing[`undo-${e.id}`];
          return (
            <div className="list-row" key={e.id}>
              <span className="list-main">
                <div className="name">{e.op} — {tx(`${e.count} ${e.count === 1 ? "song" : "songs"}`, `${e.count}曲`)}</div>
                <div className="t-small">{e.created_at?.slice(0, 16).replace("T", " ")} {e.tracks.slice(0, 2).join(", ")}</div>
              </span>
              {e.op === "dedupe-apply" && (
                <button
                  className="pill"
                  disabled={!pat || busy || (anyProcessing && !busy)}
                  onClick={() => run(e.id)}
                >
                  {tx("Undo", "取り消し")}
                </button>
              )}
              {busy ? <span className="t-small">{tx("Undoing…", "取り消し中…")}</span> : status[e.id] && <span className="t-small">{status[e.id]}</span>}
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function ClassifyActions(
  { trackId, name, pat, processing, queued }:
    { trackId: string; name: string; pat: string | null; processing: boolean; queued: boolean },
) {
  const tx = useT();
  // 振り分けもキュー経由（押した瞬間は送らない）。連続で何曲でも決められる。
  const add = (cls: "japanese" | "western") =>
    enqueue({ kind: "classify", groupId: trackId, cls, label: name });
  return (
    <div className="dupe-actions">
      {queued ? (
        <>
          <span className="t-small queued-note">{tx("Queued — not sent yet.", "決定をキューに入れました（まだ送っていません）。")}</span>
          <button className="pill" onClick={() => dequeue(trackId)}>{tx("Undo", "取り消す")}</button>
        </>
      ) : (
        <>
          <button className="pill pill-green" disabled={!pat || processing} onClick={() => add("japanese")}>{tx("Japanese", "邦楽")}</button>
          <button className="pill" disabled={!pat || processing} onClick={() => add("western")}>{tx("Western", "洋楽")}</button>
        </>
      )}
      {!pat && <span className="action-hint">{noPatHint(tx)}</span>}
      {processing && <span className="t-small" style={{ alignSelf: "center" }}>{tx("Sorting…", "振り分け中…")}</span>}
    </div>
  );
}
