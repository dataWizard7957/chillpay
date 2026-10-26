import { useState } from "react";
import { adoptSession, apiRequest, errorText, rememberCsrfToken } from "../services/apiClient.js";

export function LoginForm({ onSuccess }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body = await apiRequest(creating ? "/auth/signup" : "/auth/login", {
        method: "POST",
        body: creating
          ? { email: email.trim().toLowerCase(), password, company_name: companyName.trim() }
          : { email: email.trim().toLowerCase(), password },
      });
      rememberCsrfToken(body.csrf_token);
      adoptSession(body.user);
      onSuccess(body.user);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>{creating ? "Create account" : "Sign in"}</h2>
      <p className="auth-note">
        {creating
          ? "This starts a new company. It cannot see another company's payroll."
          : "Use the email and password for your company."}
      </p>
      <label>
        Email
        <input
          name="email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
      </label>
      <label>
        Password
        <input
          name="password"
          type="password"
          autoComplete={creating ? "new-password" : "current-password"}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
      </label>
      {creating ? (
        <label>
          Company name
          <input
            name="company_name"
            value={companyName}
            onChange={(event) => setCompanyName(event.target.value)}
            required
          />
        </label>
      ) : null}
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      <button type="submit" disabled={busy}>
        {busy ? "Please wait…" : creating ? "Create account" : "Sign in"}
      </button>
      <button
        type="button"
        className="secondary"
        onClick={() => {
          setCreating((value) => !value);
          setError("");
        }}
      >
        {creating ? "I already have an account" : "Create a company account"}
      </button>
    </form>
  );
}
