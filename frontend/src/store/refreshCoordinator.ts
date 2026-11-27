const AUTH_REFRESH_LOCK_NAME = 'auth-storage:refresh';
const REFRESH_LOCK_DATABASE = 'testlookup-auth-coordination';
const REFRESH_LOCK_STORE = 'refresh-locks';
const STORAGE_TIMEOUT_MS = 1_000;
const WAIT_TIMEOUT_MS = 5_000;
const WAIT_POLL_MS = 50;

interface RefreshLockManager {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

interface RefreshLockRecord {
  key: string;
  owner: string;
}

export type RefreshLockClaim = 'claimed' | 'held' | 'unavailable';

/** Atomic storage boundary used by the IndexedDB fallback and its deterministic tests. */
export interface RefreshLockBackend {
  claim(credential: string, owner: string): Promise<RefreshLockClaim>;
  release(credential: string, owner: string): Promise<void>;
}

export interface RefreshCoordinationOptions {
  backend?: RefreshLockBackend;
  now?: () => number;
  owner?: string;
  timeoutMs?: number;
  wait?: (delayMs: number) => Promise<void>;
}

function openLockDatabase(): Promise<IDBDatabase | null> {
  let candidate: IDBFactory | undefined;
  try {
    candidate = globalThis.indexedDB;
  } catch {
    return Promise.resolve(null);
  }
  if (!candidate) return Promise.resolve(null);
  const factory = candidate;

  return new Promise((resolve) => {
    let settled = false;
    let request: IDBOpenDBRequest;
    const finish = (database: IDBDatabase | null) => {
      if (settled) {
        database?.close();
        return;
      }
      settled = true;
      globalThis.clearTimeout(timer);
      resolve(database);
    };
    const timer = globalThis.setTimeout(() => finish(null), STORAGE_TIMEOUT_MS);

    try {
      request = factory.open(REFRESH_LOCK_DATABASE, 1);
    } catch {
      finish(null);
      return;
    }
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(REFRESH_LOCK_STORE)) {
        request.result.createObjectStore(REFRESH_LOCK_STORE, { keyPath: 'key' });
      }
    };
    request.onsuccess = () => {
      request.result.onversionchange = () => request.result.close();
      finish(request.result);
    };
    request.onerror = () => finish(null);
    request.onblocked = () => finish(null);
  });
}

/**
 * Stable non-secret identifier for a refresh credential. Collisions only
 * over-serialize refreshes; the raw bearer token is never copied into IndexedDB.
 */
function credentialKey(credential: string): string {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < credential.length; index += 1) {
    const code = credential.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x85ebca6b);
  }
  return `${(first >>> 0).toString(16).padStart(8, '0')}${(second >>> 0).toString(16).padStart(8, '0')}`;
}

/**
 * IndexedDB serializes read/write transactions for one object store across the
 * whole origin. The get-and-put below is therefore one atomic ownership claim,
 * including on plain HTTP origins where the Web Locks API is unavailable.
 */
class IndexedDbRefreshLockBackend implements RefreshLockBackend {
  async claim(credential: string, owner: string): Promise<RefreshLockClaim> {
    const database = await openLockDatabase();
    if (!database) return 'unavailable';
    const expectedKey = credentialKey(credential);

    try {
      return await new Promise<RefreshLockClaim>((resolve) => {
        let decision: RefreshLockClaim = 'held';
        let settled = false;
        let transaction: IDBTransaction | null = null;
        const finish = (result: RefreshLockClaim) => {
          if (settled) return;
          settled = true;
          globalThis.clearTimeout(timer);
          resolve(result);
        };
        const timer = globalThis.setTimeout(() => {
          try { transaction?.abort(); } catch { /* an already-finished transaction needs no abort */ }
          finish('unavailable');
        }, STORAGE_TIMEOUT_MS);

        try {
          transaction = database.transaction(REFRESH_LOCK_STORE, 'readwrite');
          const store = transaction.objectStore(REFRESH_LOCK_STORE);
          const request = store.get(expectedKey);
          request.onsuccess = () => {
            const current = request.result as RefreshLockRecord | undefined;
            if (!current) {
              store.put({ key: expectedKey, owner } satisfies RefreshLockRecord);
              decision = 'claimed';
            }
          };
          transaction.oncomplete = () => finish(decision);
          transaction.onerror = () => finish('unavailable');
          transaction.onabort = () => finish('unavailable');
        } catch {
          finish('unavailable');
        }
      });
    } finally {
      database.close();
    }
  }

