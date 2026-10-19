// MATE login theme: the light/dark toggle plus one back/forward-cache fix.
// Plain script (deferred), no dependencies. theme-init.js has already applied
// the saved theme by the time this runs.
(function () {
  var root = document.documentElement;

  var toggle = document.getElementById("mate-theme-toggle");
  if (toggle) {
    var label = function () {
      var dark = root.classList.contains("dark");
      toggle.setAttribute("aria-label", dark ? toggle.dataset.labelLight : toggle.dataset.labelDark);
    };
    label();
    toggle.addEventListener("click", function () {
      var next = root.classList.contains("dark") ? "light" : "dark";
      // The key and values next-themes uses on the platform, so the choice
      // carries over wherever the two share an origin.
      try {
        localStorage.setItem("theme", next);
      } catch (e) {
        /* storage blocked: the switch still holds for this page view */
      }
      window.mateApplyTheme(next);
      label();
    });
  }

  // The stock sign-in form disables its button on submit. Going back to the
  // page from the browser's back/forward cache would otherwise bring it back
  // disabled, with no way to sign in short of a reload.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("#kc-login").forEach(function (button) {
      button.disabled = false;
    });
  });
})();
