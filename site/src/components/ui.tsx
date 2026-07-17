import type { ReactNode } from "react";

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