  async release(credential: string, owner: string): Promise<void> {
    const database = await openLockDatabase();
    if (!database) return;
    const expectedKey = credentialKey(credential);

    try {
      await new Promise<void>((resolve) => {
        let settled = false;
        let transaction: IDBTransaction | null = null;
        const finish = () => {
          if (settled) return;
          settled = true;
          globalThis.clearTimeout(timer);
          resolve();
        };
        const timer = globalThis.setTimeout(() => {
          try { transaction?.abort(); } catch { /* an already-finished transaction needs no abort */ }
          finish();
        }, STORAGE_TIMEOUT_MS);

        try {
          transaction = database.transaction(REFRESH_LOCK_STORE, 'readwrite');
          const store = transaction.objectStore(REFRESH_LOCK_STORE);
          const request = store.get(expectedKey);
          request.onsuccess = () => {
            const current = request.result as RefreshLockRecord | undefined;
            if (current?.owner === owner) {
              store.delete(expectedKey);
            }
          };
          transaction.oncomplete = finish;
          transaction.onerror = finish;
          transaction.onabort = finish;
        } catch {
          finish();
        }
      });
    } finally {
      database.close();
    }
  }
}

function newOwnerId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  const bytes = new Uint32Array(4);
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, (part) => part.toString(16)).join('-');
  }
  return `${Date.now()}-${Math.random()}`;
}

const delay = (milliseconds: number) => new Promise<void>((resolve) => {
  globalThis.setTimeout(resolve, milliseconds);
});

/**
 * Run one refresh for an origin. A waiting caller enters the callback only
 * after ownership transfers, so it can adopt persisted credentials before
 * deciding whether a request is still necessary.
 */
export async function withOriginRefreshLock<T>(
  credential: string | null,
  callback: () => Promise<T>,
  blockedValue: T,
  options: RefreshCoordinationOptions = {},
): Promise<T> {
  if (!credential) return blockedValue;

  let locks: RefreshLockManager | undefined;
  try {
    locks = typeof navigator === 'undefined'
      ? undefined
      : (navigator as Navigator & { locks?: RefreshLockManager }).locks;
  } catch {
    locks = undefined;
  }
  if (locks) {
    let callbackStarted = false;
    try {
      return await locks.request(AUTH_REFRESH_LOCK_NAME, () => {
        callbackStarted = true;
        return callback();
      });
    } catch (error) {
      // A callback failure may follow an accepted refresh request. Never fall
      // through to a second coordination mechanism and submit it again.
      if (callbackStarted) throw error;
    }
  }

  const backend = options.backend ?? new IndexedDbRefreshLockBackend();
  const now = options.now ?? Date.now;
  const owner = options.owner ?? newOwnerId();
  const timeoutMs = options.timeoutMs ?? WAIT_TIMEOUT_MS;
  const wait = options.wait ?? delay;
  const deadline = now() + timeoutMs;

  while (true) {
    const claim = await backend.claim(credential, owner);
    if (claim === 'unavailable') return blockedValue;
    if (claim === 'claimed') {
      let completed = false;
      try {
        const result = await callback();
        completed = true;
        return result;
      } finally {
        // An unexpected interruption can leave the server outcome unknown.
        // Retain that credential's claim so no later caller can resubmit it.
        if (completed) {
          try { await backend.release(credential, owner); } catch { /* a retained claim fails closed */ }
        }
      }
    }

    const remaining = deadline - now();
    if (remaining <= 0) return blockedValue;
    await wait(Math.min(WAIT_POLL_MS, remaining));
  }
}
