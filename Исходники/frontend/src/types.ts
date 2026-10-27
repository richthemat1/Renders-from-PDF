export interface ImageEntry {
  page: number;
  sequence: number;
  title: string | null;
  folder: string;
  file: string;
  preview: string | null;
  variant: number | null;
  pdf_page: number;
  status: "named" | "review" | "unsigned";
  size: [number, number];
  reason?: string;
  removed?: boolean;
  /** Saved by the automatic filter as a service element; restorable like a removed card. */
  suppressed?: boolean;
  suppression_code?: string;
  copy_of?: number;
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
    variant: number | null;
    pages: number;
  }[];
  variant_names?: Record<string, string>;
  folder_labels?: Record<string, string>;
  suppressed?: unknown[];
  counts_by_status: Record<string, number>;
  warnings: unknown[];
  errors: unknown[];
  can_undo_save?: boolean;
  edits?: { backup?: string; action?: string; kind?: string }[];
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
  run(): void | Promise<void>;
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
