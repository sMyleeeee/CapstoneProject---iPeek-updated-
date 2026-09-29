/**
 * pillnav.js
 * ----------
 * Lightweight pill-style nav highlight: a single floating yellow pill
 * glides smoothly to whichever nav link is hovered, snapping back to
 * the active page's link when the mouse leaves. Pure CSS transitions,
 * no external animation library.
 *
 * TEXT COLOR: only the pill currently sitting under the yellow
 * indicator gets navy text (via the "nav-lit" class) — everything
 * else stays default. This must be tracked in JS rather than a static
 * ".is-active" CSS rule, because the indicator itself moves on hover;
 * a static rule would leave the active page's text navy-on-navy
 * (invisible) the moment the indicator glides away to follow the mouse.
 */

function initPillNav(container) {
  if (!container) return;

 let indicator = container.querySelector(".pill-indicator");
if (!indicator) {
  indicator = document.createElement("div");
  indicator.className = "pill-indicator";
  if (getComputedStyle(container).position === "static") {
    container.style.position = "relative";
  }
  container.insertBefore(indicator, container.firstChild);
}
  const links = container.querySelectorAll("a.pill");
  const activeLink = container.querySelector("a.pill.is-active") || links[0];

  function moveTo(el) {
    if (!el) { indicator.style.opacity = "0"; return; }
    const containerRect = container.getBoundingClientRect();
    const rect = el.getBoundingClientRect();
    indicator.style.opacity = "1";
    indicator.style.width  = `${rect.width}px`;
    indicator.style.height = `${rect.height}px`;
    indicator.style.transform = `translate(${rect.left - containerRect.left}px, ${rect.top - containerRect.top}px)`;

    links.forEach(l => l.classList.remove("nav-lit"));
    el.classList.add("nav-lit");
  }

  links.forEach(link => {
    link.addEventListener("mouseenter", () => moveTo(link));
  });

  container.addEventListener("mouseleave", () => moveTo(activeLink));

  moveTo(activeLink);
  window.addEventListener("resize", () => moveTo(activeLink));
}