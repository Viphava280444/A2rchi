/**
 * Shared test fixtures and utilities for archi Chat UI tests
 */
import { test as base, expect, Page } from '@playwright/test';

// =============================================================================
// Mock Data
// =============================================================================

export const mockData = {
  configs: {
    options: [{ name: 'cms_simple' }, { name: 'test_config' }],
  },

  conversations: [
    {
      conversation_id: 1,
      title: 'Test Conversation',
      last_message_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
    },
    {
      conversation_id: 2,
      title: 'Another Chat',
      last_message_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
    },
  ],

  providers: {
    providers: [
      {
        type: 'openrouter',
        display_name: 'OpenRouter',
        enabled: true,
        models: [
          { id: 'openai/gpt-4o', name: 'GPT-4o', display_name: 'GPT-4o' },
          { id: 'anthropic/claude-3.5-sonnet', name: 'Claude 3.5 Sonnet', display_name: 'Claude 3.5 Sonnet' },
          { id: '__custom__', name: 'Custom Model', display_name: 'Custom Model...' },
        ],
        default_model: 'openai/gpt-4o',
      },
      {
        type: 'openai',
        display_name: 'OpenAI',
        enabled: false,
        models: [],
      },
    ],
  },

  pipelineDefault: {
    model_class: 'OpenRouterLLM',
    model_name: 'openai/gpt-5-nano',
    model_label: 'gpt-5-nano',
  },

  agentInfo: {
    config_name: 'cms_simple',
    agent_name: 'CMS CompOps Agent',
    description: 'A helpful assistant for CMS Computing Operations',
    pipeline: 'CMSCompOpsAgent',
    embedding_name: 'HuggingFaceEmbeddings',
    data_sources: ['web', 'local_files'],
  },

  providerKeys: {
    providers: [
      { provider: 'openrouter', display_name: 'OpenRouter', configured: true, has_session_key: false },
      { provider: 'openai', display_name: 'OpenAI', configured: false, has_session_key: false },
    ],
  },

  // A/B Testing mock data ---------------------------------------------------

  agentsList: {
    agents: [
      { name: 'CMS CompOps Agent', filename: 'cms-comp-ops.md', ab_only: false },
      { name: 'Reviewer Agent', filename: 'reviewer.md', ab_only: false },
    ],
    active_name: 'CMS CompOps Agent',
  },

  abAgentsList: {
    agents: [
      { name: 'Baseline AB Agent', filename: 'baseline-ab.md', ab_only: true },
      { name: 'Poet AB Agent', filename: 'poet-ab.md', ab_only: true },
      { name: 'Critic AB Agent', filename: 'critic-ab.md', ab_only: true },
    ],
    active_name: null,
  },

  abPoolAdmin: {
    enabled: true,
    is_admin: true,
    can_manage: true,
    enabled_requested: true,
    champion: 'Baseline',
    variants: ['Baseline', 'Poet'],
    variant_details: [
      { label: 'Baseline', agent_spec: 'baseline-ab.md' },
      { label: 'Poet', agent_spec: 'poet-ab.md', provider: 'openrouter', model: 'anthropic/claude-3.5-sonnet' },
    ],
    sample_rate: 1,
    disclosure_mode: 'post_vote_reveal',
    default_trace_mode: 'minimal',
    max_pending_per_conversation: 1,
    defaults: {
      provider: 'openrouter',
      model: 'openai/gpt-4o',
      recursion_limit: 50,
      num_documents_to_retrieve: 5,
      ab_agents_dir: '/root/archi/ab_agents',
      ab_agents_dir_configured: true,
    },
    warnings: [],
  },

  abPoolAdminInactive: {
    enabled: false,
    is_admin: true,
    can_manage: true,
    enabled_requested: false,
    champion: '',
    variants: [],
    variant_details: [],
    sample_rate: 1,
    disclosure_mode: 'post_vote_reveal',
    default_trace_mode: 'minimal',
    max_pending_per_conversation: 1,
    defaults: {
      provider: 'openrouter',
      model: 'openai/gpt-4o',
      recursion_limit: 50,
      num_documents_to_retrieve: 5,
      ab_agents_dir: '/root/archi/ab_agents',
      ab_agents_dir_configured: true,
    },
    warnings: ['A/B testing is inactive until at least two variants are configured.'],
  },

  abPoolNonAdmin: {
    enabled: true,
    is_admin: false,
    can_manage: false,
  },

  abMetrics: {
    metrics: [
      {
        variant: 'Baseline',
        is_champion: true,
        comparisons: 10,
        wins: 6,
        losses: 3,
        ties: 1,
        win_rate: 0.6,
      },
      {
        variant: 'Poet',
        is_champion: false,
        comparisons: 10,
        wins: 3,
        losses: 6,
        ties: 1,
        win_rate: 0.3,
      },
    ],
  },
};

