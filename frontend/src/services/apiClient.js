const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

let csrfToken = "";
let sessionPromise = null;

export function apiBaseUrl() {
  return import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL;
}

export function rememberCsrfToken(token) {
  csrfToken = token || "";
}

export function adoptSession(user) {
  sessionPromise = Promise.resolve(user);
}

export function clearSession() {
  csrfToken = "";
  sessionPromise = null;
}

export function loadSession() {
  if (sessionPromise === null) {
    sessionPromise = apiRequest("/auth/me")
      .then((body) => {
        rememberCsrfToken(body.csrf_token);
        return body.user;
      })
      .catch((error) => {
        sessionPromise = null;
        throw error;
      });
  }
  return sessionPromise;
}

async function refreshCsrfToken() {
  const response = await fetch(`${apiBaseUrl()}/auth/me`, {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "include",
  });
  if (!response.ok) {
    return false;
  }
  const body = await response.json();
  rememberCsrfToken(body.csrf_token);
  return Boolean(csrfToken);
}

function csrfRequired(path, method) {
  return method !== "GET" && path !== "/auth/login" && path !== "/setup";
}

export async function apiRequest(path, options = {}, retried = false) {
  const method = options.method || "GET";
  if (csrfRequired(path, method) && !csrfToken) {
    await refreshCsrfToken();
  }
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (csrfRequired(path, method) && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method,
    headers,
    credentials: "include",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      detail = "";
    }
    const csrfFailed = response.status === 403 && /csrf/i.test(detail);
    if (csrfFailed && !retried && csrfRequired(path, method)) {
      const refreshed = await refreshCsrfToken();
      if (refreshed) {
        return apiRequest(path, options, true);
      }
    }
    const error = new Error(`Request failed with status ${response.status}`);
    error.detail = detail;
    throw error;
  }

  if (response.status === 204) {
    return null;
  }
  return response.json();
}

export async function downloadInvoice(invoiceId, filename) {
  const response = await fetch(`${apiBaseUrl()}/invoices/${invoiceId}/file`, {
    credentials: "include",
    headers: { Accept: "application/pdf" },
  });
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function loadHealthStatus() {
  return apiRequest("/health");
}

export function errorText(error) {
  return error?.detail || error?.message || "The request failed.";
}
