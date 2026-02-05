/**
 * Workflow 5: Provider & Model Selection Tests
 * 
 * Tests for selecting providers and models via the Settings modal.
 * The provider/model selection is in Settings > Models tab.
 */
import { test, expect, setupBasicMocks } from '../fixtures';

test.describe('Provider & Model Selection', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('default agent selector shows available agents', async ({ page }) => {
    await page.goto('/chat');
    
    // The footer has a simple agent selector (model-select-a)
    const agentSelect = page.locator('#model-select-a');
    await expect(agentSelect).toBeVisible();
    
    // Should have at least one agent option (mock data has 2: cms_simple, test_config)
    const optionCount = await agentSelect.locator('option').count();
    expect(optionCount).toBeGreaterThanOrEqual(1);
  });

  test('settings modal opens and shows Models tab', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    
    // Should show Settings modal with Models tab
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Models' })).toBeVisible();
  });

  test('provider dropdown lists available providers', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    
    // Provider select should be visible in the Model Provider region
    const providerSelect = page.getByRole('region', { name: 'Model Provider' }).getByRole('combobox', { name: 'Provider' });
    await expect(providerSelect).toBeVisible();
    
    // Should have pipeline default plus providers (real UI has 5: default + 4 providers)
    const optionCount = await providerSelect.locator('option').count();
    expect(optionCount).toBeGreaterThanOrEqual(2); // At least default + one provider
  });

  test('selecting provider enables model dropdown', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    
    // Get the provider and model selects within the Model Provider region
    const region = page.getByRole('region', { name: 'Model Provider' });
    const modelSelect = region.getByRole('combobox', { name: 'Model' });
    
    // Model dropdown starts disabled when using pipeline default
    await expect(modelSelect).toBeDisabled();
    
    // Select a provider (OpenRouter is enabled in our mock data)
    await region.getByRole('combobox', { name: 'Provider' }).selectOption('OpenRouter');
    
    // Model dropdown should now be enabled
    await expect(modelSelect).toBeEnabled();
  });

  test('model dropdown lists models for selected provider', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    
    const region = page.getByRole('region', { name: 'Model Provider' });
    
    // Select OpenRouter
    await region.getByRole('combobox', { name: 'Provider' }).selectOption('OpenRouter');
    
    // Model dropdown should have options
    const modelSelect = region.getByRole('combobox', { name: 'Model' });
    const optionCount = await modelSelect.locator('option').count();
    expect(optionCount).toBeGreaterThan(0);
  });

  test('pipeline default option clears provider selection', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    
    const region = page.getByRole('region', { name: 'Model Provider' });
    const providerSelect = region.getByRole('combobox', { name: 'Provider' });
    const modelSelect = region.getByRole('combobox', { name: 'Model' });
    
    // Select a provider first
    await providerSelect.selectOption('OpenRouter');
    
    // Model should be enabled
    await expect(modelSelect).toBeEnabled();
    
    // Select pipeline default
    await providerSelect.selectOption('Use pipeline default');
    
    // Model should be disabled again
    await expect(modelSelect).toBeDisabled();
  });

  test('shows current pipeline default info', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    
    // Should show info about pipeline default in the Model Provider region
    // The span with status-text shows "Using pipeline default: ..."
    const region = page.getByRole('region', { name: 'Model Provider' });
    await expect(region.getByText(/Using pipeline default:/)).toBeVisible();
  });

  test('API Keys tab is accessible', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    
    // Click API Keys tab
    await page.getByRole('button', { name: 'API Keys' }).click();
    
    // Should show API Keys content - modal should still be open
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
  });

  test('Advanced tab is accessible', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    
    // Click Advanced tab
    await page.getByRole('button', { name: 'Advanced' }).click();
    
    // Should show Advanced settings content
    // Just verify the tab was clickable and modal is still open
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
  });

  test('close button closes settings modal', async ({ page }) => {
    await page.goto('/chat');
    
    // Open settings
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    
    // Close via button
    await page.getByRole('button', { name: 'Close settings' }).click();
    
    // Modal should be gone
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).not.toBeVisible();
  });
});
