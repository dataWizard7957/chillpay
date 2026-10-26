import { Outlet } from "react-router-dom";

export function App() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <main id="main" className="app-shell">
        <Outlet />
      </main>
    </>
  );
}
