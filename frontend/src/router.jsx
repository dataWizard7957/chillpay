import { createBrowserRouter } from "react-router-dom";
import { App } from "./App.jsx";
import { NotFound } from "./pages/NotFound.jsx";
import { Workspace } from "./pages/Workspace.jsx";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Workspace /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);
