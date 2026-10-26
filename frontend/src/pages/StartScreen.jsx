import { useEffect, useState } from "react";
import { loadHealthStatus } from "../services/apiClient.js";

export function StartScreen({
  healthStatus = "",
  healthError = "",
  loadHealth = loadHealthStatus,
  compact = false,
}) {
  const [status, setStatus] = useState(healthStatus);
  const [error, setError] = useState(healthError);

  useEffect(() => {
    if (healthStatus || healthError) {
      return undefined;
    }

    let active = true;
    loadHealth()
      .then((body) => {
        if (active) {
          setStatus(body.status);
        }
      })
      .catch(() => {
        if (active) {
          setError("The payroll service could not be reached.");
        }
      });

    return () => {
      active = false;
    };
  }, [healthError, healthStatus, loadHealth]);

  return (
    <section className={compact ? "brand-lockup compact" : "brand-lockup"}>
      <p className="eyebrow">Local-first payroll</p>
      <h1>Chill Pay</h1>
      <p className="lede">Notes in. Invoices out.</p>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {status ? <p className="status">API status: {status}</p> : null}
    </section>
  );
}
