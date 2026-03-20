/**
 * Workflow 21: A/B Testing (Pool-based)
 *
 * Tests for the dedicated A/B admin page, pool management, streaming
 * comparison, vote buttons, preference submission, and metrics.
 */
import {
  test,
  expect,
  setupBasicMocks,
  setupABAdminMocks,
  setupABAdminInactiveMocks,
  mockData,
  createABStreamResponse,
} from '../fixtures';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openABAdminPage(page: import('@playwright/test').Page) {
  await page.goto('/admin/ab-testing');
  await expect(page.locator('#ab-admin-status')).toBeVisible();
}

// =============================================================================
// Admin gating -- chat settings link visibility
// =============================================================================

test.describe('A/B Management Entry Point -- Admin Gating', () => {

  test('chat settings section stays hidden for non-admin users', async ({ page }) => {
    await setupBasicMocks(page);
    await page.goto('/chat');
    await page.waitForTimeout(500);
    const display = await page.locator('#ab-settings-section').evaluate(
      (el: HTMLElement) => el.style.display,
    );
    expect(display).toBe('none');
  });

  test('chat settings shows admin link for admin users', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await page.goto('/chat');
    const display = await page.locator('#ab-settings-section').evaluate(
      (el: HTMLElement) => el.style.display,
    );
    expect(display).toBe('');
    await expect(page.locator('#ab-settings-section .settings-link-btn')).toHaveAttribute('href', '/admin/ab-testing');
  });

  test('dedicated admin page loads for admin users', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-status')).toHaveText('Active');
  });

  test('dedicated admin page shows Inactive when pool is disabled', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminInactiveMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-status')).toHaveText('Inactive');
  });
});

// =============================================================================
// Dedicated page -- variant rendering
// =============================================================================

test.describe('A/B Admin Page -- Variant List', () => {

  test('renders existing variants and their parameters', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    const cards = page.locator('.ab-variant-card');
    await expect(cards).toHaveCount(mockData.abPoolAdmin.variant_details!.length);
    await expect(cards.first().locator('[data-field="label"]')).toHaveValue('CMS CompOps Agent');
    await expect(cards.first().locator('[data-field="agent_spec"]')).toHaveValue('cms-comp-ops.md');
  });

  test('champion select is pre-populated', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-champion')).toHaveValue(mockData.abPoolAdmin.champion!);
  });

  test('agent markdown selector exposes available files', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    const options = page.locator('.ab-variant-card').first().locator('[data-field="agent_spec"] option');
    await expect(options).toHaveCount(mockData.agentsList.agents.length + 1);
  });

  test('provider selector uses provider dropdown options', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    const providerOptions = page.locator('.ab-variant-card').nth(1).locator('[data-field="provider"] option');
    await expect(providerOptions).toHaveCount(mockData.providers.providers.length + 1);
  });
});

// =============================================================================
// Dedicated page -- save / disable interactions
// =============================================================================

