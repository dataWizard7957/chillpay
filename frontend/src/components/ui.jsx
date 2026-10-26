export function StatusPill({ value }) {
  const key = String(value || "").toLowerCase().replace(/\s+/g, "-");
  return <span className={`pill pill-${key}`}>{value}</span>;
}

export function EmptyState({ children }) {
  return (
    <p className="empty" role="status">
      {children}
    </p>
  );
}

export function ConfirmDialog({ open, title, body, confirmLabel = "Confirm", onCancel, onConfirm }) {
  if (!open) {
    return null;
  }
  return (
    <div className="dialog-backdrop" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h3 id="confirm-title">{title}</h3>
        <p>{body}</p>
        <div className="toolbar">
          <button type="button" className="secondary" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export const NAV_ITEMS = ["This month", "People", "Records", "Setup"];
