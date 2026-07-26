import type { ApiErrorPayload } from "./types";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const DEFAULT_API_BASE_URL = "/api/v1";
const CSRF_COOKIE_NAME = "exec_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor({
    message,
    status,
    code = "request_failed",
    requestId = null,
    details,
  }: {
    message: string;
    status: number;
    code?: string;
    requestId?: string | null;
    details?: unknown;
  }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

export type ApiRequestOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | Record<string, unknown> | null;
  skipCsrf?: boolean;
};

function normalizeBaseUrl(value: string | undefined) {
  const normalized = value?.trim() || DEFAULT_API_BASE_URL;
  return normalized.replace(/\/+$/, "");
}

function cookieValue(name: string) {
  if (typeof document === "undefined") return null;
  const prefix = `${encodeURIComponent(name)}=`;
  const part = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  if (!part) return null;
  try {
    return decodeURIComponent(part.slice(prefix.length));
  } catch {
    return null;
  }
}

function isBodyInit(value: unknown): value is BodyInit {
  return (
    typeof value === "string" ||
    value instanceof FormData ||
    value instanceof URLSearchParams ||
    value instanceof Blob ||
    value instanceof ArrayBuffer ||
    ArrayBuffer.isView(value)
  );
}

async function parseResponseBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return text;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError({
      message: "服务器返回了无法解析的响应。",
      status: response.status,
      code: "invalid_response",
      requestId: response.headers.get("x-request-id"),
    });
  }
}

export class ApiClient {
  readonly baseUrl: string;
  private csrfToken: string | null = null;

  constructor(baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL) {
    this.baseUrl = normalizeBaseUrl(baseUrl);
  }

  setCsrfToken(token: string | null | undefined) {
    if (token) this.csrfToken = token;
  }

  clearSessionState() {
    this.csrfToken = null;
  }

  async request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    const method = (options.method ?? "GET").toUpperCase();
    const headers = new Headers(options.headers);
    let body = options.body;

    if (body != null && !isBodyInit(body)) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(body);
    }
    headers.set("Accept", "application/json");

    if (!SAFE_METHODS.has(method) && !options.skipCsrf) {
      const csrf = this.csrfToken ?? cookieValue(CSRF_COOKIE_NAME);
      if (!csrf) {
        throw new ApiError({
          message: "安全令牌已失效，请重新登录。",
          status: 403,
          code: "csrf_token_missing",
        });
      }
      headers.set(CSRF_HEADER_NAME, csrf);
    }

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`, {
        ...options,
        method,
        headers,
        body: body as BodyInit | null | undefined,
        credentials: "include",
        cache: "no-store",
      });
    } catch (error) {
      throw new ApiError({
        message: "暂时无法连接企业服务，请检查本机服务状态后重试。",
        status: 0,
        code: "network_error",
        details: error,
      });
    }

    const payload = await parseResponseBody(response);
    if (!response.ok) {
      const apiError = (payload ?? {}) as ApiErrorPayload;
      throw new ApiError({
        message: apiError.error?.message ?? `请求失败（${response.status}）`,
        status: response.status,
        code: apiError.error?.code ?? "request_failed",
        requestId:
          apiError.error?.request_id ?? response.headers.get("x-request-id"),
        details: apiError.error?.details,
      });
    }

    if (payload && typeof payload === "object" && "csrf_token" in payload) {
      this.setCsrfToken((payload as { csrf_token?: string }).csrf_token);
    }
    return payload as T;
  }
}

export function humanizeApiError(error: unknown) {
  if (error instanceof ApiError) {
    const reference = error.requestId ? `（请求编号：${error.requestId}）` : "";
    return `${error.message}${reference}`;
  }
  if (error instanceof Error && error.message) return error.message;
  return "服务暂时不可用，请稍后重试。";
}

export const apiClient = new ApiClient();