test.describe('A/B Admin Page -- Save and Disable', () => {

  test('save button is enabled when champion + 2+ variants are configured', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-save')).toBeEnabled();
  });

  test('save button is disabled when fewer than 2 variants exist', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminInactiveMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-save')).toBeDisabled();
  });

  test('disable button visible when pool is active', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-disable')).toBeVisible();
  });

  test('disable button hidden when pool is inactive', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminInactiveMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-disable')).toBeHidden();
  });

  test('clicking save sends correct payload', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    let savedPayload: any = null;
    await page.route('**/api/ab/pool/settings/set', async (route) => {
      const body = route.request().postDataJSON();
      savedPayload = body;
      await route.fulfill({ status: 200, json: { success: true, ...mockData.abPoolAdmin } });
    });

    await openABAdminPage(page);
    await page.locator('#ab-admin-save').click();

    await page.waitForTimeout(300);
    expect(savedPayload).toBeTruthy();
    expect(savedPayload.champion).toBe(mockData.abPoolAdmin.champion);
    expect(savedPayload).not.toHaveProperty('variants');
    expect(savedPayload.sample_rate).toBe(mockData.abPoolAdmin.sample_rate);
  });

  test('settings save keeps backend-confirmed values visible without relying on a follow-up GET', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route('**/api/agents/list*', async (route) => {
      await route.fulfill({ status: 200, json: mockData.agentsList });
    });
    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({ status: 200, json: mockData.abPoolAdmin });
    });
    await page.route('**/api/ab/pool/settings/set', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          ...mockData.abPoolAdmin,
          sample_rate: 0.4,
          disclosure_mode: 'blind',
          default_trace_mode: 'verbose',
        },
      });
    });

    await openABAdminPage(page);
    await page.locator('#ab-admin-sample-rate').fill('0.4');
    await page.locator('#ab-admin-disclosure-mode').selectOption('blind');
    await page.locator('#ab-admin-trace-mode').selectOption('verbose');
    await page.locator('#ab-admin-save').click();

    await expect(page.locator('#ab-admin-sample-rate')).toHaveValue('0.4');
    await expect(page.locator('#ab-admin-disclosure-mode')).toHaveValue('blind');
    await expect(page.locator('#ab-admin-trace-mode')).toHaveValue('verbose');
  });

  test('clicking save variants sends only variant details', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    let savedPayload: any = null;
    await page.route('**/api/ab/pool/variants/set', async (route) => {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({ status: 200, json: { success: true, ...mockData.abPoolAdmin } });
    });

    await openABAdminPage(page);
    await page.locator('#ab-admin-variant-save').click();

    await page.waitForTimeout(300);
    expect(savedPayload).toBeTruthy();
    expect(savedPayload).toHaveProperty('variants');
    expect(savedPayload).not.toHaveProperty('sample_rate');
    expect(savedPayload.variants).toEqual(expect.arrayContaining(mockData.abPoolAdmin.variant_details!));
  });

  test('variant save keeps backend-confirmed selections visible without relying on a follow-up GET', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route('**/api/agents/list*', async (route) => {
      await route.fulfill({ status: 200, json: mockData.agentsList });
    });
    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({ status: 200, json: mockData.abPoolAdmin });
    });
    await page.route('**/api/ab/pool/variants/set', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          ...mockData.abPoolAdmin,
          variant_details: [
            { label: 'CMS CompOps Agent', agent_spec: 'cms-comp-ops.md' },
            { label: 'Poet', agent_spec: 'challenger-claude.md', provider: 'openrouter', model: 'anthropic/claude-3.5-sonnet' },
          ],
          variants: ['CMS CompOps Agent', 'Poet'],
        },
      });
    });

    await openABAdminPage(page);
    const secondCard = page.locator('.ab-variant-card').nth(1);
    await secondCard.locator('[data-field="label"]').fill('Poet');
    await secondCard.locator('[data-field="agent_spec"]').selectOption('challenger-claude.md');
    await secondCard.locator('[data-field="provider"]').selectOption('openrouter');
    await secondCard.locator('[data-field="model_select"]').selectOption('anthropic/claude-3.5-sonnet');
    await page.locator('#ab-admin-variant-save').click();

    await expect(secondCard.locator('[data-field="label"]')).toHaveValue('Poet');
    await expect(secondCard.locator('[data-field="agent_spec"]')).toHaveValue('challenger-claude.md');
    await expect(secondCard.locator('[data-field="provider"]')).toHaveValue('openrouter');
    await expect(secondCard.locator('[data-field="model_select"]')).toHaveValue('anthropic/claude-3.5-sonnet');
  });

  test('clicking disable calls endpoint and updates UI', async ({ page }) => {
    await setupBasicMocks(page);
    let poolState = { ...mockData.abPoolAdmin };
    await page.route('**/api/agents/list*', async (route) => {
      await route.fulfill({ status: 200, json: mockData.agentsList });
    });
    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({ status: 200, json: poolState });
    });

    let disableCalled = false;
    await page.route('**/api/ab/pool/disable', async (route) => {
      disableCalled = true;
      poolState = { ...mockData.abPoolAdmin, enabled: false, enabled_requested: false };
      await route.fulfill({ status: 200, json: { success: true, ...poolState } });
    });

    await openABAdminPage(page);
    await page.locator('#ab-admin-disable').click();

    await expect(page.locator('#ab-admin-status')).toHaveText('Inactive');
    expect(disableCalled).toBe(true);
  });

  test('validation message when fewer than 2 variants remain', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('.ab-variant-remove').nth(1).click();

    await expect(page.locator('#ab-admin-variant-message')).toContainText('Add at least 2 variants');
  });

  test('settings save ignores unsaved variant label edits', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('.ab-variant-card').first().locator('[data-field="label"]').fill('Renamed baseline');

    await expect(page.locator('#ab-admin-save')).toBeEnabled();
    await expect(page.locator('#ab-admin-champion')).toHaveValue('CMS CompOps Agent');
  });

  test('unsaved draft is restored after navigation', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminInactiveMocks(page);
    await openABAdminPage(page);

    await page.locator('#ab-admin-add-variant').click();
    await page.locator('.ab-variant-card').first().locator('[data-field="label"]').fill('Draft Variant');

    await page.goto('/chat');
    await openABAdminPage(page);

    await expect(page.locator('.ab-variant-card').first().locator('[data-field="label"]')).toHaveValue('Draft Variant');
  });
});

// =============================================================================
// Dedicated page -- champion selection
// =============================================================================

