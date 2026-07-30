(function initKotatsuStats(global) {
  "use strict";

  const API_BASE_URL = "https://kotatsu-soft-stats.kotatsusoft-dev.workers.dev";

  function normalizeCount(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return null;
    return Math.max(0, Math.trunc(num));
  }

  function formatCount(value) {
    const count = normalizeCount(value);
    if (count === null) return null;
    return new Intl.NumberFormat("ja-JP").format(count);
  }

  async function postCount(id) {
    if (!id) return;
    try {
      await fetch(`${API_BASE_URL}?id=${encodeURIComponent(id)}`, {
        method: "POST",
        keepalive: true,
        cache: "no-store",
      });
    } catch (_error) {
      // Keep callers resilient when the stats API is unavailable.
    }
  }

  function sendPlayCount(gameId) {
    if (!gameId) return;
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

  function sendPortalPv() {
    return postCount("pv");
  }

  async function fetchStats() {
    try {
      const response = await fetch(API_BASE_URL, {
        method: "GET",
        cache: "no-store",
        headers: {
          accept: "application/json",
        },
      });
      if (!response.ok) return null;
      return response.json();
    } catch (_error) {
      return null;
    }
  }

  global.KotatsuStats = {
    API_BASE_URL,
    normalizeCount,
    formatCount,
    postCount,
    sendPlayCount,
    sendPortalPv,
    fetchStats,
  };

  // Backward-compatible globals used by older game pages.
  global.sendPlayCount = sendPlayCount;
})(window);
