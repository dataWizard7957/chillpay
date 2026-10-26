import { afterEach, expect, test, vi } from "vitest";
import { apiRequest } from "../../src/services/apiClient.js";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("does not write secrets to local storage", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok" }),
    })),
  );

  await expect(apiRequest("/health")).resolves.toEqual({ status: "ok" });
  expect(setItem).not.toHaveBeenCalled();
  expect(window.localStorage.length).toBe(0);
});

test("retries a mutating request after refreshing a missing CSRF token", async () => {
  const fetchMock = vi.fn(async (url, options) => {
    if (String(url).endsWith("/auth/me")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ csrf_token: "fresh-csrf", user: { id: 1, role: "hr" } }),
      };
    }
    if (options?.headers?.["X-CSRF-Token"] === "fresh-csrf") {
      return { ok: true, status: 200, json: async () => ({ chat: { reply: "ok" } }) };
    }
    return {
      ok: false,
      status: 403,
      json: async () => ({ detail: "CSRF token was missing or invalid" }),
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  await expect(apiRequest("/chat", { method: "POST", body: { message: "hello" } })).resolves.toEqual({
    chat: { reply: "ok" },
  });
  expect(fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/auth/me"))).toBe(true);
});

test("reports an API error without storing the response", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 500,
      json: async () => ({ detail: "secret payroll" }),
    })),
  );

  await expect(apiRequest("/health")).rejects.toThrow("Request failed with status 500");
  expect(setItem).not.toHaveBeenCalled();
});
