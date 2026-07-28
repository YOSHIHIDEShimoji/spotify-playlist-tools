// dashboard-design.md §5.3 のデータスキーマに対応する TS 型。

// ステップをタップしたとき出す内訳（どの曲がどこへ動いたか）。無い回もある（旧データ）。
export interface DedupeChange {
  name: string;
  artists?: string[];
  kept: { album: string; album_type: string };
  removed: { album: string; album_type: string }[];
  isrc?: string;
  delta_ms?: number;
  undo_id?: string | null;
}

export interface RunDetail {
  inbox?: { name: string; artist: string; dest: string[] }[];
  sync?: { playlist: string; added: string[]; removed: number }[];
  sort?: { name: string; status: string; count: number }[];
  archive?: { name: string; artists: string[] }[];
  // 自動整理（同一録音を1曲に畳んだ内訳）。残した版 / 消した版 / undo ID。
  dedupe?: DedupeChange[];
}

export interface RunRecord {
  date: string;
  run_id: number | null;
  status: "success" | "partial";
  dry_run: boolean;
  steps: {
    inbox: { processed: number; japanese: number; western: number; unknown: number };
    sync: { added: number; removed: number; new_playlists: number };
    sort: { playlists: number; skipped: number };
    archive: { added: number };
    // 旧データには無いので optional。UI は ?? 0 でガードする。
    dedupe?: { deleted: number; groups: number };
  };
  detail?: RunDetail;
  generated_at: string;
}

export interface AuthStatus {
  token_ok: boolean;
  checked_at: string;
  missing_scopes: string[];
}

export interface DupeTrack {
  id: string;
  name: string;
  artists: string[];
  album: string;
  album_type: string;
  release_date: string;
  duration_ms: number | null;
  popularity: number | null;
  isrc: string;
  image?: string | null; // アルバムのサムネイル URL（新データのみ）
  playlists: { id: string; name: string }[];
}

export interface DupeGroup {
  id: string;
  tier: "A" | "B" | "C";
  reason: string;
  // Tier B/C
  tracks?: DupeTrack[];
  // Tier A
  playlist?: { id: string; name: string };
  track?: { id: string; name: string; artists: string[]; image?: string | null };
  count?: number;
}

export interface Dupes {
  generated_at: string;
  counts: { A: number; B: number; C: number };
  groups: DupeGroup[];
}

// 「両方残す」で保留にした重複グループ（保留タブで一覧・取り消し）
export interface KeepGroup {
  group_id: string;
  track_ids: string[];
  tier?: "A" | "B" | "C";
  decided_at?: string;
  tracks?: { id: string; name: string; artists: string[]; image?: string | null }[];
}
export interface KeepIndex {
  groups: KeepGroup[];
}

export interface UnknownTrack {
  id: string;
  name: string;
  artists: string[];
  isrc: string;
}

export interface Unknown {
  generated_at: string;
  tracks: UnknownTrack[];
}

export interface RankedTrack {
  track_id: string; // Spotify track id、または Last.fm 由来で未解決なら "lastfm:<key>"
  name: string;
  artists: string[];
  count: number;
  image?: string | null; // Last.fm scrobble はアート URL を持つ（未解決トラックのサムネ用）
}

export interface ListeningStats {
  generated_at: string;
  since: string | null;
  weekly_top: RankedTrack[];
  cumulative_top: RankedTrack[];
  streak: number;
  milestone: { total: number; reached: number[]; next: number | null };
}

export interface StatsGroup {
  total: number;
  artists_top: { name: string; count: number; id?: string }[];
  decades: { decade: number; count: number }[];
}

export interface Stats extends StatsGroup {
  generated_at: string;
  total: number; // 管理ライブラリのユニーク曲数（延べではない・Growth 用）
  // stats タブの選択用: 各プレイリスト単体（by）＋全部合算（all）
  dist?: {
    playlists: { id: string; name: string }[];
    all: StatsGroup;
    by: Record<string, StatsGroup>;
  };
}

export interface HeatmapCell {
  dow: number;
  hour: number;
  count: number;
}
export interface Heatmap {
  generated_at: string;
  cells: HeatmapCell[];
}

