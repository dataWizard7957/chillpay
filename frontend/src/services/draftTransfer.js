/** In-memory hand-off from reviewed extraction to the payroll form. Not persisted. */

let pendingPayroll = null;

export function stashReviewedPayroll(fields) {
  pendingPayroll = { ...fields };
}

export function takeReviewedPayroll() {
  const value = pendingPayroll;
  pendingPayroll = null;
  return value;
}

export function peekReviewedPayroll() {
  return pendingPayroll;
}
