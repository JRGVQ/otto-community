/* Declarative behaviours — the replacement for inline event handlers.
 *
 * The Content-Security-Policy is nonce-based (see app/__init__.py). A nonce makes the
 * browser ignore 'unsafe-inline' completely, so an onclick="…" attribute simply never
 * fires. Every such handler is expressed as a data-attribute instead and wired up here,
 * once, through delegated listeners on the document — which also keeps working for
 * markup that is inserted later.
 */
(function () {
  "use strict";

  function t(s) { return (typeof T === "function") ? T(s) : s; }

  // Walk up from the event target to the nearest element carrying `attr`.
  function closestWith(target, attr) {
    var el = target;
    while (el && el !== document) {
      if (el.nodeType === 1 && el.hasAttribute(attr)) return el;
      el = el.parentNode;
    }
    return null;
  }

  document.addEventListener("click", function (e) {
    // Keep a click inside a dialog from reaching the backdrop that closes it.
    if (closestWith(e.target, "data-stop-propagation")) e.stopPropagation();

    var el = closestWith(e.target, "data-action");
    if (!el) return;

    switch (el.getAttribute("data-action")) {
      case "theme-toggle":
        if (typeof ottoToggleTheme === "function") ottoToggleTheme();
        break;
      case "nav-toggle":
        if (typeof ottoToggleNav === "function") ottoToggleNav();
        break;
      case "print":
        window.print();
        break;
      case "navigate":
        if (el.getAttribute("data-href")) window.location = el.getAttribute("data-href");
        break;
      case "set-value": {
        // Pick which submit button a shared form acted on.
        var target = document.getElementById(el.getAttribute("data-target"));
        if (target) target.value = el.getAttribute("data-value") || "";
        break;
      }
    }
  });

  // Confirm before a destructive submit. data-confirm holds the (already translated)
  // question; an empty answer cancels the submit.
  document.addEventListener("submit", function (e) {
    var form = closestWith(e.target, "data-confirm");
    if (form && !window.confirm(t(form.getAttribute("data-confirm")))) e.preventDefault();
  });

  // Filter selects that submit their form as soon as the choice changes.
  document.addEventListener("change", function (e) {
    var el = closestWith(e.target, "data-submit-on-change");
    if (el && el.form) el.form.submit();
  });
})();
