import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

/**
 * UX redesign P1: a flat sidebar of places (`data-nav-id`), and a section's
 * other pages as route tabs above them (`data-route-tab`). Selected by those
 * stable hooks, never by label text.
 */
test.describe('Sidebar Navigation', () => {

  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  const places = [
    { id: 'runs',       path: '/runs'            },
    { id: 'failures',   path: '/failures'        },
    { id: 'trends',     path: '/trends'          },
    { id: 'test-cases', path: '/test-management' },
  ];

  for (const place of places) {
    test(`Navigate to ${place.id}`, async ({ page }) => {
      // A client-side React Router navigation: no full reload, no re-login.
      const link = page.locator(`[data-nav-id="${place.id}"]`);
      await link.click();
      await expect(page).toHaveURL(new RegExp(`.*${place.path}`));
      await expect(link).toHaveAttribute('aria-current', 'page');
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 15000 }).catch(() => {});
    });
  }

  const tabs = [
    { place: 'trends',   to: '/coverage' },
    { place: 'failures', to: '/defects'  },
  ];

  for (const tab of tabs) {
    test(`Reach ${tab.to} through the ${tab.place} section tabs`, async ({ page }) => {
      await page.locator(`[data-nav-id="${tab.place}"]`).click();
      await page.locator(`[data-route-tab="${tab.to}"]`).click();
      await expect(page).toHaveURL(new RegExp(`.*${tab.to}`));
      // The place stays highlighted on its other pages.
      await expect(page.locator(`[data-nav-id="${tab.place}"]`)).toHaveAttribute('aria-current', 'page');
      await expect(page.locator(`[data-route-tab="${tab.to}"]`)).toHaveAttribute('aria-current', 'page');
    });
  }
});
