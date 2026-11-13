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
  /** Checked by a person and left as it is: it no longer waits for a decision. */
  reviewed?: boolean;
  size: [number, number];
  reason?: string;
  /** Captions the parser could not choose between («На проверку»). */
  candidates?: string[];
  removed?: boolean;
  /** Saved by the automatic filter as a service element; restorable like a removed card. */
  suppressed?: boolean;
  suppression_code?: string;
  copy_of?: number;
  /** Clockwise degrees the card was turned by hand («Повернуть»); its PDF page turns, the pixels stay. */
  turn?: 90 | 180 | 270;
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
  /** A cutout drawn as the page shows it, with the marks over its picture. */
  marks?: boolean;
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
  /** Source pages read turned, by page number: a scan of a sheet lying on its side, in clockwise degrees. */
  upright_pages?: Record<string, number>;
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
  /** What is drawn over a picture after it, by picture index; only pictures with marks are listed. */
  marks?: Record<string, Rect[]>;
}
export interface Result {
  id: string;
  base_url: string;
  title: string;
  report: Report;
  /** Where clean copies of saved variants were put, by variant key; absent from older servers. */
  delivery?: Record<string, VariantCopy[]>;
}
/** A variant's PDF put straight into a folder of the user's; `auto` — the folder set in the settings. */
export interface VariantCopy {
  folder: string;
  file: string;
  auto: boolean;
}
export interface VariantExport {
  folder: string;
  /** Full paths of the PDFs written to the chosen folder. */
  saved: string[];
  errors: string[];
  result: Result;
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
  /** Names of the files of the same selection never processed; absent from older servers. */
  new?: string[];
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
    /** Every file in work, in queue order: several PDFs of a queue run at once since 3.42. */
    active_files?: { name: string; index: number; total: number; phase: string; progress: number }[];
    /** Result folders of this queue in order; absent from older servers. */
    outputs?: string[];
    /** Files a stopped queue did not finish, which «Продолжить оставшиеся» processes. */
    remaining?: number;
    /** Files of the running queue not started yet, in queue order. */
    waiting?: string[];
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
    /** The folder clean variant PDFs of every presentation go to; empty when switched off. */
    delivery?: string;
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
  clipboard_text(): Promise<string>;
  choose_delivery(): Promise<Snapshot>;
  clear_delivery(): Promise<Snapshot>;
  /** Asks for a folder; null when the dialog was closed. `variants`: keys, or null for every variant. */
  export_variants(id: string, variants: string[] | null): Promise<VariantExport | null>;
  reveal_variant(id: string, variant: string): Promise<boolean>;
  /** Closes the window after the page asked about unsaved edits itself. */
  close_window(): Promise<boolean>;
  /** Scales the whole window as browser zoom does; false outside the desktop window. */
  set_zoom(factor: number): Promise<boolean>;
}
declare global {
  interface Window {
    pywebview?: { api: NativeApi };
    /** Called by the window on «×» while edits are unsaved: true when the page shows its own question. */
    pdfPhotosRequestClose?: () => boolean;
  }
}