test.describe('A/B Admin Page -- Champion Selection', () => {

  test('changing champion select updates champion', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('#ab-admin-champion').selectOption('Challenger GPT-4o');
    await expect(page.locator('#ab-admin-champion')).toHaveValue('Challenger GPT-4o');
  });

  test('adding an unsaved variant does not change champion choices yet', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('#ab-admin-add-variant').click();
    await page.locator('.ab-variant-card').last().locator('[data-field="label"]').fill('Challenger Claude');

    const championOptions = page.locator('#ab-admin-champion option');
    await expect(championOptions).toHaveCount(2);
  });

  test('removing an unsaved champion variant does not change saved champion selection', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('.ab-variant-remove').first().click();

    await expect(page.locator('#ab-admin-champion')).toHaveValue('CMS CompOps Agent');
  });
});

// =============================================================================
// A/B comparison streaming
// =============================================================================

test.describe('A/B Comparison Streaming', () => {

  test('sends A/B comparison and shows two arms', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse({
      armAContent: 'Champion says hello',
      armBContent: 'Challenger says hi',
    });

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    const comparison = page.locator('#ab-comparison-active');
    await expect(comparison).toBeVisible();

    const arms = comparison.locator('.ab-arm');
    await expect(arms).toHaveCount(2);

    await expect(comparison.locator('.ab-arm-label').first()).toHaveText('Response A');
    await expect(comparison.locator('.ab-arm-label').nth(1)).toHaveText('Response B');
  });

  test('A/B stream populates content in both arms', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse({
      armAContent: 'Alpha answer',
      armBContent: 'Beta answer',
    });

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test AB');
    await page.getByRole('button', { name: 'Send message' }).click();

    const armA = page.locator('.ab-arm').first().locator('.message-content');
    const armB = page.locator('.ab-arm').nth(1).locator('.message-content');
    await expect(armA).toContainText('Alpha answer');
    await expect(armB).toContainText('Beta answer');
  });

  test('A/B headers render cleanly with named disclosure and minimal trace mode', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          enabled: true,
          is_admin: false,
          sample_rate: 1,
          disclosure_mode: 'named',
          default_trace_mode: 'minimal',
          max_pending_per_conversation: 1,
        },
      });
    });

    const abStream = [
      JSON.stringify({ type: 'meta', event: 'stream_started' }),
      JSON.stringify({
        type: 'ab_arms',
        arm_a_name: 'CMS CompOps Agent',
        arm_b_name: 'Challenger GPT-4o',
        disclosure_mode: 'named',
      }),
      JSON.stringify({ arm: 'a', type: 'chunk', content: 'Champion says hello' }),
      JSON.stringify({ arm: 'b', type: 'chunk', content: 'Challenger says hi' }),
      JSON.stringify({
        type: 'ab_meta',
        comparison_id: 42,
        conversation_id: 1,
        arm_a_message_id: 101,
        arm_b_message_id: 102,
        arm_a_variant: 'CMS CompOps Agent',
        arm_b_variant: 'Challenger GPT-4o',
        disclosure_mode: 'named',
      }),
    ].join('\n') + '\n';

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-arm-title-row')).toHaveCount(2);
    await expect(page.locator('.ab-arm-variant-name').first()).toHaveText('CMS CompOps Agent');
    await expect(page.locator('.ab-arm-variant-name').nth(1)).toHaveText('Challenger GPT-4o');
    await expect(page.locator('.ab-comparison .trace-container')).toHaveCount(0);
  });

  test('A/B trace headers match the standard agent activity presentation', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          enabled: true,
          is_admin: false,
          sample_rate: 1,
          disclosure_mode: 'blind',
          default_trace_mode: 'normal',
          max_pending_per_conversation: 1,
        },
      });
    });

    const abStream = [
      JSON.stringify({ type: 'meta', event: 'stream_started' }),
      JSON.stringify({ arm: 'a', type: 'chunk', content: 'Champion says hello' }),
      JSON.stringify({ arm: 'b', type: 'chunk', content: 'Challenger says hi' }),
      JSON.stringify({
        type: 'ab_meta',
        comparison_id: 42,
        conversation_id: 1,
        arm_a_message_id: 101,
        arm_b_message_id: 102,
        arm_a_variant: 'normal',
        arm_b_variant: 'mad',
        disclosure_mode: 'blind',
      }),
    ].join('\n') + '\n';

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-comparison .trace-container')).toHaveCount(2);
    await expect(page.locator('.ab-comparison .trace-label').first()).toHaveText('Agent Activity');
    await expect(page.locator('.ab-comparison .trace-toggle')).toHaveCount(2);
  });

  test('vote buttons appear after A/B stream completes', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse();

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test vote');
    await page.getByRole('button', { name: 'Send message' }).click();

    const voteContainer = page.locator('.ab-vote-container');
    await expect(voteContainer).toBeVisible();

    await expect(page.locator('.ab-vote-btn-a')).toBeVisible();
    await expect(page.locator('.ab-vote-btn-tie')).toBeVisible();
    await expect(page.locator('.ab-vote-btn-b')).toBeVisible();

    await expect(page.locator('.ab-vote-prompt')).toContainText('Which response do you prefer?');
  });

  test('input stays disabled until vote is submitted', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse();

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.route('**/api/ab/preference', async (route) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test disabled');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await expect(page.getByLabel('Message input')).toBeDisabled();

    await page.locator('.ab-vote-btn-a').click();

    await expect(page.getByLabel('Message input')).not.toBeDisabled();
  });
});

