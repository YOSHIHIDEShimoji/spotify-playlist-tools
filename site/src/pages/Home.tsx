import { useState } from "react";
import { useJson, useJsonl } from "../lib/data";
import type { ListeningStats, RunDetail, RunRecord } from "../lib/types";
import { Empty, Loading, ScrollRow, Section } from "../components/ui";
import { Modal } from "../components/Modal";
import { PlayButton } from "../lib/player";

type StepKey = "inbox" | "sync" | "sort" | "archive";
const STEP_TITLE: Record<StepKey, string> = {
  inbox: "inbox — 振り分け",
  sync: "sync — 同期",
  sort: "sort — 並べ替え",
  archive: "archive — Top50 追加",
};

export function Home() {
  const runs = useJsonl<RunRecord>("runs");
  const listen = useJson<ListeningStats>("listening_stats");

  return (
    <>
      {runs.loading ? (
        <div className="nightband">
          <Loading />
        </div>
      ) : (
        <NightBand runs={runs.data ?? []} listen={listen.data} />
      )}

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

const STATUS_BADGE: Record<string, { cls: string; label: string }> = {
  success: { cls: "badge-b", label: "success" },
  partial: { cls: "badge-c", label: "partial" },
  failure: { cls: "badge-a", label: "failed" },
};

function jpDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${Number(m[2])}月${Number(m[3])}日` : iso;
}

/** ISO タイムスタンプ → JST の HH:MM（同日ランの区別用）。 */
function jstTime(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("ja-JP", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit" });
}

/** Home ヒーロー: 昨晩の夜間ランを署名要素として最前面に出す。 */
function NightBand({ runs, listen }: { runs: RunRecord[]; listen: ListeningStats | null }) {
  const [step, setStep] = useState<StepKey | null>(null);
  const real = realRuns(runs);
  // 本番ランがあればそれを、無ければ最新の dry run を出す（下の履歴と食い違わせない）。
  const latest = real.length ? real[real.length - 1] : (runs.length ? runs[runs.length - 1] : null);
  const isDryOnly = !real.length && !!latest; // 本番の夜間ランはまだ（配線確認の dry run のみ）

  let streak = 0;
  for (let i = real.length - 1; i >= 0; i--) {
    if (real[i].status === "success") streak++;
    else break;
  }
  const successRate = real.length
    ? Math.round((real.filter((r) => r.status === "success").length / real.length) * 100)
    : 0;
  // 聴取ログが有効か（再認証前は since=null・total=0 のダミー。実数ゼロと区別する）。
  const listenActive = !!listen && (listen.since != null || listen.milestone.total > 0);

  if (!latest) {
    return (
      <div className="nightband">
        <div className="nightband-top">
          <span className="eyebrow">nightly run · 01:00 JST</span>
        </div>
        <h1 className="t-display">まだ夜間ランの記録がありません</h1>
        <p className="nightband-sub">
          毎晩 01:00（JST）に inbox → sync → sort → archive が自動で走り、その結果が翌朝ここに出ます。
        </p>
      </div>
    );
  }

  const s = latest.steps;
  const badge = STATUS_BADGE[latest.status] ?? STATUS_BADGE.partial;
  const touched = s.inbox.processed + s.sync.added + s.sync.removed + s.archive.added;
  const sub = isDryOnly
    ? "パイプラインの配線は完了。まだ本番の夜間ランは走っていません（下は動作確認 dry run の記録）。今夜以降の実ランからここに実データが出ます。"
    : touched === 0
      ? "静かな夜でした。ライブラリに変更はありません。"
      : `inbox ${s.inbox.processed}件を振り分け、同期 +${s.sync.added}/−${s.sync.removed}、` +
        `${s.sort.playlists} プレイリストを整えました。`;

  return (
    <div className="nightband">
      <div className="nightband-top">
        <span className="eyebrow">
          {isDryOnly || !jstTime(latest.generated_at)
            ? "nightly run · 01:00 JST 予定"
            : `nightly run · ${jstTime(latest.generated_at)} JST 実行`}
        </span>
        {isDryOnly
          ? <span className="badge status">dry run</span>
          : <span className={"badge status " + badge.cls}>{badge.label}</span>}
      </div>

      <h1 className="t-display">{isDryOnly ? "本番の夜間ランはまだです" : `${jpDate(latest.date)}の記録`}</h1>
      <p className="nightband-sub">{sub}</p>

      <div className="pipe">
        <PipeStep n="01" name="inbox" value={s.inbox.processed} detail={`邦 ${s.inbox.japanese} · 洋 ${s.inbox.western} · 不明 ${s.inbox.unknown}`} onClick={() => setStep("inbox")} />
        <PipeStep n="02" name="sync" value={s.sync.added} prefix="+" detail={`−${s.sync.removed} · 新規AP ${s.sync.new_playlists}`} onClick={() => setStep("sync")} />
        <PipeStep n="03" name="sort" value={s.sort.playlists} detail={`見送り ${s.sort.skipped}`} onClick={() => setStep("sort")} />
        <PipeStep n="04" name="archive" value={s.archive.added} prefix="+" detail="Top50 追加" onClick={() => setStep("archive")} />
      </div>

      {step && <StepDetailModal step={step} run={latest} onClose={() => setStep(null)} />}

      <div className="nightband-foot">
        <div className="foot-stat">
          <span className="k">連続成功</span>
          <span className="v">
            {isDryOnly ? <span className="muted">本番ラン待ち</span> : <>{streak}<span className="muted"> 日</span> · 成功率 {successRate}%</>}
          </span>
        </div>
        <div className="foot-stat">
          <span className="k">累計再生</span>
          <span className="v">
            {listenActive
              ? <>{listen!.milestone.total.toLocaleString()}{listen!.milestone.next ? <span className="muted"> / 次 {listen!.milestone.next.toLocaleString()}</span> : null}</>
              : <span className="muted">未計測</span>}
          </span>
        </div>
      </div>
    </div>
  );
}

function PipeStep(
  { n, name, value, prefix, detail, onClick }:
    { n: string; name: string; value: number; prefix?: string; detail: string; onClick?: () => void },
) {
  const cls = value > 0 ? "v pos" : "v zero";
  return (
    <button type="button" className="pipe-step pipe-step--btn" onClick={onClick} aria-label={`${name} の内訳を見る`}>
      <span className="k">{n} · {name}</span>
      <span className={cls}>{prefix && value > 0 ? prefix : ""}{value}</span>
      <span className="d">{detail} ›</span>
    </button>
  );
}

// ステップをタップしたときの内訳モーダル（どの曲がどこへ動いたか）。
function StepDetailModal({ step, run, onClose }: { step: StepKey; run: RunRecord; onClose: () => void }) {
  const d = run.detail;
  return (
    <Modal
      title={STEP_TITLE[step]}
      subtitle={`${jpDate(run.date)}${run.dry_run ? "・dry run（予定）" : ""}`}
      onClose={onClose}
    >
      {!d ? (
        <p className="t-small">
          この回はステップ別の内訳が記録されていません（次回の夜間ランから表示されます）。
        </p>
      ) : (
        <StepDetailBody step={step} detail={d} />
      )}
    </Modal>
  );
}

function StepDetailBody({ step, detail }: { step: StepKey; detail: RunDetail }) {
  if (step === "inbox") {
    const rows = detail.inbox ?? [];
    if (!rows.length) return <Empty>この回は新しい振り分けはありません（0件）。</Empty>;
    return (
      <div className="modal-list">
        {rows.map((r, i) => (
          <div className="list-row" key={i}>
            <span className="list-main">
              <div className="name">{r.name}</div>
              <div className="t-small">{r.artist}</div>
            </span>
            <span className="t-small">{r.dest.join(" / ")}</span>
          </div>
        ))}
      </div>
    );
  }
  if (step === "sync") {
    const rows = detail.sync ?? [];
    if (!rows.length) return <Empty>同期による変更はありません（0件）。</Empty>;
    return (
      <div className="modal-list">
        {rows.map((r, i) => (
          <div className="list-row" key={i} style={{ alignItems: "flex-start" }}>
            <span className="list-main">
              <div className="name">{r.playlist}</div>
              {r.added.length > 0 && <div className="t-small">＋ {r.added.join(", ")}</div>}
            </span>
            <span className="t-small num">+{r.added.length}{r.removed ? ` / −${r.removed}` : ""}</span>
          </div>
        ))}
      </div>
    );
  }
  if (step === "sort") {
    const rows = detail.sort ?? [];
    if (!rows.length) return <Empty>並べ替えたプレイリストはありません（0件）。</Empty>;
    const label = (s: string) => (s === "skipped" ? "見送り" : s === "dry" ? "予定" : "並べ替え");
    return (
      <div className="modal-list">
        {rows.map((r, i) => (
          <div className="list-row" key={i}>
            <span className="list-main"><div className="name">{r.name}</div></span>
            <span className="t-small num">{r.count}曲</span>
            <span className={"badge " + (r.status === "skipped" ? "badge-c" : "badge-b")}>{label(r.status)}</span>
          </div>
        ))}
      </div>
    );
  }
  const rows = detail.archive ?? [];
  if (!rows.length) return <Empty>Top50 への追加はありません（0件）。</Empty>;
  return (
    <div className="modal-list">
      {rows.map((r, i) => (
        <div className="list-row" key={i}>
          <span className="list-main">
            <div className="name">{r.name}</div>
            <div className="t-small">{r.artists.join(", ")}</div>
          </span>
        </div>
      ))}
    </div>
  );
}

function RunTimeline({ runs }: { runs: RunRecord[] }) {
  if (!runs.length) return <Empty>まだ実行記録がありません。</Empty>;
  const recent = [...runs].slice(-14).reverse();
  return (
    <ScrollRow className="card table-scroll" variant="surface" ariaLabel="実行履歴">
      <table className="data-table">
        <thead>
          <tr><th>日時</th><th>状態</th><th>inbox</th><th>sync</th><th>sort</th><th>archive</th></tr>
        </thead>
        <tbody>
          {recent.map((r, i) => (
            <tr key={`${r.run_id}-${i}`}>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
                  <span>{r.date.slice(5)}</span>
                  {r.dry_run && <span className="badge">dry</span>}
                </div>
                {jstTime(r.generated_at) && <div className="t-small num">{jstTime(r.generated_at)}</div>}
              </td>
              <td>
                <span className={"badge " + (r.status === "success" ? "badge-b" : "badge-c")}>{r.status}</span>
              </td>
              <td className="num">{r.steps.inbox.processed}</td>
              <td className="num">+{r.steps.sync.added}/-{r.steps.sync.removed}</td>
              <td className="num">{r.steps.sort.playlists}</td>
              <td className="num">+{r.steps.archive.added}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollRow>
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
          <PlayButton uri={`spotify:track:${t.track_id}`} label={`${t.name} を再生`} />
        </div>
      ))}
    </div>
  );
}