// =============================================================================
// Stream Response Helpers
// =============================================================================

export function createStreamResponse(content: string, options: {
  messageId?: number;
  conversationId?: number;
  includeChunks?: boolean;
} = {}) {
  const { messageId = 1, conversationId = 1, includeChunks = false } = options;
  
  if (includeChunks) {
    const chunks = content.split(' ');
    const events = chunks.map(chunk => 
      JSON.stringify({ type: 'chunk', content: chunk + ' ' })
    );
    events.push(JSON.stringify({
      type: 'final',
      response: content,
      message_id: messageId,
      user_message_id: messageId,
      conversation_id: conversationId,
    }));
    return events.join('\n');
  }
  
  return JSON.stringify({
    type: 'final',
    response: content,
    message_id: messageId,
    user_message_id: messageId,
    conversation_id: conversationId,
  }) + '\n';
}

export function createToolCallEvents(toolName: string, args: object, output: string, options: {
  toolCallId?: string;
  durationMs?: number;
  status?: 'success' | 'error';
} = {}) {
  const { toolCallId = 'tc_1', durationMs = 150, status = 'success' } = options;
  
  return [
    { type: 'tool_start', tool_call_id: toolCallId, tool_name: toolName, tool_args: args },
    { type: 'tool_output', tool_call_id: toolCallId, output },
    { type: 'tool_end', tool_call_id: toolCallId, status, duration_ms: durationMs },
  ];
}

// =============================================================================
// Page Setup Helpers
// =============================================================================

export async function setupBasicMocks(page: Page) {
  await page.route('**/api/get_configs', async (route) => {
    await route.fulfill({ status: 200, json: mockData.configs });
  });

  await page.route('**/api/list_conversations*', async (route) => {
    await route.fulfill({ status: 200, json: { conversations: mockData.conversations } });
  });

  await page.route('**/api/providers', async (route) => {
    await route.fulfill({ status: 200, json: mockData.providers });
  });

  await page.route('**/api/pipeline/default_model', async (route) => {
    await route.fulfill({ status: 200, json: mockData.pipelineDefault });
  });

  await page.route('**/api/agent/info*', async (route) => {
    await route.fulfill({ status: 200, json: mockData.agentInfo });
  });

  await page.route('**/api/providers/keys', async (route) => {
    await route.fulfill({ status: 200, json: mockData.providerKeys });
  });

  await page.route('**/api/new_conversation', async (route) => {
    await route.fulfill({ status: 200, json: { conversation_id: null } });
  });

  // Default agent lists
  await page.route('**/api/agents/list*', async (route) => {
    const url = route.request().url();
    const isABScope = url.includes('scope=ab');
    await route.fulfill({ status: 200, json: isABScope ? mockData.abAgentsList : mockData.agentsList });
  });

  await page.route('**/api/agents/template*', async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        name: 'New A/B Agent',
        tools: [
          { name: 'search_docs', description: 'Search indexed documents' },
          { name: 'fetch_ticket', description: 'Fetch ticket details' },
        ],
        template: '---\nname: New A/B Agent\nab_only: true\ntools:\n  - search_docs\n  - fetch_ticket\n---\n\nYou are an A/B-only agent.',
      },
    });
  });

  await page.route('**/api/agents', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON();
    const content = String(body.content || '');
    const match = content.match(/^name:\s*(.+)$/m);
    const name = match ? match[1].trim() : 'New A/B Agent';
    const filename = `${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'new-ab-agent'}.md`;
    await route.fulfill({
      status: 200,
      json: { success: true, name, filename, scope: body.scope || 'default' },
    });
  });

  // Default A/B pool: non-admin, disabled.
  // Tests that need admin behavior should call setupABAdminMocks AFTER this.
  await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
    await route.fulfill({ status: 200, json: { enabled: false, is_admin: false, can_manage: false } });
  });
}

