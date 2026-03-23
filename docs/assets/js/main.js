/* ============================================================
   ConsensusFlow — Main JS
   Nav scroll, tabs, copy, scroll reveal, mobile menu
   ============================================================ */

(function () {
  "use strict";

  // ── Nav scroll effect ──────────────────────────────────────
  const nav = document.querySelector(".nav");
  if (nav) {
    const onScroll = () => {
      nav.classList.toggle("scrolled", window.scrollY > 20);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // ── Mobile menu ────────────────────────────────────────────
  const hamburger = document.querySelector(".nav__hamburger");
  const mobileMenu = document.querySelector(".nav__mobile-menu");
  if (hamburger && mobileMenu) {
    hamburger.addEventListener("click", () => {
      const open = mobileMenu.classList.toggle("open");
      hamburger.setAttribute("aria-expanded", open);
    });
    // Close on link click
    mobileMenu.querySelectorAll("a").forEach((a) => {
      a.addEventListener("click", () => mobileMenu.classList.remove("open"));
    });
  }

  // ── Announcement bar close ─────────────────────────────────
  const annClose = document.querySelector(".announcement__close");
  const annBar = document.querySelector(".announcement");
  if (annClose && annBar) {
    annClose.addEventListener("click", () => {
      annBar.style.display = "none";
    });
  }

  // ── Code tabs ─────────────────────────────────────────────
  document.querySelectorAll(".install-block").forEach((block) => {
    const tabs = block.querySelectorAll(".tab-btn");
    const contents = block.querySelectorAll(".tab-content");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        tabs.forEach((t) => t.classList.remove("active"));
        contents.forEach((c) => c.classList.remove("active"));
        tab.classList.add("active");
        const target = block.querySelector(`[data-tab="${tab.dataset.tabTarget}"]`);
        if (target) target.classList.add("active");
      });
    });
  });

  // ── Copy to clipboard ──────────────────────────────────────
  document.querySelectorAll(".code-block__copy").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pre = btn.closest(".code-block").querySelector("pre");
      const text = pre ? pre.innerText : "";
      navigator.clipboard.writeText(text).then(() => {
        const original = btn.textContent;
        btn.textContent = "✓ Copied!";
        btn.classList.add("copied");
        setTimeout(() => {
          btn.textContent = original;
          btn.classList.remove("copied");
        }, 2000);
      });
    });
  });

  // ── Scroll reveal ──────────────────────────────────────────
  const revealEls = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    revealEls.forEach((el) => observer.observe(el));
  } else {
    revealEls.forEach((el) => el.classList.add("visible"));
  }

  // ── Benchmark bar animation ────────────────────────────────
  const benchBars = document.querySelectorAll(".score-bar__fill");
  if ("IntersectionObserver" in window && benchBars.length) {
    const barObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const el = entry.target;
            el.style.width = el.dataset.width || "0%";
            barObserver.unobserve(el);
          }
        });
      },
      { threshold: 0.5 }
    );
    benchBars.forEach((bar) => {
      const target = bar.dataset.width || "0%";
      bar.style.width = "0%";
      barObserver.observe(bar);
    });
  }

  // ── Smooth anchor offset (fixed nav) ──────────────────────
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener("click", (e) => {
      const target = document.querySelector(anchor.getAttribute("href"));
      if (!target) return;
      e.preventDefault();
      const navH = nav ? nav.offsetHeight : 72;
      const top = target.getBoundingClientRect().top + window.scrollY - navH - 16;
      window.scrollTo({ top, behavior: "smooth" });
    });
  });

  // ── Counter animation ──────────────────────────────────────
  function animateCounter(el) {
    const target = parseFloat(el.dataset.target);
    const isFloat = el.dataset.float === "true";
    const suffix = el.dataset.suffix || "";
    const prefix = el.dataset.prefix || "";
    const duration = 1800;
    const start = performance.now();

    const tick = (now) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      // easeOutExpo
      const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      const value = target * eased;
      el.textContent =
        prefix + (isFloat ? value.toFixed(1) : Math.floor(value)) + suffix;
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  const counterEls = document.querySelectorAll("[data-target]");
  if ("IntersectionObserver" in window && counterEls.length) {
    const cObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            animateCounter(entry.target);
            cObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.5 }
    );
    counterEls.forEach((el) => cObserver.observe(el));
  }
})();