// =============================================================================
// Vote submission
// =============================================================================

test.describe('A/B Vote Submission', () => {

  async function setupABWithVote(page: any) {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse({ comparisonId: 99 });

    await page.route('**/api/ab/compare', async (route: any) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });
  }

  test('voting A sends preference "a" to server', async ({ page }) => {
    await setupABWithVote(page);

    let submittedPreference: string | null = null;
    await page.route('**/api/ab/preference', async (route: any) => {
      const body = route.request().postDataJSON();
      submittedPreference = body.preference;
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Vote A');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    expect(submittedPreference).toBe('a');
  });

  test('voting B sends preference "b" to server', async ({ page }) => {
    await setupABWithVote(page);

    let submittedPreference: string | null = null;
    await page.route('**/api/ab/preference', async (route: any) => {
      const body = route.request().postDataJSON();
      submittedPreference = body.preference;
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Vote B');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-b').click();

    expect(submittedPreference).toBe('b');
  });

  test('voting Tie sends preference "tie" to server', async ({ page }) => {
    await setupABWithVote(page);

    let submittedPreference: string | null = null;
    await page.route('**/api/ab/preference', async (route: any) => {
      const body = route.request().postDataJSON();
      submittedPreference = body.preference;
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Vote Tie');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-tie').click();

    expect(submittedPreference).toBe('tie');
  });

  test('vote sends correct comparison_id', async ({ page }) => {
    await setupABWithVote(page);

    let sentComparisonId: number | null = null;
    await page.route('**/api/ab/preference', async (route: any) => {
      const body = route.request().postDataJSON();
      sentComparisonId = body.comparison_id;
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Check ID');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    expect(sentComparisonId).toBe(99);
  });

  test('vote buttons disappear after voting', async ({ page }) => {
    await setupABWithVote(page);

    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Dismiss vote');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    await expect(page.locator('.ab-vote-container')).toHaveCount(0);
  });

  test('choosing A collapses comparison to single message', async ({ page }) => {
    await setupABWithVote(page);

    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Collapse test');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    await expect(page.locator('#ab-comparison-active')).toHaveCount(0);
  });

  test('choosing Tie keeps both arms with tie styling', async ({ page }) => {
    await setupABWithVote(page);

    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Tie test');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-tie').click();

    await expect(page.locator('.ab-arm-tie')).toHaveCount(2);
  });
});

// =============================================================================
// A/B error handling
// =============================================================================

test.describe('A/B Error Handling', () => {

  test('error in A/B stream shows error message', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const errorStream = JSON.stringify({
      type: 'error',
      message: 'Both arms timed out',
    }) + '\n';

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: errorStream });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Error test');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-error-message')).toBeVisible();
    await expect(page.locator('.ab-error-message')).toContainText('Both arms timed out');
  });

  test('HTTP error from A/B compare re-enables input', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 500, body: 'Internal Server Error' });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('500 error');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByLabel('Message input')).not.toBeDisabled();
    await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible();
  });
});

// =============================================================================
// Normal mode -- A/B not engaged when pool is inactive
// =============================================================================

test.describe('A/B Inactive -- Normal Chat', () => {

  test('chat uses single stream when A/B pool is not enabled', async ({ page }) => {
    await setupBasicMocks(page);

    let abCompareCalled = false;
    await page.route('**/api/ab/compare', async (route) => {
      abCompareCalled = true;
      await route.fulfill({ status: 200, body: '' });
    });

    await page.route('**/api/get_chat_response_stream', async (route) => {
      const body = JSON.stringify({
        type: 'final',
        response: 'Normal response',
        message_id: 1,
        user_message_id: 1,
        conversation_id: 1,
      }) + '\n';
      await route.fulfill({ status: 200, contentType: 'text/plain', body });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    await page.waitForTimeout(500);
    expect(abCompareCalled).toBe(false);

    await expect(page.locator('#ab-comparison-active')).toHaveCount(0);
  });
});
