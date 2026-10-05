// Synchronous, same-origin script runs before styles: no inline CSP exception.
(() => {
  let preference = "system";
  try {
    preference = localStorage.getItem("phantomguard.theme") || "system";
  } catch {}
  if (!["light", "dark", "system"].includes(preference)) preference = "system";
  const root = document.documentElement;
  root.dataset.themePreference = preference;
  root.dataset.theme =
    preference === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preference;
})();
