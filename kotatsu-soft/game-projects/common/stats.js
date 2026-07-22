(function initKotatsuStats(global) {
  "use strict";

  const API_BASE_URL = "https://kotatsu-soft-stats.kotatsusoft-dev.workers.dev";

  function sendPlayCount(gameId) {
    if (!gameId) {
      return;
    }

    try {
      fetch(`${API_BASE_URL}?id=${encodeURIComponent(gameId)}`, {
        method: "POST",
        keepalive: true,
        cache: "no-store",
      }).catch(function swallowNetworkError() {
        // Do not block game start on count failures.
      });
    } catch (_error) {
      // Do not block game start on count failures.
    }
  }

  global.KotatsuStats = {
    API_BASE_URL,
    sendPlayCount,
  };
})(window);