export async function setupStreamMock(page: Page, response: string, delay = 0) {
  await page.route('**/api/get_chat_response_stream', async (route) => {
    if (delay > 0) {
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    await route.fulfill({ status: 200, contentType: 'text/plain', body: response });
  });
}

/**
 * Set up route mocks that make the page behave as an admin with an active A/B pool.
 * Must be called BEFORE page.goto('/chat') so the init API calls get intercepted.
 */
export async function setupABAdminMocks(page: Page) {
  // Register AFTER setupBasicMocks — Playwright processes routes LIFO,
  // so this handler runs first and the default non-admin one never fires.
  await page.route('**/api/agents/list*', async (route) => {
    const url = route.request().url();
    const isABScope = url.includes('scope=ab');
    await route.fulfill({ status: 200, json: isABScope ? mockData.abAgentsList : mockData.agentsList });
  });

  await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
    await route.fulfill({ status: 200, json: mockData.abPoolAdmin });
  });
}

/**
 * Set up route mocks for an admin who has NOT yet enabled a pool.
 */
export async function setupABAdminInactiveMocks(page: Page) {
  await page.route('**/api/agents/list*', async (route) => {
    const url = route.request().url();
    const isABScope = url.includes('scope=ab');
    await route.fulfill({ status: 200, json: isABScope ? mockData.abAgentsList : mockData.agentsList });
  });

  await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
    await route.fulfill({ status: 200, json: mockData.abPoolAdminInactive });
  });
}

/**
 * Build an NDJSON body for a mock A/B comparison stream.
 */
export function createABStreamResponse(options: {
  armAContent?: string;
  armBContent?: string;
  comparisonId?: number;
  conversationId?: number;
  armAVariant?: string;
  armBVariant?: string;
  armAMessageId?: number;
  armBMessageId?: number;
  disclosureMode?: string;
} = {}) {
  const {
    armAContent = 'Response from arm A',
    armBContent = 'Response from arm B',
    comparisonId = 42,
    conversationId = 1,
    armAVariant = 'Baseline',
    armBVariant = 'Poet',
    armAMessageId = 101,
    armBMessageId = 102,
    disclosureMode = 'post_vote_reveal',
  } = options;

  const events = [
    { type: 'meta', event: 'stream_started' },
    {
      type: 'ab_arms',
      arm_a_name: armAVariant,
      arm_b_name: armBVariant,
      disclosure_mode: disclosureMode,
    },
    { arm: 'a', type: 'chunk', content: armAContent },
    { arm: 'b', type: 'chunk', content: armBContent },
    { arm: 'a', type: 'final', response: armAContent, model_used: 'openai/gpt-4o' },
    { arm: 'b', type: 'final', response: armBContent, model_used: 'anthropic/claude-3.5-sonnet' },
    {
      type: 'ab_meta',
      comparison_id: comparisonId,
      conversation_id: conversationId,
      arm_a_message_id: armAMessageId,
      arm_b_message_id: armBMessageId,
      arm_a_variant: armAVariant,
      arm_b_variant: armBVariant,
      disclosure_mode: disclosureMode,
    },
  ];

  return events.map(e => JSON.stringify(e)).join('\n') + '\n';
}

export async function enableABMode(page: Page) {
  // Legacy helper — kept for backwards compat but now just ensures the pool
  // state is wired correctly via evaluate (after page has loaded).
  await page.evaluate(() => {
    // @ts-ignore – Chat is a global in the app
    if (typeof Chat !== 'undefined') {
      Chat.state.abPool = {
        enabled: true,
        champion: 'Baseline',
        variants: ['Baseline', 'Poet'],
        max_pending_per_conversation: 1,
      };
    }
  });
}

export async function clearStorage(page: Page) {
  // Note: This must be called AFTER page.goto() - the page needs to be at a URL first
  await page.evaluate(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
}

// =============================================================================
// Custom Test Fixture
// =============================================================================

type ChatFixtures = {
  chatPage: Page;
};

export const test = base.extend<ChatFixtures>({
  chatPage: async ({ page }, use) => {
    await setupBasicMocks(page);
    await page.goto('/chat');
    await use(page);
  },
});

export { expect };
