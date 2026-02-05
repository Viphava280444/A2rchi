/**
 * Data Viewer JavaScript
 * Handles per-chat document selection and preview
 */

class DataViewer {
  constructor() {
    this.documents = [];
    this.selectedDocument = null;
    this.conversationId = null;
    this.searchDebounceTimer = null;
    this.typeFilter = 'all';
    this.statusFilter = 'all';
    
    this.init();
  }

  init() {
    // Get conversation ID from URL or localStorage (optional - can view without one)
    const urlParams = new URLSearchParams(window.location.search);
    this.conversationId = urlParams.get('conversation_id') || localStorage.getItem('currentConversationId') || null;
    
    this.bindEvents();
    this.loadDocuments();
    this.loadStats();
  }

  bindEvents() {
    // Search input
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => this.handleSearch(e.target.value));
    }
    
    // Type filter
    const typeFilter = document.getElementById('source-filter');
    if (typeFilter) {
      typeFilter.addEventListener('change', (e) => {
        this.typeFilter = e.target.value;
        this.renderDocuments();
      });
    }
    
    // Status filter
    const statusFilter = document.getElementById('enabled-filter');
    if (statusFilter) {
      statusFilter.addEventListener('change', (e) => {
        this.statusFilter = e.target.value;
        this.renderDocuments();
      });
    }
    
    // Bulk enable all
    const enableAllBtn = document.querySelector('.bulk-enable');
    if (enableAllBtn) {
      enableAllBtn.addEventListener('click', () => this.bulkEnable());
    }
    
    // Bulk disable all
    const disableAllBtn = document.querySelector('.bulk-disable');
    if (disableAllBtn) {
      disableAllBtn.addEventListener('click', () => this.bulkDisable());
    }
    
    // Refresh button
    const refreshBtn = document.querySelector('.refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => this.refresh());
    }
    
    // Document toggle
    const docToggle = document.getElementById('doc-toggle');
    if (docToggle) {
      docToggle.addEventListener('change', (e) => {
        if (this.selectedDocument) {
          this.toggleDocument(this.selectedDocument.hash, e.target.checked);
        }
      });
    }
  }

  handleSearch(query) {
    clearTimeout(this.searchDebounceTimer);
    this.searchDebounceTimer = setTimeout(() => {
      this.renderDocuments(query);
    }, 300);
  }

  async loadDocuments() {
    const listEl = document.getElementById('document-list');
    listEl.innerHTML = `
      <div class="loading-state">
        <div class="spinner"></div>
        <span>Loading documents...</span>
      </div>
    `;
    
    try {
      const params = new URLSearchParams();
      if (this.conversationId) params.set('conversation_id', this.conversationId);
      const response = await fetch(`/api/data/documents?${params.toString()}`);
      if (!response.ok) throw new Error('Failed to load documents');
      
      const data = await response.json();
      this.documents = data.documents || [];
      this.renderDocuments();
    } catch (error) {
      console.error('Error loading documents:', error);
      listEl.innerHTML = `
        <div class="empty-state">
          <svg width="48" height="48" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
          </svg>
          <span>Error loading documents</span>
        </div>
      `;
    }
  }

  async loadStats() {
    try {
      const params = new URLSearchParams();
      if (this.conversationId) params.set('conversation_id', this.conversationId);
      const response = await fetch(`/api/data/stats?${params.toString()}`);
      if (!response.ok) throw new Error('Failed to load stats');
      
      const stats = await response.json();
      
      document.getElementById('stat-enabled').textContent = stats.enabled_documents;
      document.getElementById('stat-total').textContent = stats.total_documents;
      document.getElementById('stat-size').textContent = this.formatSize(stats.total_size_bytes);
      document.getElementById('stat-sync').textContent = stats.last_sync ? new Date(stats.last_sync).toLocaleString() : 'Never';
    } catch (error) {
      console.error('Error loading stats:', error);
    }
  }

  renderDocuments(searchQuery = '') {
    const listEl = document.getElementById('document-list');
    
    // Filter documents
    let filtered = this.documents.filter(doc => {
      // Search filter
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        if (!doc.display_name.toLowerCase().includes(q) && 
            !(doc.url && doc.url.toLowerCase().includes(q))) {
          return false;
        }
      }
      
      // Type filter - map UI values to API source_type values
      if (this.typeFilter !== 'all') {
        const typeMap = {
          'local': 'local_files',
          'web': 'web',
          'ticket': 'ticket'
        };
        const apiType = typeMap[this.typeFilter] || this.typeFilter;
        if (doc.source_type !== apiType) return false;
      }
      
      // Status filter
      if (this.statusFilter === 'enabled' && !doc.enabled) return false;
      if (this.statusFilter === 'disabled' && doc.enabled) return false;
      
      return true;
    });
    
    if (filtered.length === 0) {
      listEl.innerHTML = `
        <div class="empty-state">
          <svg width="48" height="48" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
          </svg>
          <span>${searchQuery ? 'No documents match your search' : 'No documents available'}</span>
        </div>
      `;
      return;
    }
    
    // Group by source type (prettified)
    const sourceTypeNames = {
      'local_files': 'Local Files',
      'web': 'Web Pages', 
      'ticket': 'Tickets'
    };
    const groups = {};
    filtered.forEach(doc => {
      const source = sourceTypeNames[doc.source_type] || doc.source_type || 'Unknown';
      if (!groups[source]) groups[source] = [];
      groups[source].push(doc);
    });
    
    // Render groups
    let html = '';
    for (const [source, docs] of Object.entries(groups)) {
      const enabledCount = docs.filter(d => d.enabled).length;
      html += `
        <div class="document-group">
          <div class="group-header" onclick="dataViewer.toggleGroup(this)">
            <svg class="group-toggle" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M6 9l6 6 6-6"/>
            </svg>
            ${this.getSourceIcon(docs[0].source_type)}
            <span class="group-name">${this.escapeHtml(source)}</span>
            <span class="group-count">${enabledCount}/${docs.length}</span>
          </div>
          <div class="group-items">
            ${docs.map(doc => this.renderDocumentItem(doc)).join('')}
          </div>
        </div>
      `;
    }
    
    listEl.innerHTML = html;
  }

  renderDocumentItem(doc) {
    const isSelected = this.selectedDocument && this.selectedDocument.hash === doc.hash;
    return `
      <div class="document-item ${isSelected ? 'selected' : ''} ${!doc.enabled ? 'disabled' : ''}" 
           data-hash="${doc.hash}"
           onclick="dataViewer.selectDocument('${doc.hash}')">
        <label class="doc-checkbox" onclick="event.stopPropagation()">
          <input type="checkbox" ${doc.enabled ? 'checked' : ''} 
                 onchange="dataViewer.toggleDocument('${doc.hash}', this.checked)">
        </label>
        <span class="doc-icon ${doc.source_type || 'local'}">
          ${this.getDocIcon(doc.source_type)}
        </span>
        <div class="doc-info">
          <div class="doc-name">${this.escapeHtml(doc.display_name)}</div>
          <div class="doc-meta">${this.formatSize(doc.size_bytes)}</div>
        </div>
      </div>
    `;
  }

  getSourceIcon(sourceType) {
    switch (sourceType) {
      case 'web':
        return '<svg class="group-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>';
      case 'ticket':
        return '<svg class="group-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z"/></svg>';
      default:
        return '<svg class="group-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>';
    }
  }

  getDocIcon(sourceType) {
    switch (sourceType) {
      case 'web':
        return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>';
      case 'ticket':
        return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z"/></svg>';
      default:
        return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>';
    }
  }

  toggleGroup(headerEl) {
    const toggle = headerEl.querySelector('.group-toggle');
    const items = headerEl.nextElementSibling;
    toggle.classList.toggle('collapsed');
    items.classList.toggle('collapsed');
  }

  async selectDocument(hash) {
    const doc = this.documents.find(d => d.hash === hash);
    if (!doc) return;
    
    this.selectedDocument = doc;
    this.renderDocuments(document.getElementById('search-input')?.value || '');
    
    // Show preview panel - use correct IDs
    const emptyEl = document.getElementById('preview-empty');
    const contentEl = document.getElementById('preview-content');
    
    if (emptyEl) emptyEl.style.display = 'none';
    if (contentEl) contentEl.style.display = 'flex';
    
    // Set header info
    const nameEl = document.getElementById('preview-name');
    if (nameEl) nameEl.textContent = doc.display_name;
    
    const toggleEl = document.getElementById('preview-enabled');
    if (toggleEl) toggleEl.checked = doc.enabled;
    
    const toggleLabelEl = document.getElementById('toggle-label');
    if (toggleLabelEl) toggleLabelEl.textContent = doc.enabled ? 'Enabled' : 'Disabled';
    
    // Set metadata
    const sourceTypeNames = {
      'local_files': 'Local Files',
      'web': 'Web Pages',
      'ticket': 'Tickets'
    };
    const sourceEl = document.getElementById('preview-source');
    if (sourceEl) sourceEl.textContent = sourceTypeNames[doc.source_type] || doc.source_type || 'Unknown';
    
    const sizeEl = document.getElementById('preview-size');
    if (sizeEl) sizeEl.textContent = this.formatSize(doc.size_bytes);
    
    const dateEl = document.getElementById('preview-date');
    if (dateEl) dateEl.textContent = doc.ingested_at ? new Date(doc.ingested_at).toLocaleString() : 'Never';
    
    const urlEl = document.getElementById('preview-url');
    if (urlEl) {
      if (doc.url) {
        urlEl.innerHTML = `<a href="${doc.url}" target="_blank">${doc.url}</a>`;
        urlEl.parentElement.style.display = 'block';
      } else {
        urlEl.textContent = 'N/A';
      }
    }
    
    // Load content
    await this.loadDocumentContent(hash);
  }

  async loadDocumentContent(hash) {
    const viewerEl = document.getElementById('content-viewer');
    const loadingEl = document.getElementById('content-loading');
    
    if (loadingEl) loadingEl.style.display = 'flex';
    if (viewerEl) viewerEl.innerHTML = '';
    
    try {
      const params = new URLSearchParams();
      if (this.conversationId) params.set('conversation_id', this.conversationId);
      const response = await fetch(`/api/data/documents/${hash}/content?${params.toString()}`);
      if (!response.ok) throw new Error('Failed to load content');
      
      const data = await response.json();
      const content = data.content || '';
      const truncated = data.truncated || false;
      
      if (loadingEl) loadingEl.style.display = 'none';
      
      if (viewerEl) {
        viewerEl.innerHTML = this.escapeHtml(content).replace(/\n/g, '<br>');
      }
      
      const truncatedEl = document.getElementById('content-truncated');
      if (truncatedEl) {
        truncatedEl.style.display = truncated ? 'block' : 'none';
      }
    } catch (error) {
      console.error('Error loading content:', error);
      if (loadingEl) loadingEl.style.display = 'none';
      if (viewerEl) {
        viewerEl.innerHTML = '<p style="color: var(--text-secondary);">Error loading content</p>';
      }
    }
  }

  async loadFullContent(hash) {
    const viewerEl = document.getElementById('content-viewer');
    const loadingEl = document.getElementById('content-loading');
    
    if (loadingEl) loadingEl.style.display = 'flex';
    if (viewerEl) viewerEl.innerHTML = '';
    
    try {
      const params = new URLSearchParams();
      if (this.conversationId) params.set('conversation_id', this.conversationId);
      params.set('full', 'true');
      const response = await fetch(`/api/data/documents/${hash}/content?${params.toString()}`);
      if (!response.ok) throw new Error('Failed to load content');
      
      const data = await response.json();
      const content = data.content || '';
      
      if (loadingEl) loadingEl.style.display = 'none';
      if (viewerEl) {
        viewerEl.innerHTML = this.escapeHtml(content).replace(/\n/g, '<br>');
      }
      
      const truncatedEl = document.getElementById('content-truncated');
      if (truncatedEl) truncatedEl.style.display = 'none';
    } catch (error) {
      console.error('Error loading full content:', error);
      if (loadingEl) loadingEl.style.display = 'none';
      if (viewerEl) {
        viewerEl.innerHTML = '<p style="color: var(--text-secondary);">Error loading content</p>';
      }
    }
  }

  async toggleDocument(hash, enabled) {
    // Can't toggle without a conversation context
    if (!this.conversationId) {
      console.warn('Cannot toggle document without a conversation context');
      // Revert the UI toggle
      const doc = this.documents.find(d => d.hash === hash);
      if (doc) {
        const toggleEl = document.getElementById('preview-enabled');
        if (toggleEl) toggleEl.checked = doc.enabled;
      }
      return;
    }
    
    const endpoint = enabled ? 'enable' : 'disable';
    
    try {
      const response = await fetch(`/api/data/documents/${hash}/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: this.conversationId })
      });
      
      if (!response.ok) throw new Error(`Failed to ${endpoint} document`);
      
      // Update local state
      const doc = this.documents.find(d => d.hash === hash);
      if (doc) {
        doc.enabled = enabled;
        this.renderDocuments(document.getElementById('searchInput')?.value || '');
        
        // Update toggle label if this is the selected doc
        if (this.selectedDocument && this.selectedDocument.hash === hash) {
          document.getElementById('toggleLabel').textContent = enabled ? 'Enabled' : 'Disabled';
        }
      }
      
      this.showToast(`Document ${enabled ? 'enabled' : 'disabled'}`, 'success');
      this.loadStats();
    } catch (error) {
      console.error(`Error ${endpoint}ing document:`, error);
      this.showToast(`Failed to ${endpoint} document`, 'error');
      // Revert checkbox
      this.renderDocuments(document.getElementById('searchInput')?.value || '');
    }
  }

  async bulkEnable() {
    if (!this.conversationId) {
      this.showToast('Cannot modify documents without a chat session', 'error');
      return;
    }
    
    const hashes = this.documents.map(d => d.hash);
    
    try {
      const response = await fetch('/api/data/bulk-enable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          conversation_id: this.conversationId,
          hashes: hashes
        })
      });
      
      if (!response.ok) throw new Error('Failed to enable all documents');
      
      // Update local state
      this.documents.forEach(d => d.enabled = true);
      this.renderDocuments(document.getElementById('searchInput')?.value || '');
      
      this.showToast('All documents enabled', 'success');
      this.loadStats();
    } catch (error) {
      console.error('Error enabling all documents:', error);
      this.showToast('Failed to enable all documents', 'error');
    }
  }

  async bulkDisable() {
    if (!this.conversationId) {
      this.showToast('Cannot modify documents without a chat session', 'error');
      return;
    }
    
    const hashes = this.documents.map(d => d.hash);
    
    try {
      const response = await fetch('/api/data/bulk-disable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          conversation_id: this.conversationId,
          hashes: hashes
        })
      });
      
      if (!response.ok) throw new Error('Failed to disable all documents');
      
      // Update local state
      this.documents.forEach(d => d.enabled = false);
      this.renderDocuments(document.getElementById('searchInput')?.value || '');
      
      this.showToast('All documents disabled', 'success');
      this.loadStats();
    } catch (error) {
      console.error('Error disabling all documents:', error);
      this.showToast('Failed to disable all documents', 'error');
    }
  }

  async refresh() {
    const refreshBtn = document.getElementById('refreshBtn');
    refreshBtn.classList.add('loading');
    
    await this.loadDocuments();
    await this.loadStats();
    
    refreshBtn.classList.remove('loading');
    this.showToast('Data refreshed', 'success');
  }

  showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span class="toast-message">${message}</span>`;
    container.appendChild(toast);
    
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 200);
    }, 3000);
  }

  showError(message) {
    const listEl = document.getElementById('document-list');
    listEl.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
        </svg>
        <span>${message}</span>
      </div>
    `;
  }

  formatSize(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
      bytes /= 1024;
      i++;
    }
    return `${bytes.toFixed(1)} ${units[i]}`;
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}

// Initialize on page load
let dataViewer;
document.addEventListener('DOMContentLoaded', () => {
  dataViewer = new DataViewer();
});
