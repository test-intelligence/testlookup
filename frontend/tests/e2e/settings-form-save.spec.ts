import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

/**
 * Settings form-save round-trips (Tier-2). The settings.spec.ts smoke test only
 * proves the pages render; this exercises the actual save handlers:
 *
 *  - Profile (/settings/profile): edit display name + avatar colour →
 *    PATCH /api/v1/auth/me → "Profile updated" toast, asserting the typed
 *    values reach the backend.
 *  - AI Configuration (/settings/ai): switch the analysis engine mode →
 *    PUT /api/v1/settings/ai → "AI configuration saved" toast, asserting the
 *    new mode is persisted.
 *
 * Both PUT/PATCH endpoints are intercepted so the round-trip is deterministic
 * and never mutates real settings. The GET on /settings/ai is mocked too so the
 * form renders against a known config. Requires the ADMIN session that
 * dev-login (role=admin) mints.
 */

/** A complete AIConfigRead so the AI config form renders deterministically. */
const AI_CONFIG = {
  llm_provider: 'ollama',
  llm_model: 'qwen2.5:7b',
  llm_temperature: 0.1,
  llm_max_tokens: 4096,
  ai_offline_mode: true,
  // Offline is on because the stored setting says so, not because the env
  // pins it — so the toggle stays editable in this fixture.
  ai_offline_mode_source: 'override',
  ai_offline_mode_env_pinned: false,
  embedding_provider: 'ollama',
  embedding_model: 'nomic-embed-text',
  ai_confidence_threshold: 80,
  ai_timeout_seconds: 300,
  deep_investigation_enabled: true,
  finetune_enabled: false,
  openai_key_set: false,
  google_key_set: false,
  analysis_mode: 'auto',
  ml_model_available: false,
  ml_model_accuracy: null,
  ml_training_sample_count: 42,
  knowledge_rag_enabled: false,
};

test.describe('Settings — form saves', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('profile edit PATCHes the new name + avatar colour and toasts success', async ({ page }) => {
    let patchBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/auth/me', async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBody = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: '00000000-0000-4000-8000-0000000000a1',
            username: 'admin',
            email: 'admin@example.com',
            role: 'ADMIN',
            ...(patchBody ?? {}),
          }),
        });
      } else {
        // Leave the auth bootstrap GET to the real backend.
        await route.continue();
      }
    });

    await page.goto('/settings/profile');
    await expect(page.getByRole('heading', { name: /my profile/i })).toBeVisible({ timeout: 10000 });

    // Edit the display name + pick the violet avatar swatch (button title=colour).
    const nameInput = page.getByPlaceholder('Your display name');
    await nameInput.fill('E2E Display Name');
    await page.getByTitle('violet', { exact: true }).click();

    await page.getByRole('button', { name: /save profile/i }).click();

    await expect(page.getByText('Profile updated')).toBeVisible({ timeout: 8000 });
    expect(patchBody).toMatchObject({ full_name: 'E2E Display Name', avatar_color: 'violet' });
  });

  test('AI config mode switch PUTs the new analysis_mode and toasts success', async ({ page }) => {
    let putBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/settings/ai', async (route) => {
      const method = route.request().method();
      if (method === 'PUT') {
        putBody = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ...AI_CONFIG, ...(putBody ?? {}) }),
        });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(AI_CONFIG) });
      }
    });

    await page.goto('/settings/ai');
    await expect(page.getByRole('heading', { name: /ai configuration/i })).toBeVisible({ timeout: 10000 });

    // The Save button (and editable radios) only render for ADMIN.
    const saveBtn = page.getByRole('button', { name: /save configuration/i });
    if (!(await saveBtn.isVisible().catch(() => false))) {
      throw new Error('the AI config form is read-only; the suite runs as admin, '
        + 'so a read-only form is a regression rather than an environment quirk');
      return;
    }

    // Switch the analysis engine from Auto → Rules-Based.
    await page.getByRole('radio', { name: /Rules-Based/ }).check();
    await saveBtn.click();

    await expect(page.getByText('AI configuration saved')).toBeVisible({ timeout: 8000 });
    expect(putBody).toMatchObject({ analysis_mode: 'rules' });
  });
});
