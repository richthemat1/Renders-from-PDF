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
  group?: string;
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
  counts_by_status: Record<string, number>;
  warnings: unknown[];
  errors: unknown[];
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
export interface Snapshot {
  version: string;
  busy: boolean;
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
  history: HistoryItem[];
  current_output: string | null;
  warning: string;
}
export interface NativeApi {
  choose_pdfs(): Promise<Snapshot>;
  choose_result(): Promise<Result | null>;
  choose_output(): Promise<Snapshot>;
  open_result(id: string, relative?: string): Promise<boolean>;
  open_log(): Promise<boolean>;
}
declare global {
  interface Window {
    pywebview?: { api: NativeApi };
  }
}
