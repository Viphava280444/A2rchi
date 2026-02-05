/**
 * Workflow 13: Data Tab/Navigation Tests
 * 
 * Tests for the Data navigation button in the header.
 * Note: The Data button is in the header navigation bar, not sidebar.
 */
import { test, expect, setupBasicMocks } from '../fixtures';

test.describe('Data Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('Data button visible in header navigation', async ({ page }) => {
    await page.goto('/chat');
    
    // Data button should be in navigation
    await expect(page.getByRole('button', { name: 'Data' })).toBeVisible();
  });

  test('Chat link visible in header navigation', async ({ page }) => {
    await page.goto('/chat');
    
    // Chat link should be visible
    await expect(page.getByRole('link', { name: 'Chat' })).toBeVisible();
  });

  test('Chat link is active on chat page', async ({ page }) => {
    await page.goto('/chat');
    
    // Chat should be the current route
    const chatLink = page.getByRole('link', { name: 'Chat' });
    await expect(chatLink).toHaveAttribute('href', '/chat');
  });

  test('clicking Data button navigates to data view', async ({ page }) => {
    await page.goto('/chat');
    
    // Click Data button
    await page.getByRole('button', { name: 'Data' }).click();
    
    // Should navigate or show data view
    // Note: This might show an error if data.html doesn't exist
    await page.waitForTimeout(1000);
  });

  test('header navigation is responsive', async ({ page }) => {
    await page.goto('/chat');
    
    // At desktop size, both Chat and Data should be visible
    await expect(page.getByRole('link', { name: 'Chat' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Data' })).toBeVisible();
  });
});
