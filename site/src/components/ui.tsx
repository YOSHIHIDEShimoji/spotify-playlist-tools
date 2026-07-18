import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useT } from "../lib/i18n";

/**
 * 横スクロールする行（ナビ・ピッカー）を包み、内容が画面外に続くときだけ
 * 端にグラデーションのフェードを出す。スクロール位置を見て左右を出し分けるので
 * 「まだ右に項目がある」ことが分かり、端まで来たらフェードは消える（誤誘導しない）。
 */
export function ScrollRow(
  { children, className, role, ariaLabel, variant }:
    { children: ReactNode; className?: string; role?: string; ariaLabel?: string; variant?: "surface" },
) {
  const ref = useRef<HTMLDivElement>(null);
  const [edge, setEdge] = useState({ l: false, r: false });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      setEdge({
        l: el.scrollLeft > 4,
        r: el.scrollLeft + el.clientWidth < el.scrollWidth - 4,
      });
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [children]);

  return (
    <div
      className={"scroll-row" + (variant === "surface" ? " scroll-row--surface" : "") +
        (edge.l ? " fade-l" : "") + (edge.r ? " fade-r" : "")}
    >
      <div ref={ref} className={className} role={role} aria-label={ariaLabel}>
        {children}
      </div>
    </div>
  );
}

export function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section style={{ marginBottom: "var(--sp-8)" }}>
      <div className="sec-head">
        <h2 className="t-section">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Loading() {
  const t = useT();
  return <div className="t-caption" style={{ padding: "var(--sp-4)" }}>{t("Loading…", "読み込み中…")}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="card" style={{ color: "var(--text-secondary)", textAlign: "center" }}>
      {children}
    </div>
  );
}

export function StatCard({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat card-elevated">
      <div className="k">{label}</div>
      <div className="v">{value}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}

export function Duration({ ms }: { ms: number | null | undefined }) {
  if (!ms) return <>—</>;
  const s = Math.round(ms / 1000);
  return <>{Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}</>;
}
