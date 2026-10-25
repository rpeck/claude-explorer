import { test, expect, withNetRetry } from './fixtures'

/**
 * Mobile responsive layout (Build-8 #9).
 *
 * At viewport <768px, the sidebar should hide off-screen by default and a
 * hamburger button should be present in the main pane to slide it back in.
 */

test.describe('Mobile responsive layout', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test.beforeEach(async ({ page, mockBackend }) => {
    await mockBackend();
    await withNetRetry(page, () => page.goto('/'));
  });

  test('sidebar is hidden on mobile by default and hamburger toggles it', async ({ page }) => {
    // Sidebar should not be visible on viewport <768px.
    // Either the aside is fully off-screen (translate-x-full) or absent from layout.
    // We assert visibility via bounding box being out-of-bounds OR display:none.
    const isOffScreen = await page.evaluate(() => {
      const aside = document.querySelector('aside');
      if (!aside) return true;
      const rect = aside.getBoundingClientRect();
      const style = getComputedStyle(aside);
      return (
        rect.right <= 0 ||
        rect.left >= window.innerWidth ||
        style.display === 'none' ||
        style.visibility === 'hidden'
      );
    });
    expect(isOffScreen).toBe(true);

    // Hamburger button should be visible.
    const hamburger = page.getByRole('button', { name: /open sidebar|menu/i });
    await expect(hamburger).toBeVisible();

    // Click hamburger; sidebar slides in.
    await hamburger.click();

    // Poll until the slide animation ends (2026-10-24: a 300 ms sleep).
    await expect.poll(() => page.evaluate(() => {
      const aside = document.querySelector('aside');
      if (!aside) return false;
      const rect = aside.getBoundingClientRect();
      return rect.left >= 0 && rect.right > 0 && rect.left < window.innerWidth;
    })).toBe(true);

    // After clicking outside (or close button), drawer closes.
    const closeBtn = page.getByRole('button', { name: /close sidebar/i });
    await closeBtn.click();

    await expect.poll(() => page.evaluate(() => {
      const aside = document.querySelector('aside');
      if (!aside) return true;
      const rect = aside.getBoundingClientRect();
      const style = getComputedStyle(aside);
      return rect.right <= 0 || rect.left >= window.innerWidth || style.display === 'none';
    })).toBe(true);
  });
});
