import { useState } from "react";
import { apiBaseUrl, apiRequest, errorText } from "../services/apiClient.js";
import { demoModeEnabled, setDemoModeEnabled } from "../services/runSelection.js";
import { EmptyState } from "../components/ui.jsx";

export function ExtraSections({ section, role, onGoToPayroll, onGoToLeave, onGoToMonthlyRun }) {
  if (section === "Departments") {
    return <DepartmentsSection role={role} onGoToLeave={onGoToLeave} />;
  }
  if (section === "Projects") {
    return <ProjectsSection role={role} />;
  }
  if (section === "Reports") {
    return <ReportsSection />;
  }
  if (section === "Settings") {
    return <SettingsSection role={role} />;
  }
  if (section === "Users") {
    return <UsersSection role={role} />;
  }
  if (section === "Chat") {
    return <ChatSection onGoToPayroll={onGoToPayroll} onGoToMonthlyRun={onGoToMonthlyRun} />;
  }
  return null;
}

function SimpleList({ title, intro, load, children }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  if (!loaded) {
    setLoaded(true);
    load()
      .then(setData)
      .catch((caught) => setError(errorText(caught)));
  }
  return (
    <section className="panel">
      <h2>{title}</h2>
      {intro ? <p className="lede">{intro}</p> : null}
      {error ? <p className="alert" role="alert">{error}</p> : null}
      {!data && !error ? <p className="status" role="status">Loading…</p> : null}
      {data ? children(data, setData, setError) : null}
    </section>
  );
}