export interface StatsHistoryRow {
  date: string;
  playlist_id: string;
  name: string;
  count: number;
}

export interface TopEntry {
  id: string;
  name: string;
  artists?: string[];
  rank: number;
  image?: string | null; // アルバムのサムネイル URL（新データのみ）
}
export interface Top {
  generated_at: string;
  tracks: Record<string, TopEntry[]>;
  artists: Record<string, TopEntry[]>;
}

export interface ReleaseItem {
  album_id: string;
  album_name: string;
  album_type: string;
  artist: string;
  artist_id?: string;
  release_date: string;
  is_new?: boolean;
  image?: string | null;
  class?: "japanese" | "western"; // 邦/洋の振り分け（新データのみ・無ければ western 扱い）
}
export interface Releases {
  generated_at: string;
  items: ReleaseItem[];
}

export interface UndoEntry {
  id: string;
  op: string;
  created_at: string;
  count: number;
  tracks: string[];
}
export interface UndoIndex {
  generated_at: string;
  entries: UndoEntry[];
}

export interface WrappedIndex {
  months: string[];
  years?: string[]; // 年間 Wrapped（新データのみ・無ければ月だけ）
}
export interface Wrapped {
  month?: string; // 月間のみ
  year?: string;  // 年間のみ
  plays: number;
  ms?: number;    // 年間のみ（その年の総再生時間）
  top_tracks: RankedTrack[];
  top_artists: { name: string; count: number }[];
  new_tracks: number;
  peak: { dow: number; hour: number } | null;
  months?: { month: string; count: number }[]; // 年間のみ（月別の再生数）
}

// ── 生涯集計（history/*.jsonl.gz 由来・全件） ──

export interface LifetimeTrack {
  id: string;
  name: string;
  artists: string[];
  count: number;
  ms: number;
  first: string;               // 初めて聴いた日（JST・YYYY-MM-DD）
  last: string;                // 最後に聴いた日
  years: Record<string, number>;
  image?: string | null;
  short?: number;              // 30秒未満でやめた回数（完走率の分母）
}
export interface LifetimeTracks {
  generated_at: string;
  totals: {
    plays: number;
    tracks: number;
    artists: number;
    ms: number;
    since: string | null;
    days: number;              // 1回でも聴いた日の数
  };
  tracks: LifetimeTrack[];     // count 降順＝配列の並びがそのまま順位
}

export interface LifetimeArtist {
  name: string;
  count: number;
  tracks: number;
  ms: number;
  first: string;
  last: string;
  years: Record<string, number>;
  id?: string;
  image?: string | null;
}
export interface LifetimeArtists {
  generated_at: string;
  artists: LifetimeArtist[];
}

export interface Rediscover {
  generated_at: string;
  quiet_days: number;
  min_plays: number;
  tracks: LifetimeTrack[];
}

export interface OnThisDay {
  generated_at: string;
  date: string;
  years: { year: string; plays: number; tracks: RankedTrack[] }[];
}

// ── おすすめ（Last.fm 由来・Spotify の推薦 API は廃止済み） ──

export interface RecArtist {
  name: string;
  score: number;
  because: { name: string; count: number }[];
}
export interface RecTrack {
  name: string;
  artist: string;
  score: number;
  because: { name: string; count: number };
  id?: string;
  image?: string | null;
}
export interface Recs {
  generated_at: string;
  source: string;
  available: boolean;
  reason?: string;
  artists: RecArtist[];
  tracks: RecTrack[];
}

export interface ArchiveWeek {
  iso_week: string;
  tracks: { id: string; name: string; artists: string[]; added_at: string; image?: string | null }[];
}
export interface ArchiveWeekly {
  generated_at: string;
  weeks: ArchiveWeek[];
}

export interface SearchTrack {
  id: string;
  name: string;
  artists: string[];
  playlists: string[];
  release_date?: string; // 年代モーダル用（新データのみ）
  image?: string | null; // アルバムのサムネイル URL（新データのみ・保留タブ等の見た目に使う）
}
export interface SearchIndex {
  generated_at: string;
  tracks: SearchTrack[];
}
