(function () {
  const ENDPOINTS = {
    agents: '/api/agents/list',
    pool: '/api/ab/pool',
    save: '/api/ab/pool/set',
    disable: '/api/ab/pool/disable',
  };

  const state = {
    agents: [],
    form: {
      enabled: false,
      champion: '',
      sample_rate: 1,
      disclosure_mode: 'post_vote_reveal',
      default_trace_mode: 'minimal',
      max_pending_per_conversation: 1,
      variants: [],
    },
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
    variantList: document.getElementById('ab-admin-variant-list'),
    message: document.getElementById('ab-admin-message'),
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

  function normalizeVariant(variant = {}) {
    return {
      label: String(variant.label || '').trim(),
      agent_spec: String(variant.agent_spec || '').trim(),
      provider: String(variant.provider || '').trim(),
      model: String(variant.model || '').trim(),
      recursion_limit: variant.recursion_limit ?? '',
      num_documents_to_retrieve: variant.num_documents_to_retrieve ?? '',
    };
  }

  function normalizePool(pool = {}) {
    const details = Array.isArray(pool.variant_details) ? pool.variant_details : [];
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

  function uniqueLabel(baseLabel) {
    const used = new Set(state.form.variants.map((variant) => variant.label));
    let candidate = baseLabel || 'Variant';
    if (!used.has(candidate)) return candidate;
    let index = 2;
    while (used.has(`${candidate} ${index}`)) {
      index += 1;
    }
    return `${candidate} ${index}`;
  }

  function currentLabels() {
    return state.form.variants
      .map((variant) => String(variant.label || '').trim())
      .filter(Boolean);
  }

  function syncChampionOptions() {
    if (!els.champion) return;
    const labels = currentLabels();
    const champion = labels.includes(state.form.champion) ? state.form.champion : (labels[0] || '');
    state.form.champion = champion;
    els.champion.innerHTML = labels.map((label) => (
      `<option value="${escapeHtml(label)}"${label === champion ? ' selected' : ''}>${escapeHtml(label)}</option>`
    )).join('');
    els.champion.disabled = labels.length === 0;
  }

  function validateForm() {
    const labels = currentLabels();
    if (state.form.variants.length < 2) {
      return { valid: false, message: 'Add at least 2 variants to save the pool.' };
    }
    if (labels.length !== state.form.variants.length) {
      return { valid: false, message: 'Every variant needs a non-empty label.' };
    }
    if (new Set(labels).size !== labels.length) {
      return { valid: false, message: 'Variant labels must be unique.' };
    }
    if (state.form.variants.some((variant) => !String(variant.agent_spec || '').trim())) {
      return { valid: false, message: 'Every variant needs an agent markdown file.' };
    }
    if (!labels.includes(state.form.champion)) {
      return { valid: false, message: 'Champion must match one of the variant labels.' };
    }
    if (!Number.isFinite(state.form.sample_rate) || state.form.sample_rate < 0 || state.form.sample_rate > 1) {
      return { valid: false, message: 'Sampling rate must be between 0 and 1.' };
    }
    if (!Number.isInteger(state.form.max_pending_per_conversation) || state.form.max_pending_per_conversation < 1) {
      return { valid: false, message: 'Max pending per conversation must be at least 1.' };
    }
    return { valid: true, message: '' };
  }

  function updateSaveState() {
    const validation = validateForm();
    if (els.save) {
      els.save.disabled = !validation.valid;
    }
    if (!validation.valid) {
      setMessage(validation.message, 'error');
    } else if (els.message?.classList.contains('error')) {
      setMessage('');
    }
  }

  function agentOptionsHtml(selectedFilename) {
    return state.agents.map((agent) => {
      const selected = agent.filename === selectedFilename ? ' selected' : '';
      const suffix = agent.ab_only ? ' [AB]' : '';
      return `<option value="${escapeHtml(agent.filename)}"${selected}>${escapeHtml(agent.name)} (${escapeHtml(agent.filename)})${escapeHtml(suffix)}</option>`;
    }).join('');
  }

  function renderVariants() {
    if (!els.variantList) return;
    if (!state.form.variants.length) {
      els.variantList.innerHTML = `
        <div class="ab-admin-empty-state">
          <strong>No variants configured.</strong>
          <span>Add at least two variants to enable champion/challenger comparisons.</span>
        </div>
      `;
      syncChampionOptions();
      updateSaveState();
      return;
    }

    els.variantList.innerHTML = state.form.variants.map((variant, index) => `
      <article class="ab-variant-card" data-index="${index}">
        <div class="ab-variant-card-header">
          <div>
            <h3>Variant ${index + 1}</h3>
            <p>Configure the experiment label and concrete markdown file for this arm.</p>
          </div>
          <button class="ab-variant-remove" type="button" data-remove="${index}">Remove</button>
        </div>
        <div class="ab-variant-grid">
          <label class="ab-admin-field">
            <span>Label</span>
            <input type="text" data-field="label" value="${escapeHtml(variant.label)}" placeholder="baseline">
          </label>
          <label class="ab-admin-field">
            <span>Agent Spec</span>
            <select data-field="agent_spec">
              <option value="">Select an agent markdown</option>
              ${agentOptionsHtml(variant.agent_spec)}
            </select>
          </label>
          <label class="ab-admin-field">
            <span>Provider Override</span>
            <input type="text" data-field="provider" value="${escapeHtml(variant.provider)}" placeholder="default">
          </label>
          <label class="ab-admin-field">
            <span>Model Override</span>
            <input type="text" data-field="model" value="${escapeHtml(variant.model)}" placeholder="default">
          </label>
          <label class="ab-admin-field">
            <span>Recursion Limit</span>
            <input type="number" data-field="recursion_limit" min="1" step="1" value="${escapeHtml(variant.recursion_limit)}" placeholder="default">
          </label>
          <label class="ab-admin-field">
            <span>Document Retrieval Override</span>
            <input type="number" data-field="num_documents_to_retrieve" min="1" step="1" value="${escapeHtml(variant.num_documents_to_retrieve)}" placeholder="default">
          </label>
        </div>
      </article>
    `).join('');

    syncChampionOptions();
    updateSaveState();
  }

  function renderForm() {
    if (els.status) {
      els.status.textContent = state.form.enabled ? 'Active' : 'Inactive';
      els.status.classList.toggle('active', state.form.enabled);
    }
    if (els.sampleRate) els.sampleRate.value = String(state.form.sample_rate ?? 1);
    if (els.disclosureMode) els.disclosureMode.value = state.form.disclosure_mode || 'post_vote_reveal';
    if (els.traceMode) els.traceMode.value = state.form.default_trace_mode || 'minimal';
    if (els.maxPending) els.maxPending.value = String(state.form.max_pending_per_conversation ?? 1);
    if (els.disable) els.disable.style.display = state.form.enabled ? '' : 'none';
    renderVariants();
  }

  async function loadState() {
    const [agentsResponse, poolResponse] = await Promise.all([
      fetchJson(ENDPOINTS.agents),
      fetchJson(ENDPOINTS.pool),
    ]);
    state.agents = Array.isArray(agentsResponse.agents) ? agentsResponse.agents : [];
    state.form = normalizePool(poolResponse);
    renderForm();
  }

  function readOptionalInt(value) {
    const trimmed = String(value ?? '').trim();
    if (!trimmed) return null;
    const parsed = Number.parseInt(trimmed, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function collectPayload() {
    return {
      champion: state.form.champion,
      sample_rate: state.form.sample_rate,
      disclosure_mode: state.form.disclosure_mode,
      default_trace_mode: state.form.default_trace_mode,
      max_pending_per_conversation: state.form.max_pending_per_conversation,
      variants: state.form.variants.map((variant) => ({
        label: String(variant.label || '').trim(),
        agent_spec: String(variant.agent_spec || '').trim(),
        provider: String(variant.provider || '').trim() || null,
        model: String(variant.model || '').trim() || null,
        recursion_limit: readOptionalInt(variant.recursion_limit),
        num_documents_to_retrieve: readOptionalInt(variant.num_documents_to_retrieve),
      })),
    };
  }

  function addVariant() {
    const firstAgent = state.agents[0] || {};
    state.form.variants.push({
      label: uniqueLabel(firstAgent.name || 'Variant'),
      agent_spec: firstAgent.filename || '',
      provider: '',
      model: '',
      recursion_limit: '',
      num_documents_to_retrieve: '',
    });
    renderVariants();
  }

  async function savePool() {
    const validation = validateForm();
    if (!validation.valid) {
      setMessage(validation.message, 'error');
      return;
    }
    els.save.disabled = true;
    els.save.textContent = 'Saving…';
    try {
      const result = await fetchJson(ENDPOINTS.save, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectPayload()),
      });
      state.form = normalizePool(result);
      setMessage('A/B configuration saved.', 'success');
      renderForm();
    } catch (error) {
      setMessage(error.message || 'Failed to save A/B configuration.', 'error');
    } finally {
      els.save.textContent = 'Save Configuration';
      updateSaveState();
    }
  }

  async function disablePool() {
    els.disable.disabled = true;
    try {
      await fetchJson(ENDPOINTS.disable, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      await loadState();
      setMessage('A/B testing disabled. Variant settings remain available for editing.', 'success');
    } catch (error) {
      setMessage(error.message || 'Failed to disable A/B testing.', 'error');
    } finally {
      els.disable.disabled = false;
    }
  }

  function bindEvents() {
    els.sampleRate?.addEventListener('input', (event) => {
      state.form.sample_rate = Number(event.target.value);
      updateSaveState();
    });
    els.disclosureMode?.addEventListener('change', (event) => {
      state.form.disclosure_mode = event.target.value;
      updateSaveState();
    });
    els.traceMode?.addEventListener('change', (event) => {
      state.form.default_trace_mode = event.target.value;
      updateSaveState();
    });
    els.maxPending?.addEventListener('input', (event) => {
      state.form.max_pending_per_conversation = Number.parseInt(event.target.value || '0', 10);
      updateSaveState();
    });
    els.champion?.addEventListener('change', (event) => {
      state.form.champion = event.target.value;
      updateSaveState();
    });
    els.addVariant?.addEventListener('click', addVariant);
    els.save?.addEventListener('click', savePool);
    els.disable?.addEventListener('click', disablePool);

    els.variantList?.addEventListener('input', (event) => {
      const card = event.target.closest('.ab-variant-card');
      if (!card) return;
      const index = Number.parseInt(card.dataset.index || '-1', 10);
      const field = event.target.dataset.field;
      if (!state.form.variants[index] || !field) return;
      state.form.variants[index][field] = event.target.value;
      if (field === 'label') {
        syncChampionOptions();
      }
      updateSaveState();
    });

    els.variantList?.addEventListener('change', (event) => {
      const card = event.target.closest('.ab-variant-card');
      if (!card) return;
      const index = Number.parseInt(card.dataset.index || '-1', 10);
      const field = event.target.dataset.field;
      if (!state.form.variants[index] || !field) return;
      state.form.variants[index][field] = event.target.value;
      if (field === 'label') {
        syncChampionOptions();
      }
      updateSaveState();
    });

    els.variantList?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-remove]');
      if (!button) return;
      const index = Number.parseInt(button.dataset.remove || '-1', 10);
      if (index < 0) return;
      state.form.variants.splice(index, 1);
      renderVariants();
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
