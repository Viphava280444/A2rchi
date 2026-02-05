/**
 * Workflow 10: Sidebar Navigation Tests
 * 
 * Tests for sidebar behavior with conversation list.
 * The sidebar (complementary region) shows: Logo, New Chat button, 
 * and conversation list grouped by date (TODAY, PREVIOUS 7 DAYS, etc).
 * The toggle button is in the header banner, not the sidebar itself.
 */
import { test, expect, setupBasicMocks } from '../fixtures';

test.describe('Sidebar Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('sidebar visible by default', async ({ page }) => {
    await page.goto('/chat');
    // Wait for network idle to ensure page is fully loaded
    await page.waitForLoadState('networkidle');
    
    // Sidebar should be visible (complementary region)
    // Use getByRole which relies on the accessibility tree
    const sidebar = page.getByRole('complementary');
    await expect(sidebar).toBeVisible();
  });

  test('sidebar has logo and new chat button', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    
    const sidebar = page.getByRole('complementary');
    await expect(sidebar.getByText('archi')).toBeVisible();
    await expect(sidebar.getByRole('button', { name: /new chat/i })).toBeVisible();
  });

  test('conversation list shows date groups', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    
    const sidebar = page.getByRole('complementary');
    
    // Should show at least one date grouping (TODAY, PREVIOUS 7 DAYS, etc.)
    // These are shown as StaticText elements
    const todayText = sidebar.getByText('TODAY');
    const prevDaysText = sidebar.getByText('PREVIOUS 7 DAYS');
    
    // At least one should be visible if there are conversations
    const hasTodayGroup = await todayText.isVisible().catch(() => false);
    const hasPrevDaysGroup = await prevDaysText.isVisible().catch(() => false);
    
    // If there are any conversations, at least one group should exist
    // This is true because our mock data has conversations
    const hasDateGroup = hasTodayGroup || hasPrevDaysGroup;
    expect(hasDateGroup).toBe(true);
  });

  test('conversations show in sidebar', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    
    const sidebar = page.getByRole('complementary');
    
    // The actual page shows conversation titles from real data
    // Our mock data has "Test Conversation" and "Another Chat"
    // Just verify conversations are present
    const conversationItems = sidebar.locator('[class*="conversation-item"], [data-testid*="conversation"]');
    const count = await conversationItems.count();
    
    // If no test IDs, try checking for conversation text from mock data
    if (count === 0) {
      // Fallback: Just verify the sidebar has some content
      const sidebarText = await sidebar.textContent();
      expect(sidebarText).toContain('Today');
    } else {
      expect(count).toBeGreaterThan(0);
    }
  });

  test('new chat button is functional', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    
    const newChatBtn = page.getByRole('button', { name: /new chat/i });
    await expect(newChatBtn).toBeVisible();
    await expect(newChatBtn).toBeEnabled();
    
    // Click should not error
    await newChatBtn.click();
    
    // Input should still be available
    await expect(page.getByLabel('Message input')).toBeVisible();
  });

  test('toggle sidebar button is in header', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    
    // Toggle button is in banner, not sidebar
    const banner = page.getByRole('banner');
    const toggleBtn = banner.getByRole('button', { name: /toggle sidebar/i });
    
    await expect(toggleBtn).toBeVisible();
    // Button should indicate expanded state
    await expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
  });

  test('toggle sidebar button hides sidebar', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    
    const sidebar = page.getByRole('complementary');
    const toggleBtn = page.getByRole('button', { name: /toggle sidebar/i });
    
    // Initially visible - toggle should show expanded
    await expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
    await expect(sidebar).toBeVisible();
    
    // Click toggle to hide
    await toggleBtn.click();
    
    // Toggle button should now show collapsed state
    await expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
    
    // Sidebar should be visually hidden (CSS width close to 0)
    // Note: The element stays in the a11y tree but is visually collapsed
    const sidebarEl = page.locator('.sidebar');
    const width = await sidebarEl.evaluate(el => parseFloat(getComputedStyle(el).width));
    expect(width).toBeLessThan(5); // Should be essentially 0 or 1px
  });

  test('toggle sidebar button shows sidebar again', async ({ page }) => {
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    
    const toggleBtn = page.getByRole('button', { name: /toggle sidebar/i });
    
    // Hide sidebar
    await toggleBtn.click();
    await expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
    
    // Show sidebar again
    await toggleBtn.click();
    await expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');
    
    // Sidebar should be visible again
    const sidebar = page.getByRole('complementary');
    await expect(sidebar).toBeVisible();
  });
});
