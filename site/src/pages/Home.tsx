import { useState } from "react";
import { useJsonl } from "../lib/data";
import type { RunDetail, RunRecord } from "../lib/types";
import { Empty, Loading, ScrollRow, Section } from "../components/ui";
import { Modal } from "../components/Modal";
import { monthDay, useLang, useT } from "../lib/i18n";

type StepKey = "inbox" | "sync" | "sort" | "archive";
function stepTitle(step: StepKey, tx: (en: string, ja: string) => string): string {
  const map: Record<StepKey, string> = {
    inbox: tx("inbox — sorting", "inbox — 振り分け"),
    sync: tx("sync — sync", "sync — 同期"),
    sort: tx("sort — reorder", "sort — 並べ替え"),
    archive: tx("archive — Top50", "archive — Top50 追加"),
  };
  return map[step];
}

export function Home() {
  const tx = useT();
  const runs = useJsonl<RunRecord>("runs");

  return (
    <>
      {runs.loading ? (
        <div className="nightband">
          <Loading />
        </div>
      ) : (
        <NightBand runs={runs.data ?? []} />
      )}

      <Section title={tx("Run history", "実行履歴")}>
        {runs.loading ? <Loading /> : <RunTimeline runs={runs.data ?? []} />}
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

/** ISO タイムスタンプ → JST の HH:MM（同日ランの区別用）。 */
function jstTime(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-GB", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

/** Home ヒーロー: 昨晩の夜間ランを署名要素として最前面に出す。 */
function NightBand({ runs }: { runs: RunRecord[] }) {
  const tx = useT();
  const { lang } = useLang();
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

  if (!latest) {
    return (
      <div className="nightband">
        <div className="nightband-top">
          <span className="eyebrow">nightly run · 01:00 JST</span>
        </div>
        <h1 className="t-display">{tx("No nightly run recorded yet", "まだ夜間ランの記録がありません")}</h1>
        <p className="nightband-sub">
          {tx(
            "Every night at 01:00 (JST), inbox → sync → sort → archive run automatically, and the results appear here the next morning.",
            "毎晩 01:00（JST）に inbox → sync → sort → archive が自動で走り、その結果が翌朝ここに出ます。",
          )}
        </p>
      </div>
    );
  }

  const s = latest.steps;
  const badge = STATUS_BADGE[latest.status] ?? STATUS_BADGE.partial;
  const touched = s.inbox.processed + s.sync.added + s.sync.removed + s.archive.added;
  const sub = isDryOnly
    ? tx(
        "The pipeline is wired up. No production nightly run has happened yet (below is a dry-run check). Real data will appear here from tonight's run onward.",
        "パイプラインの配線は完了。まだ本番の夜間ランは走っていません（下は動作確認 dry run の記録）。今夜以降の実ランからここに実データが出ます。",
      )
    : touched === 0
      ? tx("A quiet night — no changes to your library.", "静かな夜でした。ライブラリに変更はありません。")
      : tx(
          `Sorted ${s.inbox.processed} in inbox, synced +${s.sync.added}/−${s.sync.removed}, tidied ${s.sort.playlists} playlists.`,
          `inbox ${s.inbox.processed}件を振り分け、同期 +${s.sync.added}/−${s.sync.removed}、${s.sort.playlists} プレイリストを整えました。`,
        );

  return (
    <div className="nightband">
      <div className="nightband-top">
        <span className="eyebrow">
          {isDryOnly || !jstTime(latest.generated_at)
            ? tx("nightly run · 01:00 JST (scheduled)", "nightly run · 01:00 JST 予定")
            : tx(`nightly run · ${jstTime(latest.generated_at)} JST`, `nightly run · ${jstTime(latest.generated_at)} JST 実行`)}
        </span>
        {isDryOnly
          ? <span className="badge status">dry run</span>
          : <span className={"badge status " + badge.cls}>{badge.label}</span>}
      </div>

      <h1 className="t-display">
        {isDryOnly
          ? tx("No production nightly run yet", "本番の夜間ランはまだです")
          : tx(`${monthDay(latest.date, "en")} report`, `${monthDay(latest.date, "ja")}の記録`)}
      </h1>
      <p className="nightband-sub">{sub}</p>

      <div className="pipe">
        <PipeStep n="01" name="inbox" value={s.inbox.processed} detail={tx(`JP ${s.inbox.japanese} · West ${s.inbox.western} · unknown ${s.inbox.unknown}`, `邦 ${s.inbox.japanese} · 洋 ${s.inbox.western} · 不明 ${s.inbox.unknown}`)} onClick={() => setStep("inbox")} />
        <PipeStep n="02" name="sync" value={s.sync.added} prefix="+" detail={tx(`−${s.sync.removed} · new AP ${s.sync.new_playlists}`, `−${s.sync.removed} · 新規AP ${s.sync.new_playlists}`)} onClick={() => setStep("sync")} />
        <PipeStep n="03" name="sort" value={s.sort.playlists} detail={tx(`skipped ${s.sort.skipped}`, `見送り ${s.sort.skipped}`)} onClick={() => setStep("sort")} />
        <PipeStep n="04" name="archive" value={s.archive.added} prefix="+" detail={tx("Top50 added", "Top50 追加")} onClick={() => setStep("archive")} />
      </div>

      {step && <StepDetailModal step={step} run={latest} lang={lang} onClose={() => setStep(null)} />}

      <div className="nightband-foot">
        <div className="foot-stat">
          <span className="k">{tx("Streak", "連続成功")}</span>
          <span className="v">
            {isDryOnly
              ? <span className="muted">{tx("awaiting production run", "本番ラン待ち")}</span>
              : <>{streak}<span className="muted"> {tx("days", "日")}</span> · {tx(`${successRate}% success`, `成功率 ${successRate}%`)}</>}
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
  const tx = useT();
  const cls = value > 0 ? "v pos" : "v zero";
  return (
    <button type="button" className="pipe-step pipe-step--btn" onClick={onClick} aria-label={tx(`View ${name} details`, `${name} の内訳を見る`)}>
      <span className="k">{n} · {name}</span>
      <span className={cls}>{prefix && value > 0 ? prefix : ""}{value}</span>
      {/* 矢印は直前の語と非改行スペースで結合し、モバイルで › だけが孤立して折り返すのを防ぐ（fable5 レビュー）。 */}
      <span className="d">{detail}{" ›"}</span>
    </button>
  );
}

// ステップをタップしたときの内訳モーダル（どの曲がどこへ動いたか）。
function StepDetailModal(
  { step, run, lang, onClose }: { step: StepKey; run: RunRecord; lang: "en" | "ja"; onClose: () => void },
) {
  const tx = useT();
  const d = run.detail;
  return (
    <Modal
      title={stepTitle(step, tx)}
      subtitle={`${monthDay(run.date, lang)}${run.dry_run ? tx(" · dry run (planned)", "・dry run（予定）") : ""}`}
      onClose={onClose}
    >
      {!d ? (
        <p className="t-small">
          {tx(
            "No per-step details recorded for this run (they appear from the next nightly run).",
            "この回はステップ別の内訳が記録されていません（次回の夜間ランから表示されます）。",
          )}
        </p>
      ) : (
        <StepDetailBody step={step} detail={d} />
      )}
    </Modal>
  );
}

function StepDetailBody({ step, detail }: { step: StepKey; detail: RunDetail }) {
  const tx = useT();
  if (step === "inbox") {
    const rows = detail.inbox ?? [];
    if (!rows.length) return <Empty>{tx("No new sorting for this run (0).", "この回は新しい振り分けはありません（0件）。")}</Empty>;
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
    if (!rows.length) return <Empty>{tx("No sync changes (0).", "同期による変更はありません（0件）。")}</Empty>;
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
    if (!rows.length) return <Empty>{tx("No playlists reordered (0).", "並べ替えたプレイリストはありません（0件）。")}</Empty>;
    const label = (st: string) =>
      st === "skipped" ? tx("skipped", "見送り") : st === "dry" ? tx("planned", "予定") : tx("reordered", "並べ替え");
    return (
      <div className="modal-list">
        {rows.map((r, i) => (
          <div className="list-row" key={i}>
            <span className="list-main"><div className="name">{r.name}</div></span>
            <span className="t-small num">{tx(`${r.count} songs`, `${r.count}曲`)}</span>
            <span className={"badge " + (r.status === "skipped" ? "badge-c" : "badge-b")}>{label(r.status)}</span>
          </div>
        ))}
      </div>
    );
  }
  const rows = detail.archive ?? [];
  if (!rows.length) return <Empty>{tx("No additions to Top50 (0).", "Top50 への追加はありません（0件）。")}</Empty>;
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
  const tx = useT();
  if (!runs.length) return <Empty>{tx("No run records yet.", "まだ実行記録がありません。")}</Empty>;
  const recent = [...runs].slice(-14).reverse();
  return (
    <ScrollRow className="card table-scroll" variant="surface" ariaLabel={tx("Run history", "実行履歴")}>
      <table className="data-table">
        <thead>
          <tr><th>{tx("Time", "日時")}</th><th>{tx("Status", "状態")}</th><th>inbox</th><th>sync</th><th>sort</th><th>archive</th></tr>
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

