// Runs in <head>, before first paint: applies the saved light/dark theme so the
// page never flashes the wrong one.
//
// It reads the key the platform's next-themes writes ("theme": "light" | "dark" |
// "system", apps/web/components/providers.tsx) and flips the same `dark` class on
// <html>. In production Keycloak is served under /auth on the web app's own
// origin, so the two share localStorage and agree; in local dev they sit on
// different ports and each remembers its own choice.
(function () {
  var root = document.documentElement;
  var query = window.matchMedia("(prefers-color-scheme: dark)");

  function stored() {
    try {
      return localStorage.getItem("theme");
    } catch (e) {
      return null;
    }
  }

  function apply(theme) {
    var dark = theme === "dark" || (theme !== "light" && query.matches);
    root.classList.toggle("dark", dark);
    root.classList.toggle("light", !dark);
    root.style.colorScheme = dark ? "dark" : "light";
  }

  // mate.js drives the toggle button through this.
  window.mateApplyTheme = apply;
  apply(stored());

  // Follow the OS only while the user has not picked a side.
  query.addEventListener("change", function () {
    var theme = stored();
    if (theme !== "light" && theme !== "dark") apply(theme);
  });
})();
