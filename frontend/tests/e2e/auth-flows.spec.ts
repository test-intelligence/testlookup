import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockError } from './apiMock';

/**
 * Auth flows that the happy-path auth.spec.ts does not cover: failed login,
 * client-side registration validation, the forced reset-password screen,
 * logout, and the protected-route → /login redirect for unauthenticated users.
 *
 * These are deterministic (no live data dependency) so they assert hard.
 */
test.describe('Auth — error & session flows', () => {

  test('failed login shows an error and stays on /login', async ({ page }) => {
    // Start from a clean (logged-out) session so the form is the only path.
    await page.goto('/login');
    await page.evaluate(() => localStorage.clear());

    // Backend rejects the credentials → LoginPage toasts the error.
    await mockError(page, '**/api/v1/auth/login', 401, 'Invalid credentials');

    await page.goto('/login');
    await page.locator('#username').fill('admin');
    await page.locator('#password').fill('wrong-password');
    await page.getByRole('button', { name: /log in/i }).click();

    await expect(page.getByText('Invalid username or password')).toBeVisible({ timeout: 10000 });
    await expect(page).toHaveURL(/\/login/);
  });

  test('registration rejects mismatched passwords client-side', async ({ page }) => {
    await page.goto('/login');
    await page.evaluate(() => localStorage.clear());
    await page.goto('/login');

    await page.getByRole('button', { name: /^register$/i }).click();

    await page.locator('#reg-email').fill('newuser@example.com');
    await page.locator('#reg-username').fill('newuser');
    await page.locator('#reg-password').fill('password123');
    await page.locator('#reg-confirm').fill('password999'); // mismatch
    await page.getByRole('button', { name: /create account/i }).click();

    // Client-side guard fires before any network call.
    await expect(page.getByText('Passwords do not match')).toBeVisible({ timeout: 8000 });
  });

  test('reset-password screen renders and validates the new password', async ({ page }) => {
    // /reset-password is inside ProtectedRoute, so authenticate first.
    await performRealLogin(page);
    await page.goto('/reset-password');

    await expect(page.getByRole('heading', { name: /set your password/i })).toBeVisible({ timeout: 10000 });

    // Too-short password. Both inputs carry `required minLength={8}`, so the
    // BROWSER refuses to submit and handleSubmit never runs -- the app's own
    // `toast.error('Password must be at least 8 characters')` is unreachable
    // from the UI for this input. The old assertion waited 8s for a toast that
    // native validation had already made impossible. Assert the guard that is
    // actually doing the work.
    await page.locator('#new-password').fill('short');
    await page.locator('#confirm-password').fill('short');
    await page.getByRole('button', { name: /set password/i }).click();

    const shortField = page.locator('#new-password');
    await expect
      .poll(
        () => shortField.evaluate((el: HTMLInputElement) => el.validity.tooShort),
        { message: 'a 5-character password was not rejected by the length constraint' },
      )
      .toBe(true);
    // And it must not have navigated away or cleared the form.
    await expect(page.getByRole('heading', { name: /set your password/i })).toBeVisible();

    // Long enough but mismatched → mismatch guard.
    await page.locator('#new-password').fill('a-strong-password');
    await page.locator('#confirm-password').fill('a-different-password');
    await page.getByRole('button', { name: /set password/i }).click();
    await expect(page.getByText('Passwords do not match')).toBeVisible({ timeout: 8000 });
  });

  test('sign out returns the user to /login', async ({ page }) => {
    await performRealLogin(page);
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible({ timeout: 10000 });

    // The Sign out control is inside the account menu, which TopBar renders
    // only while `open` -- it is NOT always in the DOM, despite what this test
    // used to claim, so force-clicking a hidden node found nothing. It also
    // carries an explicit role="menuitem", which overrides the implicit button
    // role, so getByRole('button') can never match it. Open the menu, then
    // click the menuitem.
    await page.getByRole('button', { name: /account menu/i }).click();
    const signOut = page.getByRole('menuitem', { name: /sign out/i });
    await expect(signOut).toBeVisible({ timeout: 8000 });
    await signOut.click();

    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
    // Auth state is cleared.
    const token = await page.evaluate(() => {
      try {
        return JSON.parse(localStorage.getItem('auth-storage') || '{}')?.state?.token ?? null;
      } catch {
        return null;
      }
    });
    expect(token).toBeNull();
  });

  test('unauthenticated access to a protected route redirects to /login', async ({ page }) => {
    await page.goto('/login');
    await page.evaluate(() => localStorage.clear());

    await page.goto('/runs');

    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
    // The login form is shown, not the app shell.
    await expect(page.locator('#username')).toBeVisible({ timeout: 8000 });
  });
});
