/* Light / dark theme (BR-23).
 *
 * Loaded in the page head WITHOUT defer, so the theme is set before the first paint
 * and there is no flash of the wrong theme. Rule: a saved choice wins; otherwise
 * follow the system setting. The saved choice stays on this device only and is the
 * only thing this page ever keeps in browser storage.
 */
(function () {
  "use strict";

  var KEY = "theme";
  var root = document.documentElement;

  function stored() {
    try {
      var value = window.localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (error) {
      return null; // storage can be blocked; the page still works
    }
  }

  function query() {
    try {
      return window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    } catch (error) {
      return null;
    }
  }

  function resolve() {
    var saved = stored();
    if (saved) {
      return saved;
    }
    var media = query();
    return media && media.matches ? "dark" : "light";
  }

  function apply(theme) {
    root.setAttribute("data-theme", theme);
  }

  function current() {
    return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function toggle() {
    var next = current() === "dark" ? "light" : "dark";
    apply(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch (error) {
      /* not saved, still switched */
    }
    return next;
  }

  apply(resolve());

  // Until the person picks a theme, keep following the system setting.
  var media = query();
  if (media && media.addEventListener) {
    media.addEventListener("change", function () {
      if (!stored()) {
        apply(resolve());
        window.dispatchEvent(new Event("themechange"));
      }
    });
  }

  window.PatientTheme = { resolve: resolve, current: current, toggle: toggle };
})();
