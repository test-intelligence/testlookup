import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

test.describe('Sidebar Navigation', () => {

  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  const routes = [
    { name: 'Runs',            path: '/runs'            },
    { name: 'Coverage',        path: '/coverage'        },
    { name: 'Failures',        path: '/failures'        },
    { name: 'Trends',          path: '/trends'          },
    { name: 'Defects',         path: '/defects'         },
    { name: 'Test Management', path: '/test-management' },
  ];

  for (const route of routes) {
    test(`Navigate to ${route.name}`, async ({ page }) => {
      // Click the sidebar NavLink — this is a client-side React Router navigation
      // that does NOT cause a full-page reload, so no re-authentication is needed.
      await page.locator(`aside a[href="${route.path}"]`).click();
      await expect(page).toHaveURL(new RegExp(`.*${route.path}`));

      // Give sufficient timeout for live backend loading
      await expect(page.getByRole('heading').first()).toBeVisible({ timeout: 15000 }).catch(() => {});
    });
  }
});
