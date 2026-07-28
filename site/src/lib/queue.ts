// 整理タブの「決定キュー」。
//
// 以前は1曲ぶんの決定を押すたびに workflow_dispatch を投げ、完了まで他のボタンを止めていた
// （1回押すと次を押すまで数分待たされる＝issue #5）。ここでは押した瞬間はローカルのキューに
// 積むだけにして、少し待ってからまとめて1回だけ dispatch する。押し続けても待たされないし、
// 送信前なら取り消せる。
//
// まとめて送るのは UX だけの都合ではない。site-ops.yml は concurrency group で直列化されて
// おり、実行中に2件以上を続けて投げると待機中のランが後続に追い出されて消える。1回の
// dispatch に全決定を載せれば、この取りこぼしが原理的に起きない。
import { useSyncExternalStore } from "react";

export interface DeleteDecision {
  kind: "delete";
  groupId: string;
  keep: string[];
  remove: string[];
  label: string;
}
export interface KeepBothDecision {
  kind: "keep-both";
  groupId: string;
  trackIds: string[];
  label: string;
}
/** 判定できなかった曲の振り分け。groupId には track_id を入れる（キューの一意キー）。 */
export interface ClassifyDecision {
  kind: "classify";
  groupId: string;
  cls: "japanese" | "western";
  label: string;
}
export type Pending = DeleteDecision | KeepBothDecision | ClassifyDecision;

/** 送信までの猶予（ms）。押すたびに再設定されるので、連打中は送信されない。 */
export const SEND_DELAY_MS = 8000;

const EMPTY: Pending[] = [];
let queue: Pending[] = EMPTY;
const listeners = new Set<() => void>();

function emit(next: Pending[]) {
  queue = next;
  listeners.forEach((l) => l());
}

/** 同じグループへの決定は後から押したほうで置き換える（押し直しができる）。 */
export function enqueue(item: Pending): void {
  emit([...queue.filter((q) => q.groupId !== item.groupId), item]);
}

export function dequeue(groupId: string): void {
  if (queue.some((q) => q.groupId === groupId)) {
    emit(queue.filter((q) => q.groupId !== groupId));
  }
}

export function clearQueue(): void {
  if (queue.length) emit(EMPTY);
}

export function snapshot(): Pending[] {
  return queue;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function useQueue(): Pending[] {
  return useSyncExternalStore(subscribe, () => queue, () => EMPTY);
}

/** キューを dispatch 用のペイロードに変換する（削除・保留・振り分けはそれぞれ別 op）。 */
export function toPayloads(items: Pending[]): {
  decisions: { group_id: string; keep: string[]; remove: string[] }[];
  keeps: { group_id: string; track_ids: string[] }[];
  classify: { track_id: string; class: string }[];
} {
  const decisions = items
    .filter((i): i is DeleteDecision => i.kind === "delete" && i.remove.length > 0)
    .map((i) => ({ group_id: i.groupId, keep: i.keep, remove: i.remove }));
  const keeps = items
    .filter((i): i is KeepBothDecision => i.kind === "keep-both")
    .map((i) => ({ group_id: i.groupId, track_ids: i.trackIds }));
  const classify = items
    .filter((i): i is ClassifyDecision => i.kind === "classify")
    .map((i) => ({ track_id: i.groupId, class: i.cls }));
  return { decisions, keeps, classify };
}
