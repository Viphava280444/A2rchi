/**
 * Workflow 7: Agent Info Modal Tests
 * 
 * Tests for viewing agent information and configuration details.
 * The modal shows: Active agent, Model, Pipeline, Embedding, Data sources
 */
import { test, expect, setupBasicMocks } from '../fixtures';

test.describe('Agent Info Modal', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('agent info button visible in UI', async ({ page }) => {
    await page.goto('/chat');
    
    await expect(page.getByRole('button', { name: /agent info/i })).toBeVisible();
  });

  test('clicking agent info button opens modal', async ({ page }) => {
    await page.goto('/chat');
    
    await page.getByRole('button', { name: /agent info/i }).click();
    
    await expect(page.getByRole('heading', { name: 'Agent Info' })).toBeVisible();
  });

  test('modal displays active agent name', async ({ page }) => {
    await page.goto('/chat');
    
    await page.getByRole('button', { name: /agent info/i }).click();
    
    await expect(page.getByRole('heading', { name: 'Active agent' })).toBeVisible();
    // The agent name should be visible in the modal
    await expect(page.getByLabel('Agent Info', { exact: true }).getByText('cms_simple')).toBeVisible();
  });

  test('modal displays model information', async ({ page }) => {
    await page.goto('/chat');
    
    await page.getByRole('button', { name: /agent info/i }).click();
    
    await expect(page.getByRole('heading', { name: 'Model' })).toBeVisible();
    // Should show the model info - either pipeline default class or model name
    const modal = page.getByLabel('Agent Info', { exact: true });
    await expect(modal.getByText(/OpenRouterLLM|openai\/gpt-5-nano/)).toBeVisible();
  });

  test('modal displays pipeline information', async ({ page }) => {
    await page.goto('/chat');
    
    await page.getByRole('button', { name: /agent info/i }).click();
    
    await expect(page.getByRole('heading', { name: 'Pipeline' })).toBeVisible();
  });

  test('modal displays data sources', async ({ page }) => {
    await page.goto('/chat');
    
    await page.getByRole('button', { name: /agent info/i }).click();
    
    await expect(page.getByRole('heading', { name: 'Data sources' })).toBeVisible();
  });

  test('close button closes modal', async ({ page }) => {
    await page.goto('/chat');
    
    await page.getByRole('button', { name: /agent info/i }).click();
    await expect(page.getByRole('heading', { name: 'Agent Info' })).toBeVisible();
    
    await page.getByRole('button', { name: /close agent info/i }).click();
    
    await expect(page.getByRole('heading', { name: 'Agent Info' })).not.toBeVisible();
  });

  test('Escape key closes modal', async ({ page }) => {
    await page.goto('/chat');
    
    await page.getByRole('button', { name: /agent info/i }).click();
    await expect(page.getByRole('heading', { name: 'Agent Info' })).toBeVisible();
    
    await page.keyboard.press('Escape');
    
    await expect(page.getByRole('heading', { name: 'Agent Info' })).not.toBeVisible();
  });
});
