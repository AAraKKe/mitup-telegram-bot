/* Hides every home-page latest-news banner (.news-banner) once it's more than
   EXPIRY_DAYS past its data-published date. A date that can't be parsed leaves
   that banner shown, so a typo in data-published never silently blanks it. */
(function () {
  "use strict";

  var EXPIRY_DAYS = 14;

  function applyExpiry() {
    var banners = document.querySelectorAll(".news-banner[data-published]");

    banners.forEach(function (banner) {
      var published = new Date(banner.getAttribute("data-published") + "T00:00:00Z");
      if (isNaN(published.getTime())) {
        return;
      }

      var expiresAt = published.getTime() + EXPIRY_DAYS * 24 * 60 * 60 * 1000;
      if (Date.now() > expiresAt) {
        banner.hidden = true;
        banner.classList.add("is-expired");
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyExpiry);
  } else {
    applyExpiry();
  }

  // Re-run after instant-navigation page swaps, when document$ is available.
  if (typeof document$ !== "undefined" && document$ && typeof document$.subscribe === "function") {
    document$.subscribe(applyExpiry);
  }
})();
