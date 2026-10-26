import { useState } from "react";
import { apiRequest, errorText } from "../services/apiClient.js";

export function SetupForm({ onComplete }) {
  const [form, setForm] = useState({
    email: "",
    password: "",
    company_name: "",
    company_address: "",
    company_email: "",
    company_phone: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function update(event) {
    setForm({ ...form, [event.target.name]: event.target.value });
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body = {
        email: form.email.trim().toLowerCase(),
        password: form.password,
      };
      for (const key of ["company_name", "company_address", "company_email", "company_phone"]) {
        if (form[key].trim()) {
          body[key] = form[key].trim();
        }
      }
      await apiRequest("/setup", { method: "POST", body });
      onComplete();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>First-time company setup</h2>
      <p className="auth-note">
        Create your administrator account to get started. Company details are optional and can be completed later.
      </p>
      <div className="form-grid">
        <label>
          Admin email
          <input name="email" type="email" autoComplete="username" value={form.email} onChange={update} required />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={update}
            required
            minLength={8}
          />
        </label>
      </div>
      <p className="eyebrow">Company profile (optional)</p>
      <div className="form-grid">
        <label>
          Company name
          <input name="company_name" value={form.company_name} onChange={update} />
        </label>
        <label>
          Company address
          <input name="company_address" value={form.company_address} onChange={update} />
        </label>
        <label>
          Company email
          <input name="company_email" type="email" value={form.company_email} onChange={update} />
        </label>
        <label>
          Company phone
          <input name="company_phone" value={form.company_phone} onChange={update} />
        </label>
      </div>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      <button type="submit" disabled={busy}>
        {busy ? "Creating…" : "Create first admin"}
      </button>
    </form>
  );
}
