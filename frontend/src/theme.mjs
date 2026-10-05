let cached;
export function palette() {
  if (!cached) {
    const css = getComputedStyle(document.documentElement);
    cached = Object.fromEntries(
      [
        "radar",
        "grid",
        "radar-text",
        "selection",
        "green",
        "amber",
        "red",
        "orange",
        "surface",
        "muted",
        "ink",
        "line",
      ].map((k) => [k, css.getPropertyValue(`--${k}`).trim()]),
    );
  }
  return cached;
}
export function initTheme(changed) {
  const root = document.documentElement,
    select = document.getElementById("theme"),
    system = matchMedia("(prefers-color-scheme: dark)");
  let preference = root.dataset.themePreference || "system";
  const apply = () => {
    root.dataset.theme =
      preference === "system"
        ? system.matches
          ? "dark"
          : "light"
        : preference;
    root.dataset.themePreference = preference;
    select.value = preference;
    cached = null;
    changed();
  };
  select.addEventListener("change", () => {
    preference = select.value;
    try {
      localStorage.setItem("phantomguard.theme", preference);
    } catch {}
    apply();
  });
  system.addEventListener("change", () => {
    if (preference === "system") apply();
  });
  apply();
}