function DepartmentsSection({ role, onGoToLeave }) {
  return (
    <SimpleList
      title="Departments"
      intro="Organize teams and see who currently leads each department."
      load={() => apiRequest("/departments")}
    >
      {(data) => (
        <>
          {data.departments.length === 0 ? <EmptyState>No departments have been recorded.</EmptyState> : null}
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Active</th>
                <th>Current head</th>
              </tr>
            </thead>
            <tbody>
              {data.departments.map((department) => (
                <tr key={department.id}>
                  <td>{department.name}</td>
                  <td>{department.is_active ? "Yes" : "No"}</td>
                  <td>{department.current_head ? department.current_head.member_id : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {role === "hr" ? (
            <p className="lede">
              Assign department heads and record head leave in{" "}
              <button type="button" className="text-link" onClick={onGoToLeave}>
                Leave
              </button>
              .
            </p>
          ) : null}
        </>
      )}
    </SimpleList>
  );
}

function ProjectsSection({ role }) {
  const [name, setName] = useState("");
  return (
    <SimpleList
      title="Projects"
      intro="Track the engagements referenced on payroll and invoices."
      load={() => apiRequest("/projects")}
    >
      {(data, setData, setError) => (
        <>
          {role === "hr" ? (
            <form
              onSubmit={async (event) => {
                event.preventDefault();
                setError("");
                try {
                  await apiRequest("/projects", { method: "POST", body: { name, status: "active" } });
                  setData(await apiRequest("/projects"));
                  setName("");
                } catch (caught) {
                  setError(errorText(caught));
                }
              }}
            >
              <label>
                Project name
                <input value={name} onChange={(event) => setName(event.target.value)} required />
              </label>
              <button type="submit">Save project</button>
            </form>
          ) : null}
          {data.projects.length === 0 ? <EmptyState>No projects yet.</EmptyState> : null}
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.projects.map((project) => (
                <tr key={project.id}>
                  <td>{project.name}</td>
                  <td>{project.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </SimpleList>
  );
}


function ReportsSection() {
  const [catalog, setCatalog] = useState(null);
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("payroll-register");
  const [year, setYear] = useState("2026");
  const [month, setMonth] = useState("9");
  const [loaded, setLoaded] = useState(false);
  if (!loaded) {
    setLoaded(true);
    apiRequest("/reports")
      .then((body) => {
        setCatalog(body.reports);
        if (body.reports[0]) {
          setName(body.reports[0].name);
        }
      })
      .catch((caught) => setError(errorText(caught)));
  }

  async function load(event) {
    event.preventDefault();
    setError("");
    try {
      const query = new URLSearchParams({ year, month });
      const body = await apiRequest(`/reports/${name}?${query.toString()}`);
      setReport(body.report);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  async function download(kind) {
    const query = new URLSearchParams({ year, month, export: kind });
    const response = await fetch(`${apiBaseUrl()}/reports/${name}?${query.toString()}`, {
      credentials: "include",
    });
    if (!response.ok) {
      setError(`Request failed with status ${response.status}`);
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name}.${kind}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Reports</h2>
          <p>Generate payroll and tax reports from finalized period data.</p>
        </div>
      </div>
      {error ? <p className="alert" role="alert">{error}</p> : null}
      <form onSubmit={load}>
        <label>
          Report
          <select value={name} onChange={(event) => setName(event.target.value)}>
            {(catalog || []).map((item) => (
              <option key={item.name} value={item.name}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Year
          <input value={year} onChange={(event) => setYear(event.target.value)} />
        </label>
        <label>
          Month
          <input value={month} onChange={(event) => setMonth(event.target.value)} />
        </label>
        <button type="submit">Load report</button>
        <button type="button" className="secondary" onClick={() => download("csv")}>
          Download CSV
        </button>
        <button type="button" className="secondary" onClick={() => download("xlsx")}>
          Download Excel
        </button>
      </form>
      {report ? (
        <>
          <p>{report.title}</p>
          {report.rows.length === 0 ? <EmptyState>No rows for this selection.</EmptyState> : null}
          <table>
            <thead>
              <tr>
                {report.headers.map((header) => (
                  <th key={header}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.rows.map((row, index) => (
                <tr key={index}>
                  {report.headers.map((header) => (
                    <td key={header}>{String(row[header] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </section>
  );
}

function SettingsSection({ role }) {
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [demoOn, setDemoOn] = useState(() => demoModeEnabled());
  if (!loaded) {
    setLoaded(true);
    apiRequest("/settings")
      .then((body) => setSettings(body.settings))
      .catch((caught) => setError(errorText(caught)));
  }

  const aadhaarOn = String(settings?.aadhaar_storage_enabled || "").toLowerCase() === "true";

  async function save(event) {
    event.preventDefault();
    setError("");
    setSaved("");
    try {
      const body = await apiRequest("/settings", {
        method: "PATCH",
        body: {
          company_name: settings.company_name,
          company_address: settings.company_address,
          company_email: settings.company_email,
          company_phone: settings.company_phone,
          contract_tds_rate: settings.contract_tds_rate,
          overtime_multiplier: settings.overtime_multiplier,
        },
      });
      setSettings(body.settings);
      setSaved("Settings saved.");
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Settings</h2>
          <p>Enter your company details below to update your workspace profile.</p>
        </div>
      </div>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {saved ? (
        <p className="status" role="status">
          {saved}
        </p>
      ) : null}
      {settings ? (
        <form onSubmit={save}>
          <div className="settings-grid">
            <label>
              Company name
              <input
                value={settings.company_name || ""}
                placeholder="e.g., Acme Corp Pvt Ltd"
                onChange={(event) => setSettings({ ...settings, company_name: event.target.value })}
              />
            </label>
            <label>
              Company email
              <input
                type="email"
                value={settings.company_email || ""}
                placeholder="billing@company.com"
                onChange={(event) => setSettings({ ...settings, company_email: event.target.value })}
              />
            </label>
            <label>
              Company phone
              <input
                value={settings.company_phone || ""}
                placeholder="+91 XXXXX XXXXX"
                onChange={(event) => setSettings({ ...settings, company_phone: event.target.value })}
              />
            </label>
            <label>
              Company address
              <input
                value={settings.company_address || ""}
                placeholder="Enter registered office address"
                onChange={(event) => setSettings({ ...settings, company_address: event.target.value })}
              />
            </label>
            <label>
              Contract TDS rate
              <input
                value={settings.contract_tds_rate}
                onChange={(event) => setSettings({ ...settings, contract_tds_rate: event.target.value })}
              />
            </label>
            <label>
              Overtime multiplier
              <input
                value={settings.overtime_multiplier}
                onChange={(event) => setSettings({ ...settings, overtime_multiplier: event.target.value })}
              />
            </label>
          </div>
          <div className="switch-field">
            <span className="switch">
              <input
                id="aadhaar-storage"
                type="checkbox"
                checked={aadhaarOn}
                disabled
                readOnly
              />
              <span className="switch-track" />
            </span>
            <div className="switch-copy">
              <label htmlFor="aadhaar-storage">
                <strong>Enable Aadhaar Data Storage</strong>
              </label>
              <p>
                Allow secure local storage of employee Aadhaar identification numbers for compliance and payroll
                verification. This option is currently unavailable.
              </p>
            </div>
          </div>
          <div className="form-actions">
            <button type="submit">Save settings</button>
          </div>
        </form>
      ) : null}
      <div className="switch-field">
        <span className="switch">
          <input
            id="demo-mode"
            type="checkbox"
            checked={demoOn}
            onChange={(event) => {
              const on = event.target.checked;
              setDemoModeEnabled(on);
              setDemoOn(on);
            }}
          />
          <span className="switch-track" />
        </span>
        <div className="switch-copy">
          <label htmlFor="demo-mode">
            <strong>Demo mode</strong>
          </label>
          <p>
            When on, Monthly run shows a button that sets comp-off above extra work days so you can see validation.
            Off by default.
          </p>
        </div>
      </div>
      {role === "hr" ? <BackupPanel /> : null}
    </section>
  );
}

function BackupPanel() {
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function backup() {
    setError("");
    try {
      const body = await apiRequest("/backup", { method: "POST", body: {} });
      setMessage(`Backup written to ${body.backup_folder}`);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  return (
    <div className="backup-block">
      <h3>Backup</h3>
      <p>
        Create local backups for offline archiving. Restores use a controlled recovery process so live payroll
        data is not overwritten.
      </p>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {message ? <p className="status">{message}</p> : null}
      <button type="button" onClick={backup}>
        Create backup
      </button>
    </div>
  );
}

function UsersSection({ role }) {
  if (role !== "hr") {
    return (
      <section className="panel">
        <h2>Users</h2>
        <p>User accounts are managed by HR administrators.</p>
      </section>
    );
  }
  const [data, setData] = useState(null);
  const [members, setMembers] = useState([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ email: "", password: "", role: "member", member_id: "" });
  const [loaded, setLoaded] = useState(false);
  if (!loaded) {
    setLoaded(true);
    Promise.all([apiRequest("/users"), apiRequest("/members")])
      .then(([users, memberBody]) => {
        setData(users.users);
        setMembers(memberBody.members);
      })
      .catch((caught) => setError(errorText(caught)));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      const body = { email: form.email, password: form.password, role: form.role };
      if (form.member_id) {
        body.member_id = Number(form.member_id);
      }
      await apiRequest("/users", { method: "POST", body });
      setData((await apiRequest("/users")).users);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Users</h2>
          <p>Invite teammates and assign roles. Member and department-head accounts should be linked to a profile.</p>
        </div>
      </div>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      <form className="form-grid" onSubmit={submit}>
        <label>
          Email
          <input
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={form.password}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
            required
            minLength={8}
          />
        </label>
        <label>
          Role
          <select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
            <option value="hr">HR</option>
            <option value="department_head">Department head</option>
            <option value="member">Member</option>
            <option value="finance">Finance</option>
          </select>
        </label>
        <label>
          Linked member
          <select
            value={form.member_id}
            onChange={(event) => setForm({ ...form, member_id: event.target.value })}
          >
            <option value="">None</option>
            {members.map((member) => (
              <option key={member.id} value={member.id}>
                {member.full_name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Create login</button>
      </form>
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Role</th>
            <th>Member</th>
            <th>Active</th>
          </tr>
        </thead>
        <tbody>
          {(data || []).map((row) => (
            <tr key={row.id}>
              <td>{row.email}</td>
              <td>{row.role}</td>
              <td>{row.member_id ?? "—"}</td>
              <td>{row.is_active ? "Yes" : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function ChatSection({ onGoToPayroll, onGoToMonthlyRun }) {
  const [message, setMessage] = useState("");
  const [log, setLog] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const question = message;
    try {
      const body = await apiRequest("/chat", { method: "POST", body: { message: question } });
      setLog((current) => [...current, { question, ...body.chat }]);
      setMessage("");
    } catch (caught) {
      const detail = errorText(caught);
      setLog((current) => [
        ...current,
        {
          question,
          reply:
            /csrf/i.test(detail)
              ? "Your session could not be verified. Sign in again, then retry."
              : "The assistant could not reply. You can continue in Payroll and enter values yourself.",
          model_unavailable: true,
          fallback: "manual_entry",
        },
      ]);
      setError(detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="page-head">
        <div>
          <h2>Assistant</h2>
          <p>Your AI workspace partner to draft entries, summarize reports, and guide workflow.</p>
        </div>
      </div>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      <form onSubmit={send}>
        <label>
          Message
          <textarea value={message} onChange={(event) => setMessage(event.target.value)} required rows={3} />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Sending…" : "Send"}
        </button>
      </form>
      <div className="chat-thread">
      {log.map((item, index) => (
        <article key={index}>
          <p className="bubble bubble-user">
            <strong>You:</strong> {item.question}
          </p>
          <div className="bubble bubble-assistant">
            <p className="assistant-reply">
              <strong>Assistant:</strong> {item.reply}
            </p>
            {item.extraction?.fields && Object.keys(item.extraction.fields).length > 0 ? (
              <table>
                <caption>Suggested fields</caption>
                <tbody>
                  {Object.entries(item.extraction.fields).map(([key, value]) => (
                    <tr key={key}>
                      <th>{key}</th>
                      <td>{String(value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            {(item.extraction?.validation_errors || []).length > 0 ? (
              <p className="row-error">{item.extraction.validation_errors.join(" ")}</p>
            ) : null}
          </div>
          {item.proposed_action?.type === "open_monthly_run" && onGoToMonthlyRun ? (
            <p>
              <button type="button" onClick={() => onGoToMonthlyRun(item.proposed_action.run_id)}>
                Open Monthly run
              </button>
            </p>
          ) : null}
          {item.model_unavailable || item.fallback === "manual_entry" ? (
            <p className="status">
              The assistant is offline. Continue in Payroll to enter values yourself.
              {onGoToPayroll ? (
                <>
                  {" "}
                  <button type="button" className="text-link" onClick={onGoToPayroll}>
                    Open Payroll
                  </button>
                </>
              ) : null}
            </p>
          ) : null}
          {item.calculation ? (
            <p className="status">Pay preview for this entry is shown below for your review.</p>
          ) : null}
          {item.calculation ? (
            <table>
              <tbody>
                {Object.entries(item.calculation).map(([key, value]) => (
                  <tr key={key}>
                    <th>{key}</th>
                    <td>{String(value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </article>
      ))}
      </div>
    </section>
  );
}
