import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { MonthlyRunSection } from "../../src/pages/MonthlyRun.jsx";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("collect step reads notes without logging the note text", async () => {
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url);
    if (path.endsWith("/extract/warmup")) {
      return { ok: true, status: 200, json: async () => ({ started: true }) };
    }
    if (path.endsWith("/runs") && (options.method || "GET") === "POST") {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          run: {
            id: 1,
            year: 2026,
            month: 9,
            status: "collect",
            can_approve: false,
            entries: [],
            totals: { members: 0, gross: "0", tds: "0", amount_paid: "0" },
            invoices: [],
          },
        }),
      };
    }
    if (path.endsWith("/runs/1/extract")) {
      return {
        ok: false,
        status: 503,
        json: async () => ({ detail: "Ollama is not running on 127.0.0.1:11434" }),
      };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  const log = vi.spyOn(console, "log").mockImplementation(() => {});

  render(<MonthlyRunSection role="hr" />);
  fireEvent.change(screen.getByLabelText("Year"), { target: { value: "2026" } });
  fireEvent.change(screen.getByLabelText("Month"), { target: { value: "9" } });
  fireEvent.click(screen.getByRole("button", { name: "Start run" }));
  await waitFor(() => expect(screen.getByLabelText("Notes")).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Notes"), {
    target: { value: "Raj: 3 leaves, 2 extra days, convert 2 to comp-off." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Read notes" }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("Ollama is not running on 127.0.0.1:11434"),
  );
  expect(log).not.toHaveBeenCalled();
  const extractCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/runs/1/extract"));
  expect(extractCall).toBeTruthy();
});
