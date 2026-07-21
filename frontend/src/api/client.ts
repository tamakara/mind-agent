export const API_PREFIX = "/api/v1";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Array<Record<string, unknown>>;
}

export interface ApiErrorEnvelope {
  error: ApiErrorBody;
  request_id: string;
}

export class WorkHubApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details?: Array<Record<string, unknown>>;

  constructor(options: {
    status: number;
    code: string;
    message: string;
    requestId: string | null;
    details?: Array<Record<string, unknown>>;
  }) {
    super(options.message);
    this.name = "WorkHubApiError";
    this.status = options.status;
    this.code = options.code;
    this.requestId = options.requestId;
    this.details = options.details;
  }
}

function isErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<ApiErrorEnvelope>;
  return (
    typeof candidate.request_id === "string" &&
    typeof candidate.error === "object" &&
    candidate.error !== null &&
    typeof candidate.error.code === "string" &&
    typeof candidate.error.message === "string"
  );
}

async function readJson(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${encodeURIComponent(name)}=`;
  const cookie = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null;
}

export class ApiClient {
  constructor(private readonly baseUrl = "") {}

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    const headers = new Headers(init.headers);
    if (
      init.body !== undefined &&
      !(init.body instanceof FormData) &&
      !headers.has("Content-Type")
    ) {
      headers.set("Content-Type", "application/json");
    }
    const method = (init.method ?? "GET").toUpperCase();
    if (UNSAFE_METHODS.has(method) && !headers.has("X-CSRF-Token")) {
      const csrfToken = readCookie("workhub_csrf");
      if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    }

    const response = await fetch(`${this.baseUrl}${API_PREFIX}${normalizedPath}`, {
      ...init,
      headers,
      credentials: "same-origin",
    });
    const payload = await readJson(response);
    if (response.ok) return payload as T;

    const headerRequestId = response.headers.get("X-Request-ID");
    if (isErrorEnvelope(payload)) {
      throw new WorkHubApiError({
        status: response.status,
        code: payload.error.code,
        message: payload.error.message,
        requestId: payload.request_id || headerRequestId,
        details: payload.error.details,
      });
    }
    throw new WorkHubApiError({
      status: response.status,
      code: "unexpected_response",
      message: "服务器返回了无法识别的错误响应。",
      requestId: headerRequestId,
    });
  }
}

export const apiClient = new ApiClient();
