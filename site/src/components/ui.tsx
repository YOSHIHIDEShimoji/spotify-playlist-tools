import type { ReactNode } from "react";

export function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section style={{ marginBottom: "var(--sp-8)" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: "var(--sp-3)" }}>
        <h2 className="t-section">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Loading() {
  return <div className="t-caption" style={{ padding: "var(--sp-4)" }}>読み込み中…</div>;
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
    <div className="card card-elevated" style={{ flex: "1 1 140px", minWidth: 0 }}>
      <div className="t-small" style={{ textTransform: "uppercase", letterSpacing: "1px" }}>{label}</div>
      <div className="stat-num" style={{ margin: "var(--sp-2) 0" }}>{value}</div>
      {sub && <div className="t-small">{sub}</div>}
    </div>
  );
}

export function Duration({ ms }: { ms: number | null | undefined }) {
  if (!ms) return <>—</>;
  const s = Math.round(ms / 1000);
  return <>{Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}</>;
}
