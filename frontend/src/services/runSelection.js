let selectedRunId = null;

export function setSelectedRunId(id) {
  selectedRunId = id;
}

export function takeSelectedRunId() {
  const value = selectedRunId;
  selectedRunId = null;
  return value;
}

export function demoModeEnabled() {
  try {
    return window.localStorage.getItem("chillpay_demo_mode") === "true";
  } catch {
    return false;
  }
}

export function setDemoModeEnabled(on) {
  try {
    window.localStorage.setItem("chillpay_demo_mode", on ? "true" : "false");
  } catch {
    return;
  }
}
