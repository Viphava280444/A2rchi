/**
 * Workflow 12: Agent Trace/Tool Call Visualization Tests
 * 
 * Tests for viewing tool calls in message responses.
 * The current UI may show tool calls inline or in collapsible sections.
 */
import { test, expect, setupBasicMocks, createToolCallEvents } from '../fixtures';

test.describe('Agent Trace Visualization', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('response with tool calls displays correctly', async ({ page }) => {
    const events = createToolCallEvents('search', { query: 'test' }, 'Found results');
    
    await page.route('**/api/get_chat_response_stream', async (route) => {
      let body = events.map(e => JSON.stringify(e)).join('\n') + '\n';
      body += '{"type":"final","response":"Based on the search, here is the answer.","message_id":1,"user_message_id":1,"conversation_id":1}\n';
      
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body,
      });
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Search for info');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Should show the final response
    await expect(page.getByText('Based on the search')).toBeVisible({ timeout: 5000 });
  });

  test('streaming response shows content progressively', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const chunks = [
        '{"type":"chunk","content":"First part. "}',
        '{"type":"chunk","content":"Second part. "}',
        '{"type":"chunk","content":"Third part."}',
        '{"type":"done"}',
      ];
      
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: chunks.join('\n') + '\n',
      });
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Test');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Should show the full response
    await expect(page.getByText('First part')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Third part')).toBeVisible();
  });

  test('tool call events do not break response flow', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"tool_call","name":"search","args":{"query":"test"}}',
        '{"type":"tool_result","result":"found data"}',
        '{"type":"chunk","content":"Here is the answer."}',
        '{"type":"done"}',
      ];
      
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Search');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // The response should be visible
    await expect(page.getByText('Here is the answer')).toBeVisible({ timeout: 5000 });
  });

  test('message meta shows agent info after response', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: '{"type":"chunk","content":"Response"}\n{"type":"done"}\n',
      });
    });

    await page.goto('/chat');
    
    await page.getByLabel('Message input').fill('Test');
    await page.getByRole('button', { name: 'Send message' }).click();
    
    // Should show agent name in the message area (not header/sidebar)
    await expect(page.getByRole('main').getByText('archi').first()).toBeVisible({ timeout: 5000 });
  });
});
