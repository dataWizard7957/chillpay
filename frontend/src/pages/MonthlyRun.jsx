import { useEffect, useState } from "react";
import { apiBaseUrl, apiRequest, errorText } from "../services/apiClient.js";
import {
  demoModeEnabled,
  setSelectedRunId,
  takeSelectedRunId,
} from "../services/runSelection.js";
import { ConfirmDialog, EmptyState, StatusPill } from "../components/ui.jsx";

const STEPS = [
  { key: "collect", label: "Collect" },
  { key: "review", label: "Review" },
  { key: "approve", label: "Approve" },
  { key: "invoiced", label: "Invoices" },
];

const NUMBER_FIELDS = [
  ["total_leaves", "Leaves"],
  ["extra_work_days", "Extra days"],
  ["comp_off", "Comp-off"],
  ["hours_worked", "Hours"],
  ["overtime_hours", "Overtime"],
  ["one_off_bonus", "Bonus"],
  ["advance_amount", "Advance"],
];

const PLACEHOLDER = "Raj: 3 leaves, 2 extra days, convert 2 to comp-off.";

function needsLastName(name) {
  return String(name || "").trim().split(/\s+/).filter(Boolean).length < 2;
}

function canCreateInvoices(run) {
  if (!run) {
    return false;
  }
  return (run.entries || []).every((row) => !needsLastName(row.member_name));
}

