/**
 * Workflow 15: Error Handling Tests
 * 
 * Tests for graceful error handling and user feedback.
 */
import { test, expect, setupBasicMocks } from '../fixtures';

test.describe('Error Handling', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('network error allows retry', async ({ page }) => {
    let callCount = 0;
    await page.route('**/api/get_chat_response_stream', async (route) => {
      callCount++;
      if (callCount === 1) {
        await route.abort('failed');
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'text/plain',
          body: '{"type":"chunk","content":"Success!"}\n{"type":"done"}\n',
        });
      }
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Test');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Wait for error state
    await page.waitForTimeout(1000);
    
    // Should be able to try again
    await expect(page.getByLabel('Message input')).not.toBeDisabled();
  });

  test('500 error does not crash page', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      await route.fulfill({ status: 500, body: 'Internal Server Error' });
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Test');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Wait for response
    await page.waitForTimeout(1000);
    
    // Page should still be functional
    await expect(page.getByLabel('Message input')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible();
  });

  test('input re-enabled after error', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      await route.fulfill({ status: 500, body: 'Error' });
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Test');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Wait for error to be processed
    await page.waitForTimeout(2000);
    
    // Input should be re-enabled for retry
    await expect(page.getByLabel('Message input')).not.toBeDisabled();
  });

  test('timeout error handled gracefully', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      // Just hang - never respond
      await new Promise(() => {});
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Test');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Should show stop button during streaming - use exact name to avoid multiple matches
    const stopBtn = page.getByRole('button', { name: 'Stop streaming' });
    await expect(stopBtn).toBeVisible({ timeout: 2000 });
    
    // Can click stop
    await stopBtn.click();
    
    // Input should be re-enabled
    await expect(page.getByLabel('Message input')).not.toBeDisabled({ timeout: 2000 });
  });

  test('invalid JSON response does not crash', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: 'This is not JSON\n{broken',
      });
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Test');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Wait for processing
    await page.waitForTimeout(1000);
    
    // Page should still be functional
    await expect(page.getByLabel('Message input')).toBeVisible();
  });

  test('can send new message after error', async ({ page }) => {
    let callCount = 0;
    await page.route('**/api/get_chat_response_stream', async (route) => {
      callCount++;
      if (callCount === 1) {
        await route.fulfill({ status: 500, body: 'Error' });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'text/plain',
          body: '{"type":"chunk","content":"Second attempt worked!"}\n{"type":"done"}\n',
        });
      }
    });

    await page.goto('/chat');
    
    // First message - will error
    await page.getByLabel('Message input').fill('First');
    await page.getByRole('button', { name: 'Send message' }).click();
    await page.waitForTimeout(1000);
    
    // Second message - should work
    await page.getByLabel('Message input').fill('Second');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Should see the response
    await expect(page.getByText('Second attempt worked!')).toBeVisible({ timeout: 5000 });
  });
});
