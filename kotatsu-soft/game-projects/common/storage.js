(function initKotatsuStorage(global) {
  "use strict";

  const KEY_PREFIX = "kotatsu:";

  function getLocalStorage() {
    try {
      return global.localStorage || null;
    } catch (_error) {
      return null;
    }
  }

  function buildKey(gameId, key) {
    if (!gameId || !key) return null;
    return KEY_PREFIX + gameId + ":" + key;
  }

  function get(gameId, key) {
    const storage = getLocalStorage();
    const storageKey = buildKey(gameId, key);
    if (!storage || !storageKey) return null;
    try {
      const raw = storage.getItem(storageKey);
      if (raw === null) return null;
      return JSON.parse(raw);
    } catch (_error) {
      return null;
    }
  }

  function set(gameId, key, value) {
    const storage = getLocalStorage();
    const storageKey = buildKey(gameId, key);
    if (!storage || !storageKey) return false;
    try {
      storage.setItem(storageKey, JSON.stringify(value));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function remove(gameId, key) {
    const storage = getLocalStorage();
    const storageKey = buildKey(gameId, key);
    if (!storage || !storageKey) return false;
    try {
      storage.removeItem(storageKey);
      return true;
    } catch (_error) {
      return false;
    }
  }

  function clear(gameId) {
    const storage = getLocalStorage();
    if (!storage || !gameId) return false;
    const prefix = KEY_PREFIX + gameId + ":";
    try {
      const keysToRemove = [];
      for (let i = 0; i < storage.length; i++) {
        const itemKey = storage.key(i);
        if (itemKey && itemKey.indexOf(prefix) === 0) {
          keysToRemove.push(itemKey);
        }
      }
      for (let j = 0; j < keysToRemove.length; j++) {
        storage.removeItem(keysToRemove[j]);
      }
      return true;
    } catch (_error) {
      return false;
    }
  }

  global.KotatsuStorage = {
    buildKey,
    get,
    set,
    remove,
    clear,
  };
})(window);
