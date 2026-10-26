import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { LoginForm } from "../../src/pages/LoginForm.jsx";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("signs in without writing the password to local storage", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  const fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      csrf_token: "csrf-from-login",
      user: { id: 1, email: "hr@example.test", role: "hr" },
    }),
  }));
  vi.stubGlobal("fetch", fetchMock);
  const onSuccess = vi.fn();

  render(<LoginForm onSuccess={onSuccess} />);
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "HR@example.test" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-horse" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

  await waitFor(() => expect(onSuccess).toHaveBeenCalledWith({ id: 1, email: "hr@example.test", role: "hr" }));
  expect(setItem).not.toHaveBeenCalled();
  expect(window.localStorage.length).toBe(0);
  const [, options] = fetchMock.mock.calls[0];
  expect(options.credentials).toBe("include");
  expect(options.headers["X-CSRF-Token"]).toBeUndefined();
  expect(JSON.parse(options.body)).toEqual({
    email: "hr@example.test",
    password: "correct-horse",
  });
});
