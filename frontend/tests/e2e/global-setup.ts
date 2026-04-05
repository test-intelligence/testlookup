import { chromium } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

// ESM does not provide __dirname — derive it from import.meta.url
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BACKEND_URL = process.env.VITE_API_BASE_URL || 'http://localhost:8000';
const APP_URL     = process.env.PLAYWRIGHT_BASE_URL  || 'http://localhost:3000';

/**
 * Global setup: authenticate once before the test suite runs and save the
 * browser storage state (JWT in localStorage) so individual tests don't need
 * to log in themselves.  This prevents parallel-login overload on the backend.
 *
 * Uses the /dev-login endpoint (no credentials required) when running in the
 * local development environment. Requires APP_ENV=development and
 * DEV_AUTO_LOGIN_ENABLED=true in the backend .env.
 *
 * For CI/staging environments where dev-login is disabled, set the
 * E2E_ADMIN_PASSWORD env var and the helper falls back to form-based login.
 */
async function globalSetup() {
  const authFile = path.join(__dirname, '.auth', 'user.json');
  fs.mkdirSync(path.dirname(authFile), { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  await page.goto(`${APP_URL}/login`);

  // ── Strategy 1: dev-login endpoint (no credentials) ─────────────────────
  try {
    const resp = await page.request.post(`${BACKEND_URL}/api/v1/auth/dev-login?role=admin`);
    if (resp.ok()) {
      const { access_token, refresh_token } = await resp.json();
      // Inject tokens into Zustand's persisted localStorage key
      await page.evaluate(
        ({ at, rt }) => {
          localStorage.setItem(
            'auth-storage',
            JSON.stringify({ state: { token: at, refreshToken: rt }, version: 0 }),
          );
        },
        { at: access_token, rt: refresh_token },
      );
      await context.storageState({ path: authFile });
      await browser.close();
      return;
    }
  } catch {
    // dev-login not available — fall through to form login
  }

  // ── Strategy 2: form login via E2E_ADMIN_PASSWORD env var ───────────────
  const adminPassword = process.env.E2E_ADMIN_PASSWORD;
  if (!adminPassword) {
    throw new Error(
      'E2E global setup: dev-login endpoint is unavailable and E2E_ADMIN_PASSWORD is not set. ' +
      'Set DEV_AUTO_LOGIN_ENABLED=true in .env (dev) or set E2E_ADMIN_PASSWORD for CI.',
    );
  }

  await page.waitForSelector('input[name="username"]', { state: 'visible', timeout: 15000 });
  await page.locator('input[name="username"]').fill('admin');
  await page.locator('input[name="password"]').fill(adminPassword);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/.*\/overview/, { timeout: 30000 });

  await context.storageState({ path: authFile });
  await browser.close();
}

export default globalSetup;
