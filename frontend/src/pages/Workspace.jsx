import { useEffect, useState } from "react";
import { StartScreen } from "./StartScreen.jsx";
import { LoginForm } from "./LoginForm.jsx";
import {
  apiRequest,
  clearSession,
  downloadInvoice,
  errorText,
  loadSession,
} from "../services/apiClient.js";
import { setSelectedRunId } from "../services/runSelection.js";
import { ExtraSections } from "./ExtraSections.jsx";
import { MonthlyRunSection, PayrollHistorySection } from "./MonthlyRun.jsx";
import { ConfirmDialog, EmptyState, NAV_ITEMS, StatusPill } from "../components/ui.jsx";

function pagesFor(role) {
  if (role === "finance") {
    return ["Records"];
  }
  if (role === "member" || role === "department_head") {
    return ["This month", "Records"];
  }
  return ["This month", "People", "Records", "Setup"];
}

function tabsFor(page, role) {
  if (page === "This month") {
    const tabs = ["Notes", "Review"];
    if (role === "hr" || role === "department_head") {
      tabs.push("Decisions");
    }
    return tabs;
  }
  if (page === "People") {
    return ["Members", "Departments", "Projects"];
  }
  if (page === "Records") {
    return ["Past months", "Invoices", "Reports", "Advances"];
  }
  return ["Company", "Accounts", "Head leave"];
}

