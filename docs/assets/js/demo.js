/* ============================================================
   ConsensusFlow — Terminal Demo Animation + Gotcha Score
   Simulates a live verify() call with streaming output
   ============================================================ */

(function () {
  "use strict";

  const DEMO_PROMPT =
    'verify("Plan 2 days in Istanbul", chain=["gpt-4o", "gemini/gemini-2.0-flash", "claude-3-7-sonnet"])';

  const SCRIPT = [
    { delay: 0,     cls: "t-prompt",  text: "$ " },
    { delay: 0,     cls: "t-cmd",     text: "python -m consensusflow \\\n  " + DEMO_PROMPT },
    { delay: 600,   cls: "t-dim",     text: "\n" },
    { delay: 700,   cls: "t-info",    text: "🔍 ConsensusFlow v0.1.0  —  Sequential Verification\n" },
    { delay: 900,   cls: "t-dim",     text: "────────────────────────────────────────────\n" },
    { delay: 1100,  cls: "t-output",  text: "\n" },
    { delay: 1200,  cls: "",          text: '<span class="step-pill step-pill--proposer">Step 1 — Proposer  gpt-4o</span>\n', raw: true },
    { delay: 1300,  cls: "t-output",  text: "Generating answer…  " },
    { delay: 1500,  cls: "",          text: '<span class="spin">⟳</span>\n', raw: true },
    { delay: 2800,  cls: "t-success", text: "✓ Done  (1 847 tokens, 2.1 s)\n" },
    { delay: 3000,  cls: "t-output",  text: "\n" },
    { delay: 3100,  cls: "",          text: '<span class="step-pill step-pill--auditor">Step 2a — Extractor  gpt-4o-mini</span>\n', raw: true },
    { delay: 3200,  cls: "t-output",  text: "Extracted " },
    { delay: 3200,  cls: "t-warn",    text: "9 atomic claims" },
    { delay: 3200,  cls: "t-output",  text: " for audit\n" },
    { delay: 3500,  cls: "t-output",  text: "\n" },
    { delay: 3600,  cls: "",          text: '<span class="step-pill step-pill--auditor">Step 2b — Auditor  gemini/gemini-2.0-flash</span>\n', raw: true },
    { delay: 3700,  cls: "t-output",  text: "Adversarial review…  " },
    { delay: 3900,  cls: "",          text: '<span class="spin">⟳</span>\n', raw: true },
    { delay: 5800,  cls: "t-warn",    text: "⚠ Claim [3] CORRECTED: " },
    { delay: 5800,  cls: "t-output",  text: '"Blue Mosque entry fee"\n' },
    { delay: 6000,  cls: "t-info",    text: "  Was:  " },
    { delay: 6000,  cls: "t-error",   text: '"The Blue Mosque charges €10 admission"\n' },
    { delay: 6200,  cls: "t-info",    text: "  Now:  " },
    { delay: 6200,  cls: "t-success", text: '"The Blue Mosque is FREE — always has been"\n' },
    { delay: 6500,  cls: "t-output",  text: "\n" },
    { delay: 6600,  cls: "t-warn",    text: "⚠ Claim [7] NUANCED: " },
    { delay: 6600,  cls: "t-output",  text: '"Topkapi Palace hours"\n' },
    { delay: 6800,  cls: "t-output",  text: "  Note: Closed Tuesdays; hours changed Jan 2026\n" },
    { delay: 7100,  cls: "t-output",  text: "\n" },
    { delay: 7200,  cls: "",          text: '<span class="step-pill step-pill--resolver">Step 3 — Resolver  claude-3-7-sonnet</span>\n', raw: true },
    { delay: 7300,  cls: "t-output",  text: "Synthesising final answer…  " },
    { delay: 7500,  cls: "",          text: '<span class="spin">⟳</span>\n', raw: true },
    { delay: 9200,  cls: "t-success", text: "✓ Verified answer ready  (2 309 tokens, 8.7 s)\n" },
    { delay: 9400,  cls: "t-output",  text: "\n" },
    { delay: 9500,  cls: "t-dim",     text: "────────────────────────────────────────────\n" },
    { delay: 9600,  cls: "t-success", text: "✅ Status: SUCCESS\n" },
    { delay: 9700,  cls: "t-output",  text: "   Claims: " },
    { delay: 9700,  cls: "t-success", text: "7 verified  " },
    { delay: 9700,  cls: "t-warn",    text: "1 corrected  " },
    { delay: 9700,  cls: "t-info",    text: "1 nuanced\n" },
    { delay: 9900,  cls: "t-output",  text: "   Tokens: 4 156  |  Latency: 8 691 ms\n" },
    { delay: 10100, cls: "t-dim",     text: "   Run ID: cf-3a7e91b2\n" },
    { delay: 10200, cls: "t-output",  text: "\n" },
    { delay: 10300, cls: "t-dim",     text: "────────────────────────────────────────────\n" },
    { delay: 10400, cls: "t-warn",    text: "  🟡  GOTCHA SCORE: 72/100  —  Mostly Reliable  [Grade: B]\n" },
    { delay: 10500, cls: "t-output",  text: "  🎯 Catches : 2 out of 9 claims\n" },
    { delay: 10600, cls: "t-output",  text: "  🔬 Taxonomy: FABRICATION: 1  |  OUTDATED_INFO: 1\n" },
    { delay: 10700, cls: "t-output",  text: "  💵 Est. cost: $0.0416\n" },
    { delay: 10900, cls: "t-dim",     text: "────────────────────────────────────────────\n" },
    { delay: 11000, cls: "t-info",    text: "  💬 ConsensusFlow caught 2 hallucinations! Score: 72/100 🟡\n" },
    { delay: 11200, cls: "t-output",  text: "\n" },
    { delay: 11300, cls: "t-info",    text: "📄 Report saved → ./report.md\n" },
    { delay: 11500, cls: "t-dim",     text: "" },
    { delay: 11600, cls: "",          text: '<span class="terminal__cursor"></span>', raw: true },
  ];

  // ── Gotcha Score ring animation ────────────────────────────
  function animateGotchaScore(targetScore) {
    const numEl   = document.getElementById("gotcha-score-num");
    const ringEl  = document.getElementById("gotcha-ring-fill");
    const gradeEl = document.getElementById("gotcha-grade");
    const labelEl = document.getElementById("gotcha-label");
    if (!numEl || !ringEl) return;

    const circumference = 326.7; // 2π × 52
    const duration      = 1200;
    const start         = performance.now();

    function step(now) {
      const elapsed  = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased    = 1 - Math.pow(1 - progress, 3);
      const current  = Math.round(eased * targetScore);
      const offset   = circumference - (circumference * current) / 100;

      numEl.textContent = current;
      ringEl.style.strokeDashoffset = offset;

      if (progress < 1) {
        requestAnimationFrame(step);
      } else {
        numEl.textContent = targetScore;
        ringEl.style.strokeDashoffset = circumference - (circumference * targetScore) / 100;
        if (gradeEl) gradeEl.classList.add("gotcha-card__grade--pop");
        if (labelEl) labelEl.style.opacity = "1";
      }
    }
    requestAnimationFrame(step);
  }

  function resetGotchaScore() {
    const numEl  = document.getElementById("gotcha-score-num");
    const ringEl = document.getElementById("gotcha-ring-fill");
    const gradeEl = document.getElementById("gotcha-grade");
    if (ringEl) ringEl.style.strokeDashoffset = "326.7";
    if (numEl)  numEl.textContent = "0";
    if (gradeEl) gradeEl.classList.remove("gotcha-card__grade--pop");
  }

  function buildDemo(container) {
    const body = container.querySelector(".terminal__body");
    if (!body) return [];
    body.innerHTML = "";

    const timers = [];

    SCRIPT.forEach((item) => {
      const t = setTimeout(() => {
        if (item.raw) {
          const span = document.createElement("span");
          span.innerHTML = item.text;
          body.appendChild(span);
        } else {
          const span = document.createElement("span");
          if (item.cls) span.className = item.cls;
          span.textContent = item.text;
          body.appendChild(span);
        }
        body.scrollTop = body.scrollHeight;
      }, item.delay);
      timers.push(t);
    });

    // Trigger score ring when the Gotcha Score line appears
    timers.push(setTimeout(() => animateGotchaScore(72), 10500));
    return timers;
  }

  // ── Init on DOM ready ──────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    const demoContainer = document.getElementById("demo-terminal");
    if (!demoContainer) return;

    let activeTimers = buildDemo(demoContainer);

    const replayBtn = document.getElementById("demo-replay");
    if (replayBtn) {
      replayBtn.addEventListener("click", () => {
        activeTimers.forEach(clearTimeout);
        resetGotchaScore();
        activeTimers = buildDemo(demoContainer);
      });
    }

    if ("IntersectionObserver" in window) {
      activeTimers.forEach(clearTimeout);
      resetGotchaScore();
      const body = demoContainer.querySelector(".terminal__body");
      if (body) body.innerHTML = "";

      const obs = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              activeTimers = buildDemo(demoContainer);
              obs.unobserve(demoContainer);
            }
          });
        },
        { threshold: 0.3 }
      );
      obs.observe(demoContainer);
    }
  });

})();

// ── Share button ───────────────────────────────────────────
function copyGotchaShare() {
  const text = document.getElementById("gotcha-share-text");
  const btn  = document.getElementById("gotcha-share-btn");
  if (!text || !btn) return;
  navigator.clipboard.writeText(text.textContent.trim()).then(() => {
    const orig = btn.innerHTML;
    btn.innerHTML = "✓ Copied!";
    btn.classList.add("gotcha-share-btn--copied");
    setTimeout(() => {
      btn.innerHTML = orig;
      btn.classList.remove("gotcha-share-btn--copied");
    }, 2200);
  });
}
