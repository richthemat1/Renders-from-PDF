import type { NativeApi } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(new URL(`api/${path}`, document.baseURI), {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error(
      "Локальный сервер не ответил. Запустите приложение и повторите действие.",
    );
  }
  if (!response.ok)
    throw new ApiError(
      value.error || "Не удалось выполнить действие.",
      response.status,
    );
  return value as T;
}
export async function native<K extends keyof NativeApi>(
  name: K,
  ...args: Parameters<NativeApi[K]>
): Promise<Awaited<ReturnType<NativeApi[K]>>> {
  if (!window.pywebview?.api)
    throw new Error(
      "Выбор и открытие файлов доступны в окне приложения. Запустите «Фото из PDF».",
    );
  const fn = window.pywebview.api[name] as (
    ...params: Parameters<NativeApi[K]>
  ) => ReturnType<NativeApi[K]>;
  return (await fn(...args)) as Awaited<ReturnType<NativeApi[K]>>;
}
export function errorText(error: unknown): string {
  if (error && typeof error === "object" && "message" in error)
    return String(error.message);
  return String(error);
}
export function fileUrl(base: string, relative: string): string {
  return (
    base.replace(/\/?$/, "/") +
    relative.replaceAll("\\", "/").split("/").map(encodeURIComponent).join("/")
  );
}
export function dateLabel(value: string): string {
  return new Date(value).toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
