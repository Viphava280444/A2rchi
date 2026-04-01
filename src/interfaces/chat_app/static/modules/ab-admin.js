(function () {
  const ENDPOINTS = {
    agents: '/api/agents/list?scope=ab',
    pool: '/api/ab/pool',
    saveSettings: '/api/ab/pool/settings/set',
    saveVariants: '/api/ab/pool/variants/set',
    disable: '/api/ab/pool/disable',
    metrics: '/api/ab/metrics',
    providers: '/api/providers',
    agentTemplate: '/api/agents/template?scope=ab',
    saveAgent: '/api/agents',
  };

  const SETTINGS_DRAFT_STORAGE_KEY = 'archi_ab_admin_settings_draft_v1';
  const VARIANTS_DRAFT_STORAGE_KEY = 'archi_ab_admin_variants_draft_v1';

  const state = {
    canManage: document.body.dataset.canManageAbTesting === 'true',
    canViewMetrics: document.body.dataset.canViewAbMetrics === 'true',
    agents: [],
    providers: [],
    defaults: {
      provider: '',
      model: '',
      recursion_limit: 50,
      num_documents_to_retrieve: 5,
    },
    warnings: [],
    enabledRequested: false,
    dirty: {
      settings: false,
      variants: false,
    },
    persisted: {
      enabled: false,
      champion: '',
      sample_rate: 1,
      disclosure_mode: 'post_vote_reveal',
      default_trace_mode: 'minimal',
      max_pending_per_conversation: 1,
      variants: [],
    },
    settingsForm: {
      champion: '',
      sample_rate: 1,
      disclosure_mode: 'post_vote_reveal',
      default_trace_mode: 'minimal',
      max_pending_per_conversation: 1,
    },
    variantForm: [],
    modal: {
      mode: 'create',
      targetIndex: null,
      sourceName: '',
      sourceFilename: '',
      tools: [],
      sourceTemplate: '',
    },
    metrics: [],
  };

  const els = {
    status: document.getElementById('ab-admin-status'),
    sampleRate: document.getElementById('ab-admin-sample-rate'),
    disclosureMode: document.getElementById('ab-admin-disclosure-mode'),
    traceMode: document.getElementById('ab-admin-trace-mode'),
    maxPending: document.getElementById('ab-admin-max-pending'),
    champion: document.getElementById('ab-admin-champion'),
    save: document.getElementById('ab-admin-save'),
    disable: document.getElementById('ab-admin-disable'),
    addVariant: document.getElementById('ab-admin-add-variant'),
    variantSave: document.getElementById('ab-admin-variant-save'),
    variantList: document.getElementById('ab-admin-variant-list'),
    message: document.getElementById('ab-admin-message'),
    variantMessage: document.getElementById('ab-admin-variant-message'),
    warnings: document.getElementById('ab-admin-warnings'),
    readOnly: document.getElementById('ab-admin-readonly'),
    modal: document.getElementById('ab-agent-modal'),
    modalClose: document.getElementById('ab-agent-modal-close'),
    modalCancel: document.getElementById('ab-agent-cancel'),
    modalSave: document.getElementById('ab-agent-save'),
    modalTitle: document.getElementById('ab-agent-modal-title'),
    modalDescription: document.getElementById('ab-agent-modal-description'),
    modalNameLabel: document.getElementById('ab-agent-name-label'),
    modalName: document.getElementById('ab-agent-name'),
    modalPrompt: document.getElementById('ab-agent-prompt'),
    modalTools: document.getElementById('ab-agent-tools-list'),
    modalMessage: document.getElementById('ab-agent-message'),
    metricsPanel: document.getElementById('ab-metrics-panel'),
    metricsList: document.getElementById('ab-admin-metrics-list'),
    metricsMessage: document.getElementById('ab-admin-metrics-message'),
  };

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 401) {
      window.location.href = '/login';
      throw new Error('Authentication required');
    }
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    if (!response.ok) {
      throw new Error(data?.error || data?.message || `Request failed (${response.status})`);
    }
    return data;
  }

  function setMessage(text, type = '') {
    if (!els.message) return;
    els.message.textContent = text || '';
    els.message.className = type ? `ab-pool-message ${type}` : 'ab-pool-message';
  }

  function setModalMessage(text, type = '') {
    if (!els.modalMessage) return;
    els.modalMessage.textContent = text || '';
    els.modalMessage.className = type ? `ab-pool-message ${type}` : 'ab-pool-message';
  }

  function setVariantMessage(text, type = '') {
    if (!els.variantMessage) return;
    els.variantMessage.textContent = text || '';
    els.variantMessage.className = type ? `ab-pool-message ${type}` : 'ab-pool-message';
  }

  function providerCatalog() {
    return state.providers.filter((provider) => provider);
  }

  function getAgentByFilename(filename) {
    const target = String(filename || '').trim();
    return state.agents.find((agent) => String(agent.filename || '').trim() === target) || null;
  }

  function getProviderConfig(providerType) {
    return providerCatalog().find((provider) => provider.type === providerType) || null;
  }

  function getDefaultModelForProvider(providerType) {
    const provider = getProviderConfig(providerType);
    return provider?.default_model || '';
  }

  function normalizeVariant(variant = {}) {
    return {
      label: String(variant.label || '').trim(),
      agent_spec: String(variant.agent_spec || '').trim(),
      provider: String(variant.provider || '').trim(),
      model: String(variant.model || '').trim(),
      recursion_limit: variant.recursion_limit ?? '',
      num_documents_to_retrieve: variant.num_documents_to_retrieve ?? '',
      _custom_model: false,
    };
  }

  function normalizeDefaults(defaults = {}) {
    return {
      provider: String(defaults.provider || '').trim(),
      model: String(defaults.model || '').trim(),
      recursion_limit: Number(defaults.recursion_limit ?? 50),
      num_documents_to_retrieve: Number(defaults.num_documents_to_retrieve ?? 5),
    };
  }

  function normalizePool(pool = {}) {
    let details = Array.isArray(pool.variant_details) ? pool.variant_details : [];
    if (!details.length && Array.isArray(pool.variants) && pool.variants.some((entry) => entry && typeof entry === 'object')) {
      details = pool.variants;
    }
    return {
      enabled: pool.enabled === true,
      champion: String(pool.champion || '').trim(),
      sample_rate: Number(pool.sample_rate ?? 1),
      disclosure_mode: pool.disclosure_mode || 'post_vote_reveal',
      default_trace_mode: pool.default_trace_mode || 'minimal',
      max_pending_per_conversation: Number(pool.max_pending_per_conversation ?? 1),
      variants: details.map(normalizeVariant),
    };
  }

  function cloneData(data) {
    return JSON.parse(JSON.stringify(data));
  }

  function extractSettings(pool = {}) {
    return {
      champion: String(pool.champion || '').trim(),
      sample_rate: Number(pool.sample_rate ?? 1),
      disclosure_mode: pool.disclosure_mode || 'post_vote_reveal',
      default_trace_mode: pool.default_trace_mode || 'minimal',
      max_pending_per_conversation: Number(pool.max_pending_per_conversation ?? 1),
    };
  }

  function saveSettingsDraft() {
    try {
      localStorage.setItem(SETTINGS_DRAFT_STORAGE_KEY, JSON.stringify({
        settings: state.settingsForm,
        timestamp: Date.now(),
      }));
    } catch (error) {
      console.warn('Failed to persist A/B settings draft:', error);
    }
  }

  function saveVariantsDraft() {
    try {
      localStorage.setItem(VARIANTS_DRAFT_STORAGE_KEY, JSON.stringify({
        variants: state.variantForm,
        timestamp: Date.now(),
      }));
    } catch (error) {
      console.warn('Failed to persist A/B variants draft:', error);
    }
  }

  function clearSettingsDraft() {
    try {
      localStorage.removeItem(SETTINGS_DRAFT_STORAGE_KEY);
    } catch (error) {
      console.warn('Failed to clear A/B settings draft:', error);
    }
  }

  function clearVariantsDraft() {
    try {
      localStorage.removeItem(VARIANTS_DRAFT_STORAGE_KEY);
    } catch (error) {
      console.warn('Failed to clear A/B variants draft:', error);
    }
  }

  function loadSettingsDraft() {
    try {
      const raw = localStorage.getItem(SETTINGS_DRAFT_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || !parsed.settings) return null;
      return parsed.settings;
    } catch (error) {
      console.warn('Failed to load A/B settings draft:', error);
      return null;
    }
  }

  function loadVariantsDraft() {
    try {
      const raw = localStorage.getItem(VARIANTS_DRAFT_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.variants)) return null;
      return parsed.variants;
    } catch (error) {
      console.warn('Failed to load A/B variants draft:', error);
      return null;
    }
  }

  function setSettingsDirty(dirty) {
    state.dirty.settings = dirty;
    if (dirty) {
      saveSettingsDraft();
    } else {
      clearSettingsDraft();
    }
  }

  function setVariantsDirty(dirty) {
    state.dirty.variants = dirty;
    if (dirty) {
      saveVariantsDraft();
    } else {
      clearVariantsDraft();
    }
  }

  function renderWarnings() {
    if (!els.warnings) return;
    if (!state.warnings.length) {
      els.warnings.style.display = 'none';
      els.warnings.innerHTML = '';
      return;
    }
    els.warnings.style.display = '';
    els.warnings.innerHTML = state.warnings.map((warning) => (
      `<div class="ab-admin-warning-item">${escapeHtml(warning)}</div>`
    )).join('');
  }

  function uniqueLabel(baseLabel) {
    const used = new Set(state.variantForm.map((variant) => variant.label));
    let candidate = baseLabel || 'Variant';
    if (!used.has(candidate)) return candidate;
    let index = 2;
    while (used.has(`${candidate} ${index}`)) {
      index += 1;
    }
    return `${candidate} ${index}`;
  }

  function currentLabels() {
    return state.variantForm
      .map((variant) => String(variant.label || '').trim())
      .filter(Boolean);
  }

  function persistedLabels() {
    return (state.persisted.variants || [])
      .map((variant) => String(variant.label || '').trim())
      .filter(Boolean);
  }

  function syncChampionOptions() {
    if (!els.champion) return;
    const labels = persistedLabels();
    const champion = labels.includes(state.settingsForm.champion) ? state.settingsForm.champion : (labels[0] || '');
    state.settingsForm.champion = champion;
    els.champion.innerHTML = labels.map((label) => (
      `<option value="${escapeHtml(label)}"${label === champion ? ' selected' : ''}>${escapeHtml(label)}</option>`
    )).join('');
    els.champion.disabled = !state.canManage || labels.length === 0;
  }

  function validateSettingsForm() {
    const labels = persistedLabels();
    if (labels.length < 2) {
      return { valid: false, message: 'Save at least 2 variants before saving experiment settings.' };
    }
    if (!labels.includes(state.settingsForm.champion)) {
      return { valid: false, message: 'Champion must match one of the saved variants.' };
    }
    if (!Number.isFinite(state.settingsForm.sample_rate) || state.settingsForm.sample_rate < 0 || state.settingsForm.sample_rate > 1) {
      return { valid: false, message: 'Sampling rate must be between 0 and 1.' };
    }
    if (!Number.isInteger(state.settingsForm.max_pending_per_conversation) || state.settingsForm.max_pending_per_conversation < 1) {
      return { valid: false, message: 'Max pending per conversation must be at least 1.' };
    }
    return { valid: true, message: '' };
  }

  function validateVariantsForm() {
    const labels = currentLabels();
    if (state.variantForm.length < 2) {
      return { valid: false, message: 'Add at least 2 variants to save the variants list.' };
    }
    if (labels.length !== state.variantForm.length) {
      return { valid: false, message: 'Every variant needs a non-empty label.' };
    }
    if (new Set(labels).size !== labels.length) {
      return { valid: false, message: 'Variant labels must be unique.' };
    }
    if (state.variantForm.some((variant) => !String(variant.agent_spec || '').trim())) {
      return { valid: false, message: 'Every variant needs an A/B agent spec from the experiment catalog.' };
    }
    for (const variant of state.variantForm) {
      const providerType = String(variant.provider || '').trim();
      const modelValue = String(variant.model || '').trim();
      if (providerType && !getProviderConfig(providerType)) {
        return { valid: false, message: `Variant '${variant.label || 'untitled'}' uses an unknown provider.` };
      }
      if (providerType && variant._custom_model === true && !modelValue) {
        return { valid: false, message: `Variant '${variant.label || 'untitled'}' needs a custom model value.` };
      }
    }
    return { valid: true, message: '' };
  }

  function updateSettingsSaveState() {
    if (!state.canManage) return;
    const validation = validateSettingsForm();
    if (els.save) {
      els.save.disabled = !validation.valid;
    }
    if (!validation.valid) {
      setMessage(validation.message, 'error');
    } else if (els.message?.classList.contains('error')) {
      setMessage('');
    }
  }

  function updateVariantSaveState() {
    if (!state.canManage) return;
    const validation = validateVariantsForm();
    if (els.variantSave) {
      els.variantSave.disabled = !validation.valid;
    }
    if (!validation.valid) {
      setVariantMessage(validation.message, 'error');
    } else if (els.variantMessage?.classList.contains('error')) {
      setVariantMessage('');
    }
  }

  function providerOptionsHtml(selectedProvider) {
    const inherited = state.defaults.provider || 'deployment default';
    const options = [
      `<option value="">Use default (${escapeHtml(inherited)})</option>`,
    ];
    for (const provider of providerCatalog()) {
      options.push(
        `<option value="${escapeHtml(provider.type)}"${provider.type === selectedProvider ? ' selected' : ''}>${escapeHtml(provider.display_name || provider.type)}${provider.enabled === false ? ' (disabled)' : ''}</option>`
      );
    }
    return options.join('');
  }

  function buildModelSelectState(variant) {
    const providerType = String(variant.provider || '').trim();
    if (!providerType) {
      return {
        disabled: true,
        selectedValue: '',
        customVisible: false,
        customValue: '',
        options: `<option value="">Use default (${escapeHtml(state.defaults.model || 'deployment default')})</option>`,
      };
    }
    const provider = getProviderConfig(providerType);
    const models = Array.isArray(provider?.models) ? provider.models : [];
    const modelValue = String(variant.model || '').trim();
    const known = models.some((model) => model.id === modelValue);
    const usingCustom = variant._custom_model === true || (Boolean(modelValue) && !known);
    const providerDefault = provider?.default_model || state.defaults.model || 'provider default';
    const options = [
      `<option value="">Use provider default (${escapeHtml(providerDefault)})</option>`,
      ...models.map((model) => (
        `<option value="${escapeHtml(model.id)}"${model.id === modelValue ? ' selected' : ''}>${escapeHtml(model.display_name || model.name || model.id)}</option>`
      )),
      `<option value="__custom__"${usingCustom ? ' selected' : ''}>Custom model…</option>`,
    ].join('');
    return {
      disabled: false,
      selectedValue: usingCustom ? '__custom__' : modelValue,
      customVisible: usingCustom,
      customValue: usingCustom ? modelValue : '',
      options,
    };
  }

  function agentOptionsHtml(selectedFilename) {
    const options = ['<option value="">Select an A/B agent spec</option>'];
    for (const agent of state.agents) {
      options.push(
        `<option value="${escapeHtml(agent.filename)}"${agent.filename === selectedFilename ? ' selected' : ''}>${escapeHtml(agent.name)} (${escapeHtml(agent.filename)})</option>`
      );
    }
    if (state.canManage) {
      options.push('<option value="__create_new__">Add new A/B agent…</option>');
    }
    return options.join('');
  }

  function renderVariants() {
    if (!els.variantList) return;
    if (!state.variantForm.length) {
      els.variantList.innerHTML = `
        <div class="ab-admin-empty-state">
          <strong>No variants configured.</strong>
          <span>Add at least two variants to enable champion/challenger comparisons.</span>
        </div>
      `;
      updateVariantSaveState();
      return;
    }

    els.variantList.innerHTML = state.variantForm.map((variant, index) => {
      const modelState = buildModelSelectState(variant);
      const disabledAttr = state.canManage ? '' : 'disabled';
      return `
        <article class="ab-variant-card" data-index="${index}">
          <div class="ab-variant-card-header">
            <div>
              <h3>Variant ${index + 1}</h3>
              <p>Configure the experiment label, A/B agent spec, and optional runtime overrides.</p>
            </div>
            <button class="ab-variant-remove" type="button" data-remove="${index}" ${disabledAttr}>Remove</button>
          </div>
          <div class="ab-variant-grid">
            <label class="ab-admin-field">
              <span>Label</span>
              <input type="text" data-field="label" value="${escapeHtml(variant.label)}" placeholder="baseline" ${disabledAttr}>
            </label>
              <div class="ab-admin-field">
              <span>Agent Spec</span>
              <div class="ab-agent-select-row">
                <select data-field="agent_spec" ${disabledAttr}>
                  ${agentOptionsHtml(variant.agent_spec)}
                </select>
                <button
                  class="ab-variant-inline-btn"
                  type="button"
                  data-edit-agent="${index}"
                  ${!state.canManage || !String(variant.agent_spec || '').trim() ? 'disabled' : ''}
                >Edit</button>
              </div>
            </div>
            <label class="ab-admin-field">
              <span>Provider Override</span>
              <select data-field="provider" ${disabledAttr}>
                ${providerOptionsHtml(variant.provider)}
              </select>
            </label>
            <div class="ab-admin-field">
              <span>Model Override</span>
              <div class="ab-model-control">
                <select data-field="model_select" ${state.canManage && !modelState.disabled ? '' : 'disabled'}>
                  ${modelState.options}
                </select>
                <input
                  type="text"
                  data-field="model_custom"
                  value="${escapeHtml(modelState.customValue)}"
                  placeholder="${escapeHtml((getDefaultModelForProvider(variant.provider) || state.defaults.model || 'custom-model') + ' (default)')}"
                  style="${modelState.customVisible ? '' : 'display:none;'}"
                  ${disabledAttr}
                >
              </div>
            </div>
            <label class="ab-admin-field">
              <span>Recursion Limit</span>
              <input
                type="number"
                data-field="recursion_limit"
                min="1"
                step="1"
                value="${escapeHtml(variant.recursion_limit)}"
                placeholder="${escapeHtml(String(state.defaults.recursion_limit) + ' (default)')}"
                ${disabledAttr}
              >
            </label>
            <label class="ab-admin-field">
              <span>Document Retrieval Override</span>
              <input
                type="number"
                data-field="num_documents_to_retrieve"
                min="1"
                step="1"
                value="${escapeHtml(variant.num_documents_to_retrieve)}"
                placeholder="${escapeHtml(String(state.defaults.num_documents_to_retrieve) + ' (default)')}"
                ${disabledAttr}
              >
            </label>
          </div>
        </article>
      `;
    }).join('');

    updateVariantSaveState();
  }

  function renderForm() {
    if (els.status) {
      els.status.textContent = state.persisted.enabled ? 'Active' : 'Inactive';
      els.status.classList.toggle('active', state.persisted.enabled);
    }
    if (els.sampleRate) els.sampleRate.value = String(state.settingsForm.sample_rate ?? 1);
    if (els.disclosureMode) els.disclosureMode.value = state.settingsForm.disclosure_mode || 'post_vote_reveal';
    if (els.traceMode) els.traceMode.value = state.settingsForm.default_trace_mode || 'minimal';
    if (els.maxPending) els.maxPending.value = String(state.settingsForm.max_pending_per_conversation ?? 1);
    if (els.disable) els.disable.style.display = state.enabledRequested ? '' : 'none';
    if (els.readOnly) els.readOnly.style.display = state.canManage ? 'none' : '';
    if (els.sampleRate) els.sampleRate.disabled = !state.canManage;
    if (els.disclosureMode) els.disclosureMode.disabled = !state.canManage;
    if (els.traceMode) els.traceMode.disabled = !state.canManage;
    if (els.maxPending) els.maxPending.disabled = !state.canManage;
    if (els.save) els.save.disabled = !state.canManage;
    if (els.disable) els.disable.disabled = !state.canManage;
    if (els.variantSave) els.variantSave.disabled = !state.canManage;
    if (els.addVariant) els.addVariant.disabled = !state.canManage;
    renderWarnings();
    syncChampionOptions();
    renderVariants();
    if (state.canManage) {
      updateSettingsSaveState();
    }
  }

  function applyPoolResponseMeta(poolResponse = {}) {
    state.defaults = normalizeDefaults(poolResponse.defaults || state.defaults || {});
    state.warnings = Array.isArray(poolResponse.warnings) ? poolResponse.warnings : [];
    state.enabledRequested = poolResponse.enabled_requested === true;
    state.canManage = poolResponse.can_manage === true || state.canManage === true;
    state.canViewMetrics = poolResponse.can_view_metrics === true || state.canViewMetrics === true;
  }

  async function loadAgents() {
    const agentsResponse = await fetchJson(ENDPOINTS.agents);
    state.agents = Array.isArray(agentsResponse.agents) ? agentsResponse.agents : [];
  }

  function renderMetrics() {
    if (!els.metricsPanel || !els.metricsList) return;
    if (!state.canViewMetrics) {
      els.metricsPanel.style.display = 'none';
      return;
    }
    els.metricsPanel.style.display = '';
    if (!state.metrics.length) {
      els.metricsList.innerHTML = `
        <div class="ab-admin-empty-state">
          <strong>No comparison data yet.</strong>
          <span>Metrics will appear after participants submit A/B votes.</span>
        </div>
      `;
      return;
    }

    els.metricsList.innerHTML = `
      <table class="ab-admin-metrics-table">
        <thead>
          <tr>
            <th>Variant</th>
            <th>Wins</th>
            <th>Losses</th>
            <th>Ties</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          ${state.metrics.map((metric) => `
            <tr>
              <td>${escapeHtml(metric.variant_name || '')}</td>
              <td>${escapeHtml(metric.wins ?? 0)}</td>
              <td>${escapeHtml(metric.losses ?? 0)}</td>
              <td>${escapeHtml(metric.ties ?? 0)}</td>
              <td>${escapeHtml(metric.total_comparisons ?? 0)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function applyPersistedPool(pool = {}, options = {}) {
    const {
      useSettingsDraft = true,
      useVariantsDraft = true,
    } = options;
    state.persisted = normalizePool(pool);

    const settingsDraft = useSettingsDraft ? loadSettingsDraft() : null;
    if (settingsDraft) {
      state.settingsForm = extractSettings(settingsDraft);
      state.dirty.settings = true;
      setMessage('Restored unsaved experiment settings.', 'success');
    } else {
      state.settingsForm = extractSettings(state.persisted);
      state.dirty.settings = false;
      setMessage('');
    }

    const variantsDraft = useVariantsDraft ? loadVariantsDraft() : null;
    if (variantsDraft) {
      state.variantForm = variantsDraft.map(normalizeVariant);
      state.dirty.variants = true;
      setVariantMessage('Restored unsaved variant changes.', 'success');
    } else {
      state.variantForm = (state.persisted.variants || []).map(normalizeVariant);
      state.dirty.variants = false;
      setVariantMessage('');
    }
  }

  async function loadState() {
    const requests = [
      fetchJson(ENDPOINTS.pool),
      fetchJson(ENDPOINTS.providers),
    ];
    if (state.canViewMetrics) {
      requests.push(fetchJson(ENDPOINTS.metrics));
    }
    const [poolResponse, providersResponse, metricsResponse] = await Promise.all(requests);
    await loadAgents();
    state.providers = Array.isArray(providersResponse.providers) ? providersResponse.providers : [];
    state.metrics = Array.isArray(metricsResponse?.metrics) ? metricsResponse.metrics : [];
    applyPoolResponseMeta(poolResponse);
    applyPersistedPool(poolResponse);
    renderForm();
    renderMetrics();
  }

  function readOptionalInt(value) {
    const trimmed = String(value ?? '').trim();
    if (!trimmed) return null;
    const parsed = Number.parseInt(trimmed, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function collectSettingsPayload() {
    return {
      champion: state.settingsForm.champion,
      sample_rate: state.settingsForm.sample_rate,
      disclosure_mode: state.settingsForm.disclosure_mode,
      default_trace_mode: state.settingsForm.default_trace_mode,
      max_pending_per_conversation: state.settingsForm.max_pending_per_conversation,
    };
  }

  function collectVariantPayload() {
    return {
      variants: state.variantForm.map((variant) => {
        const provider = String(variant.provider || '').trim();
        let model = String(variant.model || '').trim();
        if (provider && !model) {
          model = getDefaultModelForProvider(provider);
        }
        return {
          label: String(variant.label || '').trim(),
          agent_spec: String(variant.agent_spec || '').trim(),
          provider: provider || null,
          model: model || null,
          recursion_limit: readOptionalInt(variant.recursion_limit),
          num_documents_to_retrieve: readOptionalInt(variant.num_documents_to_retrieve),
        };
      }),
    };
  }

  function addVariant() {
    if (!state.canManage) return;
    const firstAgent = state.agents[0] || {};
    state.variantForm.push({
      label: uniqueLabel(firstAgent.name || 'Variant'),
      agent_spec: firstAgent.filename || '',
      provider: '',
      model: '',
      recursion_limit: '',
      num_documents_to_retrieve: '',
      _custom_model: false,
    });
    setVariantsDirty(true);
    renderVariants();
  }

  async function saveSettings() {
    if (!state.canManage) return;
    const validation = validateSettingsForm();
    if (!validation.valid) {
      setMessage(validation.message, 'error');
      return;
    }
    els.save.disabled = true;
    els.save.textContent = 'Saving…';
    try {
      const poolResponse = await fetchJson(ENDPOINTS.saveSettings, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectSettingsPayload()),
      });
      setSettingsDirty(false);
      applyPoolResponseMeta(poolResponse);
      applyPersistedPool(poolResponse, {
        useSettingsDraft: false,
        useVariantsDraft: state.dirty.variants,
      });
      renderForm();
      setMessage('Experiment settings saved.', 'success');
    } catch (error) {
      setMessage(error.message || 'Failed to save experiment settings.', 'error');
    } finally {
      els.save.textContent = 'Save Configuration';
      updateSettingsSaveState();
    }
  }

  async function saveVariants() {
    if (!state.canManage) return;
    const validation = validateVariantsForm();
    if (!validation.valid) {
      setVariantMessage(validation.message, 'error');
      return;
    }
    if (els.variantSave) {
      els.variantSave.disabled = true;
      els.variantSave.textContent = 'Saving…';
    }
    try {
      const poolResponse = await fetchJson(ENDPOINTS.saveVariants, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectVariantPayload()),
      });
      setVariantsDirty(false);
      applyPoolResponseMeta(poolResponse);
      applyPersistedPool(poolResponse, {
        useSettingsDraft: state.dirty.settings,
        useVariantsDraft: false,
      });
      renderForm();
      setVariantMessage('Variants saved.', 'success');
    } catch (error) {
      setVariantMessage(error.message || 'Failed to save variants.', 'error');
    } finally {
      if (els.variantSave) {
        els.variantSave.textContent = 'Save Variants';
      }
      updateVariantSaveState();
    }
  }

  async function disablePool() {
    if (!state.canManage) return;
    els.disable.disabled = true;
    try {
      const poolResponse = await fetchJson(ENDPOINTS.disable, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      setSettingsDirty(false);
      applyPoolResponseMeta(poolResponse);
      applyPersistedPool(poolResponse, {
        useSettingsDraft: false,
        useVariantsDraft: state.dirty.variants,
      });
      renderForm();
      setMessage('A/B testing disabled. Variant settings remain available for later reactivation.', 'success');
    } catch (error) {
      setMessage(error.message || 'Failed to disable A/B testing.', 'error');
    } finally {
      els.disable.disabled = false;
    }
  }

  function serialiseAgentSpec(name, tools, prompt) {
    let yaml = `---\nname: ${name}\nab_only: true\n`;
    if (tools.length) {
      yaml += 'tools:\n';
      for (const tool of tools) yaml += `  - ${tool}\n`;
    }
    yaml += '---\n\n';
    return yaml + String(prompt || '').trim();
  }

  function configureModalForMode(mode, options = {}) {
    state.modal.mode = mode === 'edit' ? 'edit' : 'create';
    state.modal.targetIndex = Number.isInteger(options.targetIndex) ? options.targetIndex : null;
    state.modal.sourceName = String(options.sourceName || '').trim();
    state.modal.sourceFilename = String(options.sourceFilename || '').trim();
    if (els.modalTitle) {
      els.modalTitle.textContent = state.modal.mode === 'edit' ? 'Edit A/B Agent' : 'New A/B Agent';
    }
    if (els.modalDescription) {
      els.modalDescription.textContent = state.modal.mode === 'edit'
        ? 'Editing creates a new immutable A/B agent spec and switches the current variant to that new copy.'
        : 'Create an agent spec that is only available to A/B experiments.';
    }
    if (els.modalNameLabel) {
      els.modalNameLabel.textContent = state.modal.mode === 'edit' ? 'Source Agent Name' : 'Agent Name';
    }
    if (els.modalName) {
      els.modalName.disabled = state.modal.mode === 'edit';
      els.modalName.placeholder = state.modal.mode === 'edit' ? 'Immutable copy will be named automatically' : 'A/B Candidate';
    }
    if (els.modalSave) {
      els.modalSave.textContent = state.modal.mode === 'edit' ? 'Save Edited Copy' : 'Create Agent';
    }
  }

  async function openCreateAgentModal(targetIndex = null) {
    if (!state.canManage) return;
    configureModalForMode('create', { targetIndex });
    setModalMessage('');
    if (els.modalName) els.modalName.value = '';
    if (els.modalPrompt) els.modalPrompt.value = '';
    if (els.modalTools) els.modalTools.innerHTML = '<div class="ab-admin-empty-state">Loading template…</div>';
    if (els.modal) els.modal.style.display = '';
    try {
      const template = await fetchJson(`${ENDPOINTS.agentTemplate}&name=${encodeURIComponent('New A/B Agent')}`);
      state.modal.tools = Array.isArray(template.tools) ? template.tools : [];
      state.modal.sourceTemplate = String(template.template || '');
      if (els.modalPrompt) {
        const match = state.modal.sourceTemplate.match(/^---\s*\n[\s\S]*?\n---\s*\n?([\s\S]*)$/);
        els.modalPrompt.value = (match ? match[1] : '').trim();
      }
      if (els.modalTools) {
        els.modalTools.innerHTML = state.modal.tools.map((tool) => `
          <label class="ab-agent-tool-item">
            <input type="checkbox" value="${escapeHtml(tool.name)}" checked>
            <span>${escapeHtml(tool.name)}</span>
            <small>${escapeHtml(tool.description || '')}</small>
          </label>
        `).join('');
      }
    } catch (error) {
      setModalMessage(error.message || 'Unable to load A/B agent template.', 'error');
    }
  }

  async function openEditAgentModal(targetIndex) {
    if (!state.canManage || !Number.isInteger(targetIndex) || !state.variantForm[targetIndex]) return;
    const variant = state.variantForm[targetIndex];
    const selectedAgent = getAgentByFilename(variant.agent_spec);
    if (!selectedAgent) {
      setVariantMessage('Select an A/B agent before editing it.', 'error');
      return;
    }

    configureModalForMode('edit', {
      targetIndex,
      sourceName: selectedAgent.name,
      sourceFilename: selectedAgent.filename,
    });
    setModalMessage('');
    if (els.modalName) els.modalName.value = selectedAgent.name;
    if (els.modalPrompt) els.modalPrompt.value = '';
    if (els.modalTools) els.modalTools.innerHTML = '<div class="ab-admin-empty-state">Loading agent…</div>';
    if (els.modal) els.modal.style.display = '';

    try {
      const [template, spec] = await Promise.all([
        fetchJson(`${ENDPOINTS.agentTemplate}&name=${encodeURIComponent(selectedAgent.name)}`),
        fetchJson(`/api/agents/spec?scope=ab&filename=${encodeURIComponent(selectedAgent.filename)}`),
      ]);
      state.modal.tools = Array.isArray(template.tools) ? template.tools : [];
      state.modal.sourceTemplate = String(template.template || '');
      if (els.modalPrompt) {
        els.modalPrompt.value = String(spec.prompt || '').trim();
      }
      const enabledTools = new Set(Array.isArray(spec.tools) ? spec.tools : []);
      if (els.modalTools) {
        els.modalTools.innerHTML = state.modal.tools.map((tool) => `
          <label class="ab-agent-tool-item">
            <input type="checkbox" value="${escapeHtml(tool.name)}" ${enabledTools.has(tool.name) ? 'checked' : ''}>
            <span>${escapeHtml(tool.name)}</span>
            <small>${escapeHtml(tool.description || '')}</small>
          </label>
        `).join('');
      }
    } catch (error) {
      setModalMessage(error.message || 'Unable to load the selected A/B agent.', 'error');
    }
  }

  function closeCreateAgentModal() {
    if (els.modal) els.modal.style.display = 'none';
    configureModalForMode('create', { targetIndex: null });
    state.modal.targetIndex = null;
    setModalMessage('');
  }

  async function saveNewAgent() {
    if (!state.canManage) return;
    const name = String(els.modalName?.value || '').trim();
    const prompt = String(els.modalPrompt?.value || '').trim();
    const tools = [...(els.modalTools?.querySelectorAll('input[type="checkbox"]:checked') || [])].map((checkbox) => checkbox.value);
    const isEdit = state.modal.mode === 'edit';
    if (!isEdit && !name) {
      setModalMessage('Agent name is required.', 'error');
      els.modalName?.focus();
      return;
    }
    if (!prompt) {
      setModalMessage('Prompt is required.', 'error');
      els.modalPrompt?.focus();
      return;
    }

    els.modalSave.disabled = true;
    els.modalSave.textContent = isEdit ? 'Saving…' : 'Creating…';
    try {
      const content = serialiseAgentSpec(isEdit ? state.modal.sourceName : name, tools, prompt);
      const response = await fetchJson(ENDPOINTS.saveAgent, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope: 'ab',
          mode: isEdit ? 'edit' : 'create',
          existing_name: isEdit ? state.modal.sourceName : null,
          content,
        }),
      });
      await loadAgents();
      if (Number.isInteger(state.modal.targetIndex) && state.variantForm[state.modal.targetIndex]) {
        state.variantForm[state.modal.targetIndex].agent_spec = response.filename;
        if (!state.variantForm[state.modal.targetIndex].label) {
          state.variantForm[state.modal.targetIndex].label = uniqueLabel(response.name);
        }
        setVariantsDirty(true);
        renderVariants();
      }
      closeCreateAgentModal();
      setVariantMessage(
        isEdit
          ? `Saved edited copy '${response.name}' and updated the variant to use it.`
          : `Created A/B agent '${response.name}'.`,
        'success',
      );
    } catch (error) {
      setModalMessage(error.message || (isEdit ? 'Unable to save edited A/B agent copy.' : 'Unable to create A/B agent.'), 'error');
    } finally {
      els.modalSave.disabled = false;
      els.modalSave.textContent = isEdit ? 'Save Edited Copy' : 'Create Agent';
    }
  }

  function handleVariantFieldChange(target, isInputEvent = false) {
    if (!state.canManage) return;
    const card = target.closest('.ab-variant-card');
    if (!card) return;
    const index = Number.parseInt(card.dataset.index || '-1', 10);
    const field = target.dataset.field;
    if (!state.variantForm[index] || !field) return;
    const variant = state.variantForm[index];

    if (field === 'provider') {
      variant.provider = target.value;
      if (!variant.provider) {
        variant.model = '';
        variant._custom_model = false;
      } else {
        const provider = getProviderConfig(variant.provider);
        const models = Array.isArray(provider?.models) ? provider.models : [];
        if (!models.some((model) => model.id === variant.model)) {
          variant.model = '';
        }
        variant._custom_model = false;
      }
      setVariantsDirty(true);
      renderVariants();
      return;
    }

    if (field === 'model_select') {
      if (target.value === '__custom__') {
        variant._custom_model = true;
        if (!String(variant.model || '').trim() || getProviderConfig(variant.provider)?.models?.some((model) => model.id === variant.model)) {
          variant.model = '';
        }
      } else {
        variant._custom_model = false;
        variant.model = target.value;
      }
      setVariantsDirty(true);
      renderVariants();
      return;
    }

    if (field === 'model_custom') {
      variant.model = target.value;
      setVariantsDirty(true);
      updateVariantSaveState();
      return;
    }

    if (field === 'agent_spec') {
      if (target.value === '__create_new__') {
        openCreateAgentModal(index);
        renderVariants();
        return;
      }
      variant.agent_spec = target.value;
      setVariantsDirty(true);
      renderVariants();
      return;
    }

    variant[field] = target.value;
    setVariantsDirty(true);
    if (!isInputEvent) {
      renderVariants();
      return;
    }
    updateVariantSaveState();
  }

  function bindEvents() {
    els.sampleRate?.addEventListener('input', (event) => {
      state.settingsForm.sample_rate = Number(event.target.value);
      setSettingsDirty(true);
      updateSettingsSaveState();
    });
    els.disclosureMode?.addEventListener('change', (event) => {
      state.settingsForm.disclosure_mode = event.target.value;
      setSettingsDirty(true);
      updateSettingsSaveState();
    });
    els.traceMode?.addEventListener('change', (event) => {
      state.settingsForm.default_trace_mode = event.target.value;
      setSettingsDirty(true);
      updateSettingsSaveState();
    });
    els.maxPending?.addEventListener('input', (event) => {
      state.settingsForm.max_pending_per_conversation = Number.parseInt(event.target.value || '0', 10);
      setSettingsDirty(true);
      updateSettingsSaveState();
    });
    els.champion?.addEventListener('change', (event) => {
      state.settingsForm.champion = event.target.value;
      setSettingsDirty(true);
      updateSettingsSaveState();
    });
    els.addVariant?.addEventListener('click', addVariant);
    els.variantSave?.addEventListener('click', saveVariants);
    els.save?.addEventListener('click', saveSettings);
    els.disable?.addEventListener('click', disablePool);
    els.modalClose?.addEventListener('click', closeCreateAgentModal);
    els.modalCancel?.addEventListener('click', closeCreateAgentModal);
    els.modalSave?.addEventListener('click', saveNewAgent);
    els.modal?.addEventListener('click', (event) => {
      if (event.target?.dataset?.closeModal === 'true') {
        closeCreateAgentModal();
      }
    });

    els.variantList?.addEventListener('input', (event) => {
      handleVariantFieldChange(event.target, true);
    });

    els.variantList?.addEventListener('change', (event) => {
      handleVariantFieldChange(event.target, false);
    });

    els.variantList?.addEventListener('click', (event) => {
      const removeButton = event.target.closest('[data-remove]');
      if (removeButton) {
        const index = Number.parseInt(removeButton.dataset.remove || '-1', 10);
        if (index >= 0) {
          state.variantForm.splice(index, 1);
          setVariantsDirty(true);
          renderVariants();
        }
        return;
      }

      const editButton = event.target.closest('[data-edit-agent]');
      if (editButton) {
        const index = Number.parseInt(editButton.dataset.editAgent || '-1', 10);
        if (index >= 0) {
          openEditAgentModal(index);
        }
      }
    });

    window.addEventListener('beforeunload', (event) => {
      if (!state.dirty.settings && !state.dirty.variants) return;
      event.preventDefault();
      event.returnValue = '';
    });
  }

  async function init() {
    bindEvents();
    try {
      await loadState();
    } catch (error) {
      setMessage(error.message || 'Failed to load A/B testing configuration.', 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
