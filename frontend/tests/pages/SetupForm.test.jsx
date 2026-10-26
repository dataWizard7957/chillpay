import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SetupForm } from "../../src/pages/SetupForm.jsx";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("sends only provided company fields", async () => {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ setup_required: false, user: { id: 1, email: "hr@example.test", role: "hr" } }),
  }));
  vi.stubGlobal("fetch", fetchMock);
  const onComplete = vi.fn();
  render(<SetupForm onComplete={onComplete} />);
  fireEvent.change(screen.getByLabelText("Admin email"), { target: { value: "hr@example.test" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "longenough" } });
  fireEvent.change(screen.getByLabelText("Company name"), { target: { value: "Example Co" } });
  fireEvent.click(screen.getByRole("button", { name: "Create first admin" }));
  await waitFor(() => expect(onComplete).toHaveBeenCalled());
  const [, options] = fetchMock.mock.calls[0];
  expect(JSON.parse(options.body)).toEqual({
    email: "hr@example.test",
    password: "longenough",
    company_name: "Example Co",
  });
});
