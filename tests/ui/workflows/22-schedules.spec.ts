/**
 * Workflow 22: Playbook Schedules Settings section
 */
import { test, expect, setupBasicMocks } from '../fixtures';

test.describe('Schedules Settings', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  async function openSchedules(page: import('@playwright/test').Page) {
    await page.goto('/chat');
    await page.getByRole('button', { name: /settings/i }).click();
    await page.getByRole('button', { name: 'Schedules' }).click();
  }

  test('schedules nav item opens the section', async ({ page }) => {
    await openSchedules(page);
    await expect(page.getByRole('heading', { name: 'Schedules' })).toBeVisible();
    await expect(page.locator('.schedules-empty')).toBeVisible();
  });

  test('new-schedule opens the editor with builder defaults, cron hidden', async ({ page }) => {
    await openSchedules(page);
    await page.locator('.schedules-new').click();
    await expect(page.locator('.schedule-modal')).toBeVisible();
    await expect(page.locator('#schedule-repeats')).toHaveValue('daily');
    await expect(page.locator('#schedule-time')).toHaveValue('07:00');
    await expect(page.locator('#schedule-cron')).toHaveValue('0 7 * * *'); // hidden but synced
    await expect(page.locator('#schedule-advanced-body')).toBeHidden();
  });

  test('saving a schedule POSTs the form payload', async ({ page }) => {
    await openSchedules(page);
    await page.locator('.schedules-new').click();
    await page.locator('#schedule-name').fill('daily-transfers');
    await page.locator('#schedule-recipients').fill('ops@cern.ch');

    const [request] = await Promise.all([
      page.waitForRequest((r) => r.url().endsWith('/api/schedules') && r.method() === 'POST'),
      page.locator('.schedule-save').click(),
    ]);
    const body = request.postDataJSON();
    expect(body.name).toBe('daily-transfers');
    expect(body.recipients).toEqual(['ops@cern.ch']);
    expect(body.cron).toBe('0 7 * * *');
    await expect(page.locator('.schedule-modal')).toBeHidden();
  });

  test('a listed schedule shows Run now and queues on click', async ({ page }) => {
    await page.route('**/api/schedules**', (route) => {
      if (route.request().method() === 'GET' && !route.request().url().includes('/runs')) {
        return route.fulfill({
          status: 200,
          json: { schedules: [{
            id: 1, name: 'daily-transfers', playbook_id: 7, cron: '0 7 * * *',
            timezone: 'UTC', mode: 'digest', recipients: ['ops@cern.ch'],
            enabled: true, consecutive_failures: 0,
            next_run_at: '2026-07-15T05:00:00+00:00',
          }] },
        });
      }
      return route.fallback();
    });
    await openSchedules(page);
    await page.locator('[data-action="run"]').click();
    await expect(page.locator('#schedules-status')).toContainText(/queued/i);
  });

  test('editing a schedule PATCHes without playbook_id rejection', async ({ page }) => {
    await page.route('**/api/schedules**', (route) => {
      if (route.request().method() === 'GET' && !route.request().url().includes('/runs')) {
        return route.fulfill({
          status: 200,
          json: { schedules: [{
            id: 1, name: 'daily-transfers', playbook_id: 7, cron: '0 7 * * *',
            timezone: 'UTC', mode: 'digest', recipients: ['ops@cern.ch'],
            enabled: true, consecutive_failures: 0,
            next_run_at: '2026-07-15T05:00:00+00:00',
          }] },
        });
      }
      return route.fallback();
    });
    await openSchedules(page);
    await page.locator('[data-action="edit"]').click();
    await expect(page.locator('.schedule-modal')).toBeVisible();
    await page.locator('#schedule-name').fill('renamed');

    const [request] = await Promise.all([
      page.waitForRequest((r) => r.url().includes('/api/schedules/1') && r.method() === 'PATCH'),
      page.locator('.schedule-save').click(),
    ]);
    expect(request.postDataJSON().name).toBe('renamed');
    await expect(page.locator('.schedule-modal')).toBeHidden();
  });

  test('weekly builder compiles day chips into the save payload', async ({ page }) => {
    await openSchedules(page);
    await page.locator('.schedules-new').click();
    await page.locator('#schedule-name').fill('weekly-report');
    await page.locator('#schedule-recipients').fill('ops@cern.ch');
    await page.locator('#schedule-repeats').selectOption('weekly');
    await page.locator('#schedule-time').fill('09:00');
    // Mon is preselected; add Thu
    await page.locator('#schedule-days [data-day="4"]').click();
    const [request] = await Promise.all([
      page.waitForRequest((r) => r.url().endsWith('/api/schedules') && r.method() === 'POST'),
      page.locator('.schedule-save').click(),
    ]);
    expect(request.postDataJSON().cron).toBe('0 9 * * 1,4');
  });

  test('editing a builder-shaped schedule restores the builder state', async ({ page }) => {
    await page.route('**/api/schedules**', (route) => {
      if (route.request().method() === 'GET' && !route.request().url().includes('/runs')) {
        return route.fulfill({
          status: 200,
          json: { schedules: [{
            id: 1, name: 'weekly-report', playbook_id: 7, cron: '0 9 * * 1,4',
            timezone: 'UTC', mode: 'digest', recipients: ['ops@cern.ch'],
            enabled: true, consecutive_failures: 0,
            next_run_at: '2026-07-23T09:00:00+00:00',
          }] },
        });
      }
      return route.fallback();
    });
    await openSchedules(page);
    await page.locator('[data-action="edit"]').click();
    await expect(page.locator('#schedule-repeats')).toHaveValue('weekly');
    await expect(page.locator('#schedule-time')).toHaveValue('09:00');
    await expect(page.locator('#schedule-days [data-day="1"]')).toHaveClass(/on/);
    await expect(page.locator('#schedule-days [data-day="4"]')).toHaveClass(/on/);
    await expect(page.locator('#schedule-days [data-day="2"]')).not.toHaveClass(/on/);
  });

  test('editing an exotic cron opens in Custom mode with the string preserved', async ({ page }) => {
    await page.route('**/api/schedules**', (route) => {
      if (route.request().method() === 'GET' && !route.request().url().includes('/runs')) {
        return route.fulfill({
          status: 200,
          json: { schedules: [{
            id: 1, name: 'first-tuesday', playbook_id: 7, cron: '15 6 * * 2#1',
            timezone: 'Europe/Zurich', mode: 'digest', recipients: ['ops@cern.ch'],
            enabled: true, consecutive_failures: 0,
            next_run_at: '2026-08-04T04:15:00+00:00',
          }] },
        });
      }
      return route.fallback();
    });
    await openSchedules(page);
    await page.locator('[data-action="edit"]').click();
    await expect(page.locator('#schedule-repeats')).toHaveValue('custom');
    await expect(page.locator('#schedule-advanced-body')).toBeVisible();
    await expect(page.locator('#schedule-cron')).toHaveValue('15 6 * * 2#1');
  });
});
