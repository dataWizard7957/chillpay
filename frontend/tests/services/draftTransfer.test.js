import { afterEach, expect, test, vi } from "vitest";
import { peekReviewedPayroll, stashReviewedPayroll, takeReviewedPayroll } from "../../src/services/draftTransfer.js";

afterEach(() => {
  takeReviewedPayroll();
  vi.restoreAllMocks();
});

test("holds reviewed payroll fields in memory only", () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  stashReviewedPayroll({ member_id: 2, total_leaves: "3" });
  expect(peekReviewedPayroll()).toEqual({ member_id: 2, total_leaves: "3" });
  expect(takeReviewedPayroll()).toEqual({ member_id: 2, total_leaves: "3" });
  expect(takeReviewedPayroll()).toBeNull();
  expect(setItem).not.toHaveBeenCalled();
  expect(window.localStorage.length).toBe(0);
});