export function PayrollHistorySection({ role, onOpenRun }) {
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [denied, setDenied] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);

  function openRun(run) {
    setSelectedRunId(run.id);
    onOpenRun();
  }

  async function removeRun() {
    if (!pendingDelete) {
      return;
    }
    setError("");
    try {
      await apiRequest(`/runs/${pendingDelete.id}`, { method: "DELETE" });
      setRuns((current) => current.filter((run) => run.id !== pendingDelete.id));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setPendingDelete(null);
    }
  }

  useEffect(() => {
    let active = true;
    apiRequest("/runs")
      .then((body) => {
        if (active) {
          setRuns(body.runs || []);
        }
      })
      .catch((caught) => {
        if (!active) {
          return;
        }
        if (String(caught.message || "").includes("403")) {
          setDenied(true);
        } else {
          setError(errorText(caught));
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [role]);

  if (loading) {
    return (
      <section className="panel">
        <p className="status" role="status">
          Loading payroll runs…
        </p>
      </section>
    );
  }
  if (denied) {
    return (
      <section className="panel">
        <h2>Payroll</h2>
        <p className="alert" role="alert">
          You do not have access to payroll runs.
        </p>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Payroll</h2>
          <p>Past monthly runs, with month and status.</p>
        </div>
      </div>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {runs.length === 0 ? <EmptyState>No monthly runs yet.</EmptyState> : null}
      {runs.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Month</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    {run.year}-{String(run.month).padStart(2, "0")}
                  </td>
                  <td>
                    <StatusPill value={run.status} />
                  </td>
                  <td>
                    <button type="button" onClick={() => openRun(run)}>
                      Edit
                    </button>{" "}
                    <button
                      type="button"
                      className="secondary"
                      disabled={run.status === "invoiced"}
                      onClick={() => setPendingDelete(run)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete this monthly run"
        body="This removes the run and its rows. An invoiced month stays."
        confirmLabel="Delete"
        onCancel={() => setPendingDelete(null)}
        onConfirm={removeRun}
      />
    </section>
  );
}

export function MonthlyRunSection({ role }) {
  const [run, setRun] = useState(null);
  const [notes, setNotes] = useState("");
  const [year, setYear] = useState("2026");
  const [month, setMonth] = useState("9");
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [denied, setDenied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [manual, setManual] = useState(false);
  const [members, setMembers] = useState([]);
  const [previewId, setPreviewId] = useState(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [doneMessage, setDoneMessage] = useState("");
  const [confirmCreate, setConfirmCreate] = useState(false);
  const demo = demoModeEnabled();

  useEffect(() => {
    apiRequest("/extract/warmup").catch(() => {});
    const existing = takeSelectedRunId();
    if (existing) {
      loadRun(existing);
      return;
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    return () => {
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
    };
  }, [previewUrl]);

  const needsMemberList =
    manual ||
    (run && (run.unmatched_names || []).length > 0 && run.status === "collect") ||
    (run && run.status === "review" && !(run.entries || []).length);
  useEffect(() => {
    if (needsMemberList && members.length === 0 && role !== "finance") {
      apiRequest("/members")
        .then((body) => setMembers(body.members || []))
        .catch(() => {});
    }
  }, [needsMemberList, members.length, role]);

  async function loadRun(id) {
    setLoading(true);
    setDenied(false);
    setError("");
    try {
      const body = await apiRequest(`/runs/${id}`);
      setRun(body.run);
    } catch (caught) {
      if (String(caught.message || "").includes("403") || String(caught.message || "").includes("404")) {
        setDenied(true);
        setRun(null);
      } else {
        setError(errorText(caught));
      }
    } finally {
      setLoading(false);
    }
  }

  async function createRun({ confirm = false } = {}) {
    setError("");
    try {
      const body = await apiRequest("/runs", {
        method: "POST",
        body: { year: Number(year), month: Number(month), confirm },
      });
      setRun(body.run);
      setConfirmCreate(false);
    } catch (caught) {
      const detail = errorText(caught);
      if (!confirm && /already exists/i.test(detail) && !/invoiced/i.test(detail)) {
        setConfirmCreate(true);
      } else {
        setError(detail);
        setConfirmCreate(false);
      }
    }
  }

  async function readNotes(event) {
    event.preventDefault();
    if (!run) {
      return;
    }
    setReading(true);
    setError("");
    try {
      const body = await apiRequest(`/runs/${run.id}/extract`, {
        method: "POST",
        body: { notes_text: notes },
      });
      setRun(body.run);
      setManual((body.run.unmatched_names || []).length > 0 && !(body.run.entries || []).length);
    } catch (caught) {
      setError(errorText(caught));
      setManual(true);
    } finally {
      setReading(false);
    }
  }

  async function saveField(memberId, field, value) {
    if (!run) {
      return;
    }
    setError("");
    try {
      const body = await apiRequest(`/runs/${run.id}/entries/${memberId}`, {
        method: "PATCH",
        body: { [field]: value },
      });
      setRun(body.run);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function approve() {
    if (!run) {
      return;
    }
    setError("");
    try {
      const body = await apiRequest(`/runs/${run.id}/approve`, { method: "POST", body: {} });
      setRun(body.run);
      setDoneMessage("");
      if (!canCreateInvoices(body.run)) {
        return;
      }
      const invoiced = await apiRequest(`/runs/${run.id}/invoices`, { method: "POST", body: {} });
      setRun(invoiced.run);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function saveLastName(event) {
    event.preventDefault();
    if (!run) {
      return;
    }
    const form = new FormData(event.target);
    setError("");
    try {
      for (const row of run.entries || []) {
        if (!needsLastName(row.member_name)) {
          continue;
        }
        const last = String(form.get(`last-${row.member_id}`) || "").trim();
        if (!last) {
          continue;
        }
        await apiRequest(`/members/${row.member_id}`, {
          method: "PATCH",
          body: { full_name: `${String(row.member_name).trim()} ${last}` },
        });
      }
      const body = await apiRequest(`/runs/${run.id}`);
      setRun(body.run);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function createInvoices() {
    if (!run || !canCreateInvoices(run)) {
      return;
    }
    setError("");
    try {
      const body = await apiRequest(`/runs/${run.id}/invoices`, { method: "POST", body: {} });
      setRun(body.run);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function showMistake() {
    const first = run?.entries?.[0];
    if (!first) {
      return;
    }
    const extra = Number(first.extra_work_days || 0);
    await saveField(first.member_id, "comp_off", String(extra + 1));
  }

  async function preview(invoice) {
    setError("");
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    try {
      const response = await fetch(`${apiBaseUrl()}/invoices/${invoice.id}/pdf`, {
        credentials: "include",
        headers: { Accept: "application/pdf" },
      });
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }
      const blob = await response.blob();
      setPreviewId(invoice.id);
      setPreviewUrl(URL.createObjectURL(blob));
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function addManual(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    const memberId = form.get("member_id");
    if (!memberId || !run) {
      return;
    }
    const fields = {};
    for (const [field] of NUMBER_FIELDS) {
      fields[field] = form.get(field) || "0";
    }
    setError("");
    try {
      const body = await apiRequest(`/runs/${run.id}/entries/${Number(memberId)}`, {
        method: "PATCH",
        body: fields,
      });
      setRun(body.run);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  const status = run?.status || "collect";
  const hasErrors = (run?.entries || []).some((row) => (row.validation_errors || []).length);
  const showTable = run && (status === "review" || status === "approve") && (run.entries || []).length > 0;

  if (loading) {
    return (
      <section className="panel">
        <p className="status" role="status">
          Loading the monthly run…
        </p>
      </section>
    );
  }
  if (denied) {
    return (
      <section className="panel">
        <h2>Monthly run</h2>
        <p className="alert" role="alert">
          You do not have access to this run.
        </p>
      </section>
    );
  }

  return (
    <section className="panel monthly-run">
      <div className="page-head">
        <div>
          <h2>Monthly run</h2>
          <p>Read notes on this laptop, review values, then approve and invoice.</p>
        </div>
        {run ? <StatusPill value={run.status} /> : null}
      </div>
      <ol className="stepper" aria-label="Run steps">
        {STEPS.map((step) => (
          <li key={step.key} className={status === step.key ? "is-current" : ""}>
            {step.label}
          </li>
        ))}
      </ol>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {doneMessage ? (
        <p className="status" role="status">
          {doneMessage}
        </p>
      ) : null}

      {!run ? (
        <form
          className="form-grid"
          onSubmit={(event) => {
            event.preventDefault();
            createRun({ confirm: false });
          }}
        >
          <label>
            Year
            <input name="year" value={year} onChange={(event) => setYear(event.target.value)} required />
          </label>
          <label>
            Month
            <input name="month" value={month} onChange={(event) => setMonth(event.target.value)} required />
          </label>
          <div className="form-actions">
            <button type="submit">Start run</button>
          </div>
        </form>
      ) : null}

      {run && status === "collect" ? (
        <>
          <form onSubmit={readNotes}>
            <label>
              Notes
              <textarea
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder={PLACEHOLDER}
                rows={6}
              />
            </label>
            <button type="submit" disabled={reading}>
              {reading ? "Reading notes on this laptop..." : "Read notes"}
            </button>
          </form>
          {(run.unmatched_names || []).length ? (
            <p className="alert" role="alert">
              These names were not matched: {run.unmatched_names.join(", ")}.
            </p>
          ) : null}
          <p>
            <button type="button" className="secondary" onClick={() => setManual(true)}>
              Enter values manually
            </button>
          </p>
          {manual || (run.unmatched_names || []).length ? (
            <div className="manual-fallback">
              <p>You can enter values manually if the assistant is unavailable.</p>
              <form className="form-grid" onSubmit={addManual}>
                <label>
                  Member
                  <select name="member_id" required>
                    <option value="">Choose</option>
                    {members.map((member) => (
                      <option key={member.id} value={member.id}>
                        {member.full_name}
                      </option>
                    ))}
                  </select>
                </label>
                {NUMBER_FIELDS.map(([field, label]) => (
                  <label key={field}>
                    {label}
                    <input name={field} defaultValue="0" />
                  </label>
                ))}
                <div className="form-actions">
                  <button type="submit">Save manual row</button>
                </div>
              </form>
            </div>
          ) : null}
        </>
      ) : null}

      {run && (status === "review" || status === "approve") && (run.entries || []).some((row) => needsLastName(row.member_name)) ? (
        <form className="form-grid" onSubmit={saveLastName}>
          <p>Invoice numbers need a first and last name.</p>
          {(run.entries || [])
            .filter((row) => needsLastName(row.member_name))
            .map((row) => (
              <label key={row.member_id}>
                Last name for {row.member_name}
                <input name={`last-${row.member_id}`} required />
              </label>
            ))}
          <div className="form-actions">
            <button type="submit">Save name</button>
          </div>
        </form>
      ) : null}

      {run && status === "review" && (run.entries || []).length > 0 ? (
        <div className="approve-bar">
          {run.approval_note ? <p>{run.approval_note}</p> : null}
          {(run.invoice_blockers || []).length ? (
            <p className="alert" role="alert">{run.invoice_blockers.join(" ")}</p>
          ) : null}
          {run.can_approve ? (
            <>
              <button type="button" onClick={approve} disabled={hasErrors || (run.invoice_blockers || []).length > 0}>
                Approve and create invoices
              </button>
              {hasErrors ? <span>Fix validation errors before approval.</span> : null}
            </>
          ) : (
            <p>{run.who_needs_to_approve}</p>
          )}
        </div>
      ) : null}

      {showTable ? (
        <ReviewTable
          run={run}
          editable={status === "review"}
          onSave={saveField}
          demo={demo && status === "review"}
          onMistake={showMistake}
        />
      ) : null}

      {run && status === "review" && !(run.entries || []).length ? (
        <div className="manual-fallback">
          <p>No member was read from the notes. Add one here. The note must use the member's full name.</p>
          {members.length === 0 ? (
            <p className="alert" role="alert">
              Add a member first, then come back to this run.
            </p>
          ) : (
            <form className="form-grid" onSubmit={addManual}>
              <label>
                Member
                <select name="member_id" required>
                  <option value="">Choose</option>
                  {members.map((member) => (
                    <option key={member.id} value={member.id}>
                      {member.full_name}
                    </option>
                  ))}
                </select>
              </label>
              {NUMBER_FIELDS.map(([field, label]) => (
                <label key={field}>
                  {label}
                  <input name={field} defaultValue="0" />
                </label>
              ))}
              <div className="form-actions">
                <button type="submit">Save member</button>
              </div>
            </form>
          )}
        </div>
      ) : null}

      {run && status === "approve" ? (
        <div className="approve-bar">
          <p>Approved. Create invoices to finish.</p>
          <button type="button" onClick={createInvoices} disabled={!canCreateInvoices(run)}>
            Create invoices
          </button>
        </div>
      ) : null}

      {run && status === "invoiced" ? (
        <InvoicePreview invoices={run.invoices || []} previewId={previewId} previewUrl={previewUrl} onPreview={preview} />
      ) : null}

      {run && !(run.entries || []).length && status !== "collect" && status !== "review" ? (
        <EmptyState>No members in this run.</EmptyState>
      ) : null}

      <ConfirmDialog
        open={confirmCreate}
        title="A run for this month already exists"
        body="Start another run for the same month? The earlier run is not invoiced yet."
        confirmLabel="Start another run"
        onCancel={() => setConfirmCreate(false)}
        onConfirm={() => createRun({ confirm: true })}
      />
    </section>
  );
}

function ReviewTable({ run, editable, onSave, demo, onMistake }) {
  const [drafts, setDrafts] = useState({});

  useEffect(() => {
    setDrafts({});
  }, [run]);

  function valueFor(row, field) {
    const local = drafts[row.member_id];
    if (local && Object.prototype.hasOwnProperty.call(local, field)) {
      return local[field];
    }
    return row[field] ?? "";
  }

  function changeField(memberId, field, value) {
    setDrafts((current) => ({
      ...current,
      [memberId]: { ...(current[memberId] || {}), [field]: value },
    }));
  }

  return (
    <div>
      <div className="page-head">
        <h3>What I understood</h3>
        {demo ? (
          <button type="button" className="secondary" onClick={onMistake}>
            Show me a mistake
          </button>
        ) : null}
      </div>
      <p className="review-note">Numbers came from the note. Amount is calculated by code.</p>
      <div className="table-wrap table-scroll">
        <table className="review-table">
          <thead>
            <tr>
              <th>Member</th>
              {NUMBER_FIELDS.map(([field, label]) => (
                <th key={field}>{label}</th>
              ))}
              <th>Amount</th>
            </tr>
          </thead>
          <tbody>
            {(run.entries || []).map((row) => (
              <tr key={row.member_id}>
                <td>
                  {row.member_name}
                  {row.evidence ? <div className="how-read">{row.evidence}</div> : null}
                  {(row.validation_errors || []).map((item) => (
                    <div key={item} className="row-error">
                      {item}
                    </div>
                  ))}
                  {(row.warnings || []).map((item) => (
                    <div key={item} className="row-warn">
                      {item}
                    </div>
                  ))}
                </td>
                {NUMBER_FIELDS.map(([field, label]) => (
                  <td key={field}>
                    <input
                      value={valueFor(row, field)}
                      readOnly={!editable}
                      aria-label={`${label} for ${row.member_name}`}
                      onChange={(event) => changeField(row.member_id, field, event.target.value)}
                      onBlur={(event) => {
                        if (editable && event.target.value !== (row[field] ?? "")) {
                          onSave(row.member_id, field, event.target.value);
                        }
                      }}
                    />
                  </td>
                ))}
                <td className="review-amount">{row.amount_paid ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="totals-strip">
        <span>Members {run.totals?.members ?? 0}</span>
        <span>Gross {run.totals?.gross ?? "0"}</span>
        <span>TDS {run.totals?.tds ?? "0"}</span>
        <span>Amount payable {run.totals?.amount_paid ?? "0"}</span>
      </div>
    </div>
  );
}

function InvoicePreview({ invoices, previewId, previewUrl, onPreview }) {
  if (!invoices.length) {
    return <EmptyState>No invoices for this run.</EmptyState>;
  }
  return (
    <div className="invoice-preview">
      <ul className="invoice-list">
        {invoices.map((invoice) => (
          <li key={invoice.id}>
            <span>{invoice.invoice_number}</span>
            <button type="button" onClick={() => onPreview(invoice)}>
              Preview
            </button>
          </li>
        ))}
      </ul>
      {previewUrl ? (
        <iframe title={`Invoice ${previewId}`} src={previewUrl} className="pdf-frame" />
      ) : (
        <EmptyState>Select an invoice to preview.</EmptyState>
      )}
    </div>
  );
}
