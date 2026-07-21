/**
 * Workflow 24: Scheduled runs sidebar group
 *
 * Scheduled runs create real conversations (is_scheduled=true from the server).
 * The sidebar keeps personal chats as-is and folds every scheduled conversation
 * into a single collapsible "Scheduled runs (N)" section: collapsed by default,
 * expand/collapse persisted in localStorage, empty group hidden entirely.
 */
import { test, expect, setupBasicMocks } from '../fixtures';
import type { Page } from '@playwright/test';

const SCHEDULED_COLLAPSE_KEY = 'archi_scheduled_group_collapsed';

function conv(id: number, title: string, isScheduled: boolean) {
  const now = new Date().toISOString();
  return {
    conversation_id: id,
    title,
    last_message_at: now,
    created_at: now,
    is_scheduled: isScheduled,
  };
}

const MIXED = [
  conv(1, 'Personal one', false),
  conv(2, 'Personal two', false),
  conv(10, '[Scheduled] daily transfers', true),
  conv(11, '[Scheduled] hourly rates', true),
  conv(12, '[Scheduled] weekly report', true),
];

// Registered AFTER setupBasicMocks so it wins: Playwright runs matching routes in
// reverse registration order (last registered wins) as long as it fully fulfills.
async function mockConversations(page: Page, convos: ReturnType<typeof conv>[]) {
  await page.route('**/api/list_conversations*', async (route) => {
    await route.fulfill({ status: 200, json: { conversations: convos } });
  });
}

test.describe('Scheduled runs sidebar group', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
    await page.route('**/api/load_conversation', async (route) => {
      await route.fulfill({ status: 200, json: { messages: [] } });
    });
  });

  test('personal entries stay ungrouped and scheduled ones fold into one group', async ({ page }) => {
    await mockConversations(page, MIXED);
    await page.goto('/chat');

    const scheduledGroup = page.locator('.conversation-group-scheduled');
    await expect(scheduledGroup).toHaveCount(1);

    // Exactly the 3 scheduled conversations live inside the group.
    await expect(scheduledGroup.locator('.conversation-item')).toHaveCount(3);
    await expect(scheduledGroup.locator('.conversation-group-count')).toHaveText('3');

    // The 2 personal conversations render outside the scheduled group.
    await expect(
      page.locator('.conversation-group:not(.conversation-group-scheduled) .conversation-item')
    ).toHaveCount(2);
  });

  test('scheduled group is collapsed by default and hides its items', async ({ page }) => {
    await mockConversations(page, MIXED);
    await page.goto('/chat');

    const scheduledGroup = page.locator('.conversation-group-scheduled');
    await expect(scheduledGroup).toHaveAttribute('data-collapsed', 'true');
    await expect(scheduledGroup.locator('.conversation-group-toggle'))
      .toHaveAttribute('aria-expanded', 'false');
    await expect(scheduledGroup.locator('.conversation-item').first()).toBeHidden();
  });

  test('clicking the toggle expands the group', async ({ page }) => {
    await mockConversations(page, MIXED);
    await page.goto('/chat');

    const scheduledGroup = page.locator('.conversation-group-scheduled');
    const toggle = scheduledGroup.locator('.conversation-group-toggle');
    await toggle.click();

    await expect(scheduledGroup).toHaveAttribute('data-collapsed', 'false');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(scheduledGroup.locator('.conversation-item').first()).toBeVisible();
  });

  test('expanded state survives a reload (persisted in localStorage)', async ({ page }) => {
    await mockConversations(page, MIXED);
    await page.goto('/chat');

    const scheduledGroup = page.locator('.conversation-group-scheduled');
    await scheduledGroup.locator('.conversation-group-toggle').click();
    await expect(scheduledGroup).toHaveAttribute('data-collapsed', 'false');

    const stored = await page.evaluate(
      (key) => localStorage.getItem(key), SCHEDULED_COLLAPSE_KEY);
    expect(stored).toBe('false');

    await page.reload();

    const afterReload = page.locator('.conversation-group-scheduled');
    await expect(afterReload).toHaveAttribute('data-collapsed', 'false');
    await expect(afterReload.locator('.conversation-group-toggle'))
      .toHaveAttribute('aria-expanded', 'true');
  });

  test('scheduled group is hidden entirely when there are no scheduled runs', async ({ page }) => {
    await mockConversations(page, [conv(1, 'Personal one', false), conv(2, 'Personal two', false)]);
    await page.goto('/chat');

    await expect(page.locator('.conversation-item')).toHaveCount(2);
    await expect(page.locator('.conversation-group-scheduled')).toHaveCount(0);
  });

  test('new scheduled conversations arriving on refresh land in the group', async ({ page }) => {
    // The route reads this closure at request time, so a later refresh sees updates.
    let convos = [conv(1, 'Personal one', false)];
    await page.route('**/api/list_conversations*', async (route) => {
      await route.fulfill({ status: 200, json: { conversations: convos } });
    });

    await page.goto('/chat');
    await expect(page.locator('.conversation-group-scheduled')).toHaveCount(0);

    // A scheduled run creates a conversation; the next list refresh includes it.
    convos = [conv(1, 'Personal one', false), conv(20, '[Scheduled] new run', true)];
    await page.reload();

    const scheduledGroup = page.locator('.conversation-group-scheduled');
    await expect(scheduledGroup).toHaveCount(1);
    await expect(scheduledGroup.locator('.conversation-group-count')).toHaveText('1');
  });
});
