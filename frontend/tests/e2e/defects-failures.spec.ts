import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { seedActiveProject } from './apiMock';

test.describe('Defects and Failure Analysis', () => {

  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('should render Failure Analysis page', async ({ page }) => {
    await page.goto('/failures');
    // When no project is selected the page renders an EmptyState <h3>; when a
    // project is selected it renders a PageHeader <h1>.  Either way a heading
    // at any level is present — don't constrain to level 1.
    await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
  });

  test('should render Defects tracking page', async ({ page }) => {
    await page.goto('/defects');
    await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
  });
});

// ── Defect Intake modal ────────────────────────────────────────────────────
//
// Covers the "Defect Intake" feature shipped 2026-05-15:
//   - `POST /api/v1/analytics/defects` (manual intake) — see
//     backend/app/routers/analytics.py
//   - DefectIntakeModal at frontend/src/components/defects/DefectIntakeModal.tsx
//   - Wired into DefectsPage via the page-header "New defect" CTA.
//
// The modal requires a single project selected (All-Projects mode toasts a
// hint instead of opening). We assert the opens-cleanly path, the client-
// side validation gate on the title field, and Escape-to-close.

test.describe('Defect Intake modal', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
    // Seed a concrete project. Every test below used to skip itself when the
    // CTA was absent -- which is precisely what All-Projects mode looks like,
    // and also what a regression that removed the CTA looks like. Establish
    // the precondition instead of treating its absence as a reason to opt out.
    await seedActiveProject(page);
    await page.goto('/defects');
    // Wait for either the page header or the EmptyState to land.
    await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 10000 });
  });

  test('opens the intake modal from the page header CTA', async ({ page }) => {
    const newDefectBtn = page.getByRole('button', { name: /^new defect$/i }).first();
    await expect(newDefectBtn, 'the New defect CTA did not render for a seeded project')
      .toBeVisible({ timeout: 10000 });
    await newDefectBtn.click();

    // In All-Projects mode the click toasts and does NOT open the modal.
    const dialog = page.getByRole('dialog', { name: /new defect/i });
    const opened = await dialog.waitFor({ state: 'visible', timeout: 4000 })
      .then(() => true)
      .catch(() => false);
    if (!opened) {
      // Verify the toast hint reached the user and bail.
      const toast = page.locator('text=/pick a single project/i').first();
      await expect(toast).toBeVisible({ timeout: 4000 });
      return;
    }

    // Modal shape: title input + severity row + Create button.
    await expect(page.getByLabel(/^title/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /^p1/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /create defect/i })).toBeVisible();
  });

  test('disables the submit button until title meets the min-length rule', async ({ page }) => {
    const newDefectBtn = page.getByRole('button', { name: /^new defect$/i }).first();
    await expect(newDefectBtn, 'the New defect CTA did not render for a seeded project')
      .toBeVisible({ timeout: 10000 });
    await newDefectBtn.click();
    const dialog = page.getByRole('dialog', { name: /new defect/i });
    await expect(dialog, 'the intake modal did not open for a seeded project')
      .toBeVisible({ timeout: 8000 });

    const submit = page.getByRole('button', { name: /create defect/i });
    await expect(submit).toBeDisabled();

    // Type a too-short title: button stays disabled.
    const titleInput = page.getByLabel(/^title/i);
    await titleInput.fill('xx');
    await expect(submit).toBeDisabled();

    // Type a valid title (>= 3 chars): button enables.
    await titleInput.fill('Checkout flow regression');
    await expect(submit).toBeEnabled();
  });

  test('rejects an invalid Jira URL inline', async ({ page }) => {
    const newDefectBtn = page.getByRole('button', { name: /^new defect$/i }).first();
    await expect(newDefectBtn, 'the New defect CTA did not render for a seeded project')
      .toBeVisible({ timeout: 10000 });
    await newDefectBtn.click();
    const dialog = page.getByRole('dialog', { name: /new defect/i });
    await expect(dialog, 'the intake modal did not open for a seeded project')
      .toBeVisible({ timeout: 8000 });

    await page.getByLabel(/^title/i).fill('Valid title here');
    await page.getByLabel(/jira ticket url/i).fill('not-a-url');

    // Inline error message renders + submit button disables.
    await expect(page.locator('text=/must be an http\\(s\\) url/i')).toBeVisible();
    await expect(page.getByRole('button', { name: /create defect/i })).toBeDisabled();
  });

  test('closes the modal on Escape', async ({ page }) => {
    const newDefectBtn = page.getByRole('button', { name: /^new defect$/i }).first();
    await expect(newDefectBtn, 'the New defect CTA did not render for a seeded project')
      .toBeVisible({ timeout: 10000 });
    await newDefectBtn.click();
    const dialog = page.getByRole('dialog', { name: /new defect/i });
    await expect(dialog, 'the intake modal did not open for a seeded project')
      .toBeVisible({ timeout: 8000 });
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden({ timeout: 3000 });
  });
});