export function Workspace() {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [page, setPage] = useState("This month");
  const [tab, setTab] = useState("Notes");
  const visiblePages = pagesFor(user?.role);
  const tabs = tabsFor(page, user?.role);

  useEffect(() => {
    if (user && !visiblePages.includes(page)) {
      const next = visiblePages[0];
      setPage(next);
      setTab(tabsFor(next, user.role)[0]);
    }
  }, [page, user, visiblePages]);

  useEffect(() => {
    let active = true;
    apiRequest("/setup/status")
      .catch(() => null)
      .then(() => loadSession())
      .then((current) => {
        if (active) {
          setUser(current);
        }
      })
      .catch(() => {
        if (active) {
          setUser(null);
        }
      })
      .finally(() => {
        if (active) {
          setReady(true);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function logout() {
    try {
      await apiRequest("/auth/logout", { method: "POST", body: {} });
    } catch {
      // The cookie is cleared locally even if the server already expired it.
    }
    clearSession();
    setUser(null);
  }

  function openReview(runId) {
    if (runId) {
      setSelectedRunId(runId);
    }
    setPage("This month");
    setTab("Review");
  }

  if (!user) {
    return (
      <div className="guest-shell">
        <StartScreen />
        {!ready ? <p className="status" role="status">Preparing your workspace…</p> : null}
        {ready ? <LoginForm onSuccess={setUser} /> : null}
      </div>
    );
  }

  return (
    <div className="app-frame">
      <StartScreen compact />
      <nav className="nav-rail" aria-label="Workspace">
        <div className="nav-mark">
          <strong>Chill Pay</strong>
        </div>
        <div className="nav-list">
          {NAV_ITEMS.filter((name) => visiblePages.includes(name)).map((name) => (
            <button
              key={name}
              type="button"
              className={page === name ? "nav-item is-active" : "nav-item"}
              aria-current={page === name ? "page" : undefined}
              onClick={() => {
                setPage(name);
                setTab(tabsFor(name, user.role)[0]);
              }}
            >
              {name}
            </button>
          ))}
        </div>
        <div className="nav-account">
          <p className="who">
            <strong>{user.email}</strong>
            <span>{roleLabel(user.role)}</span>
          </p>
          <button type="button" className="nav-item" onClick={logout}>
            Sign out
          </button>
        </div>
      </nav>
      <div className="workspace">
        <div className="page">
          {page === "This month" ? <DashboardSection /> : null}
          <Subnav tabs={tabs} tab={tabs.includes(tab) ? tab : tabs[0]} onChange={setTab} />
          {page === "This month" && (tabs.includes(tab) ? tab : tabs[0]) === "Notes" ? (
            <ExtraSections
              section="Chat"
              role={user.role}
              onGoToPayroll={openReview}
              onGoToLeave={() => {}}
              onGoToMonthlyRun={openReview}
            />
          ) : null}
          {page === "This month" && (tabs.includes(tab) ? tab : tabs[0]) === "Review" ? (
            <MonthlyRunSection role={user.role} />
          ) : null}
          {page === "This month" && (tabs.includes(tab) ? tab : tabs[0]) === "Decisions" ? (
            <ApprovalsSection role={user.role} />
          ) : null}
          {page === "People" && (tabs.includes(tab) ? tab : tabs[0]) === "Members" ? <MembersSection /> : null}
          {page === "People" && (tabs.includes(tab) ? tab : tabs[0]) !== "Members" ? (
            <ExtraSections
              section={(tabs.includes(tab) ? tab : tabs[0]) === "Departments" ? "Departments" : "Projects"}
              role={user.role}
              onGoToPayroll={openReview}
              onGoToLeave={() => {
                setPage("Setup");
                setTab("Head leave");
              }}
              onGoToMonthlyRun={openReview}
            />
          ) : null}
          {page === "Records" && (tabs.includes(tab) ? tab : tabs[0]) === "Past months" ? (
            <PayrollHistorySection role={user.role} onOpenRun={openReview} />
          ) : null}
          {page === "Records" && (tabs.includes(tab) ? tab : tabs[0]) === "Invoices" ? <InvoicesSection /> : null}
          {page === "Records" && (tabs.includes(tab) ? tab : tabs[0]) === "Advances" ? <AdvancesSection role={user.role} /> : null}
          {page === "Records" && (tabs.includes(tab) ? tab : tabs[0]) === "Reports" ? (
            <ExtraSections section="Reports" role={user.role} onGoToPayroll={openReview} onGoToLeave={() => {}} onGoToMonthlyRun={openReview} />
          ) : null}
          {page === "Setup" && (tabs.includes(tab) ? tab : tabs[0]) === "Head leave" ? <LeaveSection role={user.role} /> : null}
          {page === "Setup" && (tabs.includes(tab) ? tab : tabs[0]) !== "Head leave" ? (
            <ExtraSections
              section={(tabs.includes(tab) ? tab : tabs[0]) === "Accounts" ? "Users" : "Settings"}
              role={user.role}
              onGoToPayroll={openReview}
              onGoToLeave={() => setTab("Head leave")}
              onGoToMonthlyRun={openReview}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Subnav({ tabs, tab, onChange }) {
  return (
    <div className="subnav" role="tablist">
      {tabs.map((name) => (
        <button
          key={name}
          type="button"
          role="tab"
          aria-selected={name === tab}
          className={name === tab ? "is-active" : ""}
          onClick={() => onChange(name)}
        >
          {name}
        </button>
      ))}
    </div>
  );
}

function roleLabel(role) {
  return (
    {
      hr: "HR",
      department_head: "Department head",
      member: "Member",
      finance: "Finance",
    }[role] || role
  );
}

function useLoader(load) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    load()
      .then((body) => {
        if (active) {
          setData(body);
          setError("");
        }
      })
      .catch((caught) => {
        if (active) {
          setError(errorText(caught));
        }
      });
    return () => {
      active = false;
    };
  }, [version]);

  return { data, error, setError, reload: () => setVersion((value) => value + 1) };
}

function DashboardSection() {
  const { data, error } = useLoader(() => apiRequest("/reports/dashboard"));
  const board = data?.dashboard;
  return (
    <section>
      <div className="page-head">
        <div>
          <h2>Dashboard</h2>
          <p>Overview of members, approvals, invoices, and advances for the current period.</p>
        </div>
      </div>
      {error ? <p className="alert" role="alert">{error}</p> : null}
      {!board && !error ? <p className="status" role="status">Loading summaries…</p> : null}
      {board ? (
        <div className="panel-grid">
          <article className="metric">
            <span>Current period</span>
            <strong>
              {board.current_period.year}-{board.current_period.month}
            </strong>
          </article>
          <article className="metric">
            <span>Active members</span>
            <strong>{board.active_members}</strong>
          </article>
          <article className="metric">
            <span>Pending approvals</span>
            <strong>{board.pending_approvals}</strong>
          </article>
          <article className="metric">
            <span>Approved entries</span>
            <strong>{board.approved_entries}</strong>
          </article>
          <article className="metric">
            <span>Invoices generated</span>
            <strong>{board.invoices_generated}</strong>
          </article>
          <article className="metric">
            <span>Outstanding advances</span>
            <strong>{board.outstanding_advances}</strong>
          </article>
          <article className="metric">
            <span>Awaiting HR review</span>
            <strong>{board.needs_hr_review}</strong>
          </article>
        </div>
      ) : null}
    </section>
  );
}

function MembersSection() {
  const { data, error, setError, reload } = useLoader(() =>
    Promise.all([apiRequest("/members"), apiRequest("/departments")]).then(([members, departments]) => ({
      members: members.members,
      departments: departments.departments,
    })),
  );
  const [form, setForm] = useState({
    full_name: "",
    email: "",
    department_id: "",
    member_type: "contract",
    joining_date: "",
    monthly_payment: "",
    default_project_allowance: "0.00",
  });

  function update(event) {
    setForm({ ...form, [event.target.name]: event.target.value });
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await apiRequest("/members", {
        method: "POST",
        body: {
          full_name: form.full_name,
          email: form.email,
          department_id: Number(form.department_id),
          member_type: form.member_type,
          joining_date: form.joining_date,
          pay_term: {
            effective_from: form.joining_date,
            pay_basis: "monthly",
            monthly_payment: form.monthly_payment,
            default_project_allowance: form.default_project_allowance,
            default_project_bonus: "0.00",
          },
        },
      });
      reload();
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Members</h2>
          <p>Manage employee directory and compensation details. Sensitive fields are restricted to HR administrators.</p>
        </div>
      </div>
      {error ? <p className="alert" role="alert">{error}</p> : null}
      <form onSubmit={submit}>
        <label>
          Full name
          <input name="full_name" value={form.full_name} onChange={update} required />
        </label>
        <label>
          Email
          <input name="email" type="email" value={form.email} onChange={update} required />
        </label>
        <label>
          Department
          <select name="department_id" value={form.department_id} onChange={update} required>
            <option value="">Choose</option>
            {(data?.departments || []).map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Member type
          <select name="member_type" value={form.member_type} onChange={update}>
            <option value="contract">Contract</option>
            <option value="employee">Employee</option>
            <option value="trainee">Trainee</option>
          </select>
        </label>
        <label>
          Joining date
          <input name="joining_date" type="date" value={form.joining_date} onChange={update} required />
        </label>
        <label>
          Monthly payment
          <input name="monthly_payment" value={form.monthly_payment} onChange={update} required />
        </label>
        <label>
          Default project allowance
          <input
            name="default_project_allowance"
            value={form.default_project_allowance}
            onChange={update}
            required
          />
        </label>
        <button type="submit">Save member</button>
      </form>
      <MemberTable
        members={data?.members || []}
        departments={data?.departments || []}
        onSaved={reload}
      />
    </section>
  );
}

function MemberTable({ members, departments, onSaved }) {
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [edit, setEdit] = useState(null);
  const [payTerms, setPayTerms] = useState([]);
  const [error, setError] = useState("");

  function shownTerm(member, terms) {
    const rows = terms.length ? terms : member.current_pay_term ? [member.current_pay_term] : [];
    return [...rows].sort((left, right) => left.effective_from.localeCompare(right.effective_from))[0] || {};
  }

  function formFrom(member, terms) {
    const term = shownTerm(member, terms);
    return {
      full_name: member.full_name || "",
      email: member.email || "",
      address: member.address || "",
      work_description: member.work_description || "",
      department_id: String(member.department_id || ""),
      member_type: member.member_type || "employee",
      joining_date: member.joining_date || "",
      pay_start: term.effective_from || member.joining_date || "",
      monthly_payment: term.monthly_payment || "",
      default_project_allowance: term.default_project_allowance || "0.00",
    };
  }

  async function open(member) {
    setError("");
    setSelected(member.id);
    try {
      const [body, termsBody] = await Promise.all([
        apiRequest(`/members/${member.id}`),
        apiRequest(`/members/${member.id}/pay-terms`),
      ]);
      const terms = termsBody.pay_terms || [];
      setDetail(body.member);
      setPayTerms(terms);
      setEdit(formFrom(body.member, terms));
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  function changeEdit(event) {
    setEdit({ ...edit, [event.target.name]: event.target.value });
  }

  async function saveEdit(event) {
    event.preventDefault();
    if (!detail || !edit) {
      return;
    }
    setError("");
    try {
      await apiRequest(`/members/${detail.id}`, {
        method: "PATCH",
        body: {
          full_name: edit.full_name,
          email: edit.email,
          department_id: Number(edit.department_id),
          member_type: edit.member_type,
          joining_date: edit.joining_date,
          address: edit.address,
          work_description: edit.work_description,
        },
      });
      const term = payTerms.find((row) => row.effective_from === edit.pay_start);
      if (term) {
        const amountsChanged =
          edit.monthly_payment !== (term.monthly_payment || "") ||
          edit.default_project_allowance !== (term.default_project_allowance || "0.00");
        if (amountsChanged) {
          await apiRequest(`/members/${detail.id}/pay-terms/${term.id}`, {
            method: "PATCH",
            body: {
              monthly_payment: edit.monthly_payment,
              default_project_allowance: edit.default_project_allowance || "0.00",
              default_project_bonus: term.default_project_bonus || "0.00",
            },
          });
        }
      } else {
        await apiRequest(`/members/${detail.id}/pay-terms`, {
          method: "POST",
          body: {
            effective_from: edit.pay_start,
            pay_basis: "monthly",
            monthly_payment: edit.monthly_payment,
            default_project_allowance: edit.default_project_allowance || "0.00",
            default_project_bonus: "0.00",
          },
        });
      }
      const [body, termsBody] = await Promise.all([
        apiRequest(`/members/${detail.id}`),
        apiRequest(`/members/${detail.id}/pay-terms`),
      ]);
      const terms = termsBody.pay_terms || [];
      setDetail(body.member);
      setPayTerms(terms);
      setEdit(formFrom(body.member, terms));
      onSaved();
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  return (
    <>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Type</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {members.map((member) => (
            <tr key={member.id}>
              <td>{member.full_name}</td>
              <td>{member.email}</td>
              <td>{member.member_type}</td>
              <td>
                <button type="button" onClick={() => open(member)}>
                  Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {detail && edit && selected === detail.id ? (
        <form className="form-grid" onSubmit={saveEdit}>
          <h3>Edit {detail.full_name}</h3>
          <label>
            Full name
            <input name="full_name" value={edit.full_name} onChange={changeEdit} required />
          </label>
          <label>
            Email
            <input name="email" type="email" value={edit.email} onChange={changeEdit} required />
          </label>
          <label>
            Department
            <select name="department_id" value={edit.department_id} onChange={changeEdit} required>
              {(departments || []).map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Member type
            <select name="member_type" value={edit.member_type} onChange={changeEdit}>
              <option value="contract">Contract</option>
              <option value="employee">Employee</option>
              <option value="trainee">Trainee</option>
            </select>
          </label>
          <label>
            Joining date
            <input name="joining_date" type="date" value={edit.joining_date} onChange={changeEdit} required />
          </label>
          <label>
            Address
            <input name="address" value={edit.address} onChange={changeEdit} />
          </label>
          <label>
            Work description
            <input name="work_description" value={edit.work_description} onChange={changeEdit} />
          </label>
          <label>
            Pay start
            <input name="pay_start" type="date" value={edit.pay_start} onChange={changeEdit} required />
          </label>
          <label>
            Monthly payment
            <input name="monthly_payment" value={edit.monthly_payment} onChange={changeEdit} required />
          </label>
          <label>
            Default project allowance
            <input
              name="default_project_allowance"
              value={edit.default_project_allowance}
              onChange={changeEdit}
              required
            />
          </label>
          <div className="form-actions">
            <button type="submit">Save changes</button>
          </div>
        </form>
      ) : null}
    </>
  );
}

function ApprovalsSection({ role }) {
  const canDecide = role === "hr" || role === "department_head";
  const { data, error, setError, reload } = useLoader(() => apiRequest("/payroll-entries"));
  const [comment, setComment] = useState("");
  const pending = (data?.payroll_entries || []).filter((entry) => entry.status === "submitted");

  const [confirm, setConfirm] = useState(null);

  async function decide(entry, action) {
    setConfirm({ entry, action });
  }

  async function runDecision() {
    if (!confirm) {
      return;
    }
    const { entry, action } = confirm;
    setError("");
    try {
      const body = { lock_version: entry.lock_version };
      if (action === "reject") {
        body.comment = comment;
      }
      await apiRequest(`/payroll-entries/${entry.id}/${action}`, { method: "POST", body });
      reload();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setConfirm(null);
    }
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Approvals</h2>
          <p>Review submitted payroll entries and approve or reject them.</p>
        </div>
      </div>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      <label>
        Comment (required when rejecting)
        <input value={comment} onChange={(event) => setComment(event.target.value)} />
      </label>
      <table>
        <thead>
          <tr>
            <th>Member</th>
            <th>Month</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {pending.map((entry) => (
            <tr key={entry.id}>
              <td>{entry.member_id}</td>
              <td>
                {entry.year}-{entry.month}
              </td>
              <td className="toolbar">
                {canDecide ? (
                  <>
                    <button type="button" onClick={() => decide(entry, "approve")}>
                      Approve
                    </button>
                    <button type="button" className="secondary" onClick={() => decide(entry, "reject")}>
                      Reject
                    </button>
                  </>
                ) : (
                  "Waiting"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {pending.length === 0 ? <EmptyState>No submitted entries waiting for a decision.</EmptyState> : null}
      <ConfirmDialog
        open={Boolean(confirm)}
        title={confirm?.action === "reject" ? "Reject this entry?" : "Approve this entry?"}
        body="Please confirm this decision. Approvals take effect only after you continue."
        confirmLabel={confirm?.action === "reject" ? "Reject" : "Approve"}
        onCancel={() => setConfirm(null)}
        onConfirm={runDecision}
      />
    </section>
  );
}

function Calculation({ calculation }) {
  const rows = [
    ["Daily rate", calculation.daily_rate],
    ["Final leaves", calculation.final_leaves],
    ["Final payment", calculation.final_payment],
    ["Gross payable", calculation.gross_payable],
    ["TDS", calculation.tds],
    ["Amount paid", calculation.amount_paid],
    ["Advance recovered", calculation.actual_advance_recovery],
    ["Net deposit", calculation.net_deposit],
  ];
  return (
    <div>
      <h3>Pay preview</h3>
      <table>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label}>
              <th>{label}</th>
              <td>{value ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {calculation.recovery_shortfall_flag ? (
        <p className="alert">The recovered amount was limited to the available balance.</p>
      ) : null}
    </div>
  );
}

function AdvancesSection({ role }) {
  const { data, error, setError, reload } = useLoader(async () => {
    const advances = await apiRequest("/advances");
    const members = role === "hr" ? await apiRequest("/members") : { members: [] };
    return { advances: advances.advances, members: members.members };
  });
  const [form, setForm] = useState({
    member_id: "",
    original_amount: "",
    date_paid: "",
    recovery_months: "6",
  });

  function update(event) {
    setForm({ ...form, [event.target.name]: event.target.value });
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await apiRequest("/advances", {
        method: "POST",
        body: {
          member_id: Number(form.member_id),
          original_amount: form.original_amount,
          date_paid: form.date_paid,
          recovery_months: Number(form.recovery_months),
        },
      });
      reload();
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Advances</h2>
          <p>Repayment installments automatically commence the month following disbursement.</p>
        </div>
      </div>
      {error ? <p className="alert" role="alert">{error}</p> : null}
      {role === "hr" ? (
        <form className="form-grid" onSubmit={submit}>
          <label>
            Member
            <select name="member_id" value={form.member_id} onChange={update} required>
              <option value="">Choose</option>
              {(data?.members || []).map((member) => (
                <option key={member.id} value={member.id}>
                  {member.full_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Amount
            <input name="original_amount" value={form.original_amount} onChange={update} required />
          </label>
          <label>
            Date paid
            <input name="date_paid" type="date" value={form.date_paid} onChange={update} required />
          </label>
          <label>
            Recovery months
            <input name="recovery_months" value={form.recovery_months} onChange={update} required />
          </label>
          <button type="submit">Save advance</button>
        </form>
      ) : null}
      {(data?.advances || []).map((advance) => (
        <article key={advance.id}>
          <h3>
            Advance {advance.id}: outstanding {advance.outstanding}
            {advance.needs_hr_review ? " — awaiting HR review" : ""}
          </h3>
          <table>
            <thead>
              <tr>
                <th>Due</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {advance.installments.map((row) => (
                <tr key={row.sequence}>
                  <td>
                    {row.due_year}-{row.due_month}
                  </td>
                  <td>{row.amount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      ))}
    </section>
  );
}

function InvoicesSection() {
  const { data, error, setError } = useLoader(() => apiRequest("/invoices"));

  async function download(invoice) {
    setError("");
    try {
      await downloadInvoice(invoice.id, `${invoice.invoice_number}.pdf`);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Invoices</h2>
          <p>Download issued invoices. Creating a new invoice keeps previous versions on file.</p>
        </div>
      </div>
      {error ? <p className="alert" role="alert">{error}</p> : null}
      {(data?.invoices || []).length === 0 ? (
        <EmptyState>Invoices appear here after a monthly run is approved.</EmptyState>
      ) : null}
      {(data?.invoices || []).length > 0 ? (
      <table>
        <thead>
          <tr>
            <th>Number</th>
            <th>Month</th>
            <th>Version</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(data?.invoices || []).map((invoice) => (
            <tr key={invoice.id}>
              <td>{invoice.invoice_number}</td>
              <td>
                {invoice.year}-{invoice.month}
              </td>
              <td>{invoice.version}</td>
              <td>
                <button type="button" onClick={() => download(invoice)}>
                  Download
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      ) : null}
    </section>
  );
}

function LeaveSection({ role }) {
  const { data, error, setError, reload } = useLoader(async () => {
    const departments = await apiRequest("/departments");
    const leaves = await Promise.all(
      departments.departments.map((department) =>
        apiRequest(`/departments/${department.id}/leaves`).then((body) =>
          body.leaves.map((leave) => ({ ...leave, department_name: department.name })),
        ),
      ),
    );
    const members = role === "hr" ? await apiRequest("/members") : { members: [] };
    return {
      departments: departments.departments,
      leaves: leaves.flat(),
      members: members.members,
    };
  });
  const [form, setForm] = useState({
    department_id: "",
    member_id: "",
    start_date: "",
    end_date: "",
    reason: "",
    head_start: "",
  });

  function update(event) {
    setForm({ ...form, [event.target.name]: event.target.value });
  }

  async function assignHead(event) {
    event.preventDefault();
    setError("");
    try {
      await apiRequest(`/departments/${form.department_id}/heads`, {
        method: "POST",
        body: { member_id: Number(form.member_id), start_date: form.head_start },
      });
      reload();
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await apiRequest(`/departments/${form.department_id}/leaves`, {
        method: "POST",
        body: {
          member_id: Number(form.member_id),
          start_date: form.start_date,
          end_date: form.end_date,
          reason: form.reason,
        },
      });
      reload();
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function cancel(leave) {
    setError("");
    try {
      await apiRequest(`/departments/${leave.department_id}/leaves/${leave.id}/cancel`, {
        method: "POST",
        body: {},
      });
      reload();
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Department head leave</h2>
          <p>Record an active department head leave period to authorize HR coverage of regular member entries.</p>
        </div>
      </div>
      {error ? <p className="alert" role="alert">{error}</p> : null}
      {role === "hr" ? (
        <form onSubmit={submit}>
          <label>
            Department
            <select name="department_id" value={form.department_id} onChange={update} required>
              <option value="">Choose</option>
              {(data?.departments || []).map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Department head
            <select name="member_id" value={form.member_id} onChange={update} required>
              <option value="">Choose</option>
              {(data?.members || []).map((member) => (
                <option key={member.id} value={member.id}>
                  {member.full_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Start
            <input name="start_date" type="date" value={form.start_date} onChange={update} required />
          </label>
          <label>
            End
            <input name="end_date" type="date" value={form.end_date} onChange={update} required />
          </label>
          <label>
            Reason
            <input name="reason" value={form.reason} onChange={update} />
          </label>
          <button type="submit">Record leave</button>
        </form>
      ) : null}
      {role === "hr" ? (
        <form onSubmit={assignHead}>
          <label>
            Head start date
            <input name="head_start" type="date" value={form.head_start} onChange={update} required />
          </label>
          <button type="submit">Assign department head</button>
        </form>
      ) : null}
      <table>
        <thead>
          <tr>
            <th>Department</th>
            <th>Member</th>
            <th>Dates</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {(data?.leaves || []).map((leave) => (
            <tr key={leave.id}>
              <td>{leave.department_name}</td>
              <td>{leave.member_id}</td>
              <td>
                {leave.start_date} to {leave.end_date}
              </td>
              <td>
                {leave.cancelled_at ? "Cancelled" : "Active"}
                {role === "hr" && !leave.cancelled_at ? (
                  <button type="button" onClick={() => cancel(leave)}>
                    Cancel
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
