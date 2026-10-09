/* Onboarding scan animation — fills the progress bar, then completes onboarding. */
(function () {
  "use strict";
  const fill = document.getElementById("scan-fill");
  const label = document.getElementById("scan-label");
  const form = document.getElementById("complete-form");
  if (!fill || !form) return;

  let pct = 0;
  const labels = [
    [35, "Mailbox verbinden…"],
    [70, "Recente mails analyseren…"],
    [99, "Schrijfstijl leren…"],
    [100, "Klaar!"],
  ];

  const iv = setInterval(() => {
    pct = Math.min(100, pct + 7);
    fill.style.width = pct + "%";
    if (label) {
      const next = labels.find(([t]) => pct < t) || labels[labels.length - 1];
      label.textContent = next[1];
    }
    if (pct >= 100) {
      clearInterval(iv);
      setTimeout(() => form.submit(), 450);
    }
  }, 90);
})();
