import type { ReactNode } from "react";

export interface ImageEntry {
  page: number;
  sequence: number;
  title: string | null;
  folder: string;
  file: string;
  preview: string | null;
  variant: number | string | null;
  pdf_page: number;
  status: "named" | "review" | "unsigned";
  size: [number, number];
  reason?: string;
  /** Captions the parser could not choose between («На проверку»). */
  candidates?: string[];
  removed?: boolean;
  /** Saved by the automatic filter as a service element; restorable like a removed card. */
  suppressed?: boolean;
  suppression_code?: string;
  copy_of?: number;
  /** The collage this card belongs to: one of its frames, or the whole picture offered for cutting. */
  collage?: string;
  /** A frame's box in the collage pixels. */
  crop_px?: [number, number, number, number];
  cut?: "seam" | "manual" | "cutout";
  /** An earlier frame (or the whole picture) replaced by a new cut; kept for undo, not shown as removed. */
  collage_replaced?: boolean;
  /** The cutout record this card was cut by: a part of a picture or a page snapshot. */
  cutout?: string;
  /** The picture or an earlier cutout replaced by cutouts of this record; not shown as removed. */
  cutout_replaced?: string;
  method?: string;
  manual?: boolean;
  group?: string;
  auto_folder?: string;
  original_location?: {
    folder: string;
    status: "named" | "review" | "unsigned";
    reason?: string;
    file?: string;
  };
}
export interface Report {
  version: string;
  revision: number;
  source: string;
  output: string;
  pages: number;
  images: ImageEntry[];
  documents: {
    file: string;
    folder: string;
    variant: number | string | null;
    pages: number;
  }[];
  variant_names?: Record<string, string>;
  collages?: Collage[];
  cutouts?: CutoutRecord[];
  folder_labels?: Record<string, string>;
  suppressed?: unknown[];
  counts_by_status: Record<string, number>;
  warnings: unknown[];
  errors: unknown[];
  can_undo_save?: boolean;
  edits?: { backup?: string; action?: string; kind?: string }[];
}
export interface Collage {
  id: string;
  page: number;
  /** suspected — offered for cutting; auto/manual — cut; whole — cut undone; dismissed — one photo. */
  state: "suspected" | "auto" | "manual" | "whole" | "dismissed";
  checked: boolean;
  size: [number, number];
  preview?: string | null;
  file?: string;
  image?: number;
  title?: string | null;
  boxes?: [number, number, number, number][];
  suggestion?: [number, number, number, number][];
  reason?: string;
}
/** Left, top, right, bottom as shares of the source page, from 0 to 1. */
export type Rect = [number, number, number, number];
/** Where cutouts of a picture or of a page snapshot come from. */
export interface CutoutRecord {
  id: string;
  page: number;
  /** image — parts of one picture in its pixels; page — snapshots of the page. */
  kind: "image" | "page";
  /** The picture the cutouts replace. */
  image?: number;
  /** The share of the page the source covers. */
  area: Rect;
  /** Pixels of the source: the picture, or the page snapshot. */
  size: [number, number];
  file?: string;
  scale?: number;
  title?: string | null;
}
/** The source pages as the cutout editor draws them. */
export interface PagesInfo {
  pages: { number: number; size: [number, number]; snapshot: [number, number] }[];
  pictures: { image: number; page: number; area: Rect; size: [number, number] }[];
}
export interface Result {
  id: string;
  base_url: string;
  title: string;
  report: Report;
}
export interface HistoryItem {
  id: string;
  name: string;
  output: string;
  source: string;
  images: number;
  documents: number;
  variants: number;
  unsigned: number;
  review: number;
  errors: number;
  warnings: number;
  size: number;
  finished_at: string;
  available: boolean;
  imported: boolean;
}
export interface ProcessingFailure {
  id: string;
  source: string;
  name: string;
  failed_at: string;
  reason: string;
  action: "choose_output" | "retry";
}
export interface PendingRepeat {
  token: string;
  files: {
    name: string;
    source: string;
    previous_id: string;
    finished_at: string;
    available: boolean;
  }[];
}
export interface NoticeAction {
  label: string;
  /** Drawn before the label, as the undo arrow of «Отменить сохранение». */
  icon?: ReactNode;
  run(): void | Promise<void>;
}
export interface Notice {
  text: string;
  error: boolean;
  action?: NoticeAction;
}
export interface Snapshot {
  version: string;
  busy: boolean;
  has_job?: boolean;
  job?: {
    outcome: "idle" | "processing" | "complete" | "partial" | "cancelled" | "failed";
    total: number;
    completed: number;
    failed: number;
    warnings: number;
    errors: number;
    active_file: { name: string; index: number; total: number } | null;
    /** Result folders of this queue in order; absent from older servers. */
    outputs?: string[];
  };
  cancelling: boolean;
  closing: boolean;
  status: string;
  detail: string;
  progress: number;
  totals: {
    images: number;
    documents: number;
    unsigned: number;
    review: number;
  };
  logs: { id: number; text: string; level: string }[];
  settings: {
    mode: "nearby" | "results" | "custom";
    folder: string;
    label: string;
  };
  sidebar?: { width: number; hidden: boolean };
  history: HistoryItem[];
  current_output: string | null;
  warning: string;
  failures?: ProcessingFailure[];
  pending_repeat?: PendingRepeat | null;
}
export interface NativeApi {
  choose_pdfs(): Promise<Snapshot>;
  choose_result(): Promise<Result | null>;
  choose_output(): Promise<Snapshot>;
  open_result(id: string, relative?: string): Promise<boolean>;
  open_log(): Promise<boolean>;
  open_help(): Promise<boolean>;
  open_history_folder(id: string): Promise<boolean>;
  relocate_result(id: string): Promise<Result | null>;
}
declare global {
  interface Window {
    pywebview?: { api: NativeApi };
  }
}
