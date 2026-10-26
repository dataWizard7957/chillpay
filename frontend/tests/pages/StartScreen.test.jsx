import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { StartScreen } from "../../src/pages/StartScreen.jsx";

test("renders the start screen", () => {
  render(<StartScreen healthStatus="ok" loadHealth={() => Promise.resolve({ status: "ok" })} />);

  expect(screen.getByRole("heading", { name: "Chill Pay" })).toBeInTheDocument();
  expect(screen.getByText("API status: ok")).toBeInTheDocument();
});

test("renders an API error state", () => {
  render(
    <StartScreen
      healthError="The payroll service could not be reached."
      loadHealth={() => Promise.reject(new Error("down"))}
    />,
  );

  expect(screen.getByRole("alert")).toHaveTextContent("could not be reached");
});
