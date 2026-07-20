/**
 * Workflow 23: Schedule "When" builder — ScheduleCron pure functions.
 */
import { test, expect, setupBasicMocks } from '../fixtures';

const COMPILE_CASES: Array<[Record<string, unknown>, string]> = [
  [{ pattern: 'daily', time: '07:00' }, '0 7 * * *'],
  [{ pattern: 'weekdays', time: '18:30' }, '30 18 * * 1-5'],
  [{ pattern: 'weekly', time: '09:00', days: [4, 1] }, '0 9 * * 1,4'],
  [{ pattern: 'interval', every: 30, unit: 'minutes' }, '*/30 * * * *'],
  [{ pattern: 'interval', every: 2, unit: 'hours' }, '0 */2 * * *'],
  [{ pattern: 'monthly', time: '08:00', monthday: 1 }, '0 8 1 * *'],
];

test.describe('ScheduleCron pure functions', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
    await page.goto('/chat');
  });

  test('compile produces the exact cron for every builder pattern', async ({ page }) => {
    for (const [state, cron] of COMPILE_CASES) {
      const got = await page.evaluate((s) => (window as any).ScheduleCron.compile(s), state);
      expect(got, JSON.stringify(state)).toBe(cron);
    }
  });

  test('recognize round-trips every builder-generated cron', async ({ page }) => {
    for (const [, cron] of COMPILE_CASES) {
      const roundTripped = await page.evaluate((c) => {
        const S = (window as any).ScheduleCron;
        return S.compile(S.recognize(c));
      }, cron);
      expect(roundTripped).toBe(cron);
    }
  });

  test('recognize returns null for anything outside the builder shapes', async ({ page }) => {
    const exotic = ['15 6 * * 2#1', '0 7 * * 1-3', '*/3 * * * *', '0 7 * *',
                    '0 7 * * * *', '61 7 * * *', '0 25 * * *', '0 7 * * 1,1', ''];
    for (const cron of exotic) {
      const got = await page.evaluate((c) => (window as any).ScheduleCron.recognize(c), cron);
      expect(got, cron).toBeNull();
    }
  });

  test('describe renders human sentences and null for exotic crons', async ({ page }) => {
    const cases: Array<[string, string | null]> = [
      ['0 7 * * *', 'every day at 07:00'],
      ['30 18 * * 1-5', 'weekdays at 18:30'],
      ['0 9 * * 1,4', 'every Mon, Thu at 09:00'],
      ['*/30 * * * *', 'every 30 minutes'],
      ['0 8 1 * *', 'monthly on day 1 at 08:00'],
      ['15 6 * * 2#1', null],
    ];
    for (const [cron, sentence] of cases) {
      const got = await page.evaluate((c) => (window as any).ScheduleCron.describe(c), cron);
      expect(got, cron).toBe(sentence);
    }
  });
});
