(function () {
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value ?? ''; };
  const chips = (items) => (items || []).filter((item) => item != null && String(item) !== '').map((item) => `<span class="mini-chip">${esc(item)}</span>`).join('') || '<span class="muted">none</span>';
  const metaList = (meta) => Object.entries(meta || {}).filter(([, value]) => value != null && value !== '').map(([key, value]) => `<li><span>${esc(key)}</span><strong>${esc(value)}</strong></li>`).join('');

  function renderActions(actions) {
    const container = document.getElementById('actions');
    if (!container) return;
    container.innerHTML = (actions || []).map((action, index) => `<a class="button ${index === 0 ? 'primary' : ''}" href="${esc(action.href)}" title="${esc(action.description)}">${esc(action.label)}</a>`).join('');
  }

  function renderBoard(board) {
    const container = document.getElementById('kanbanBoard');
    if (!container) return;
    container.innerHTML = (board?.columns || []).map((column) => {
      const cards = (column.cards || []).map((card) => `<article class="kanban-card">
        <div class="mesh-thumb mesh-thumb--small" aria-hidden="true"><i></i><i></i><i></i></div>
        <div class="kanban-card__top"><span>${esc(card.type || 'card')}</span><span>${esc(card.status || 'unknown')}</span></div>
        <h3>${esc(card.title || card.id)}</h3>
        <p class="kanban-id">${esc(card.id)}</p>
        ${card.summary ? `<p class="kanban-summary">${esc(card.summary)}</p>` : ''}
        <div class="mini-chip-row">${chips(card.chips)}</div>
        <ul class="kanban-meta">${metaList(card.meta)}</ul>
      </article>`).join('') || '<p class="kanban-empty">No cards</p>';
      return `<section class="kanban-column kanban-column--${esc(column.kanban_status || column.id)}" data-status="${esc(column.kanban_status || column.id)}">
        <div class="kanban-column__header"><div><h2><span class="status-dot" aria-hidden="true"></span>${esc(column.title || column.id)}<span class="sr-only">${esc(column.legacy_title || '')}</span></h2><small>${esc(column.kanban_status || column.id)}</small></div><span>${esc(column.count || 0)}</span></div>
        <p class="kanban-column__hint">${esc(column.description || '')}</p>
        <div class="kanban-column__cards">${cards}</div>
      </section>`;
    }).join('');
  }

  function renderNodeRows(nodes) {
    const list = document.getElementById('nodesList');
    if (!list) return;
    if (!nodes.length) {
      list.innerHTML = '<section class="empty-state"><p class="eyebrow">No nodes registered</p><h2>Waiting for Capability Mesh nodes to join.</h2></section>';
      return;
    }
    list.innerHTML = nodes.map((node) => {
      const health = node.online_status || { label: 'unknown', status: 'unknown' };
      const label = String(health.label || health.status || 'unknown');
      const statusClass = String(health.status || label || 'unknown').toLowerCase().replace(/[^a-z0-9_-]/g, '-');
      return `<article class="node-row"><div class="node-row__top"><div><h3>${esc(node.display_name || node.node_id)}</h3><p class="node-row__id">${esc(node.node_id)}</p></div><span class="status-pill status-pill--${esc(statusClass)}">${esc(label)}</span></div><div class="node-row__caps" aria-label="Node capabilities">${chips(node.task_types)}${chips(node.tools_available)}</div></article>`;
    }).join('');
  }

  async function openDrawer(endpoint) {
    const drawer = document.getElementById('nodesDrawer');
    const loading = document.getElementById('nodesLoading');
    const error = document.getElementById('nodesError');
    const list = document.getElementById('nodesList');
    if (!drawer || !loading || !error || !list) return;
    drawer.hidden = false;
    loading.hidden = false;
    error.hidden = true;
    list.innerHTML = '';
    try {
      const response = await fetch(endpoint || '/api/nodes/statuses', { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      renderNodeRows(await response.json());
    } catch (err) {
      error.textContent = `Unable to load node status: ${err.message}`;
      error.hidden = false;
    } finally {
      loading.hidden = true;
    }
  }

  async function init() {
    const initial = document.getElementById('initialProjection');
    let projection = initial?.textContent ? JSON.parse(initial.textContent) : null;
    if (!projection) {
      const response = await fetch('/api/ui/dashboard', { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      projection = await response.json();
    }
    const summary = projection.summary || {};
    setText('dashboardTitle', projection.title || 'Capability Mesh');
    setText('issueLabel', projection.issue_label || 'Open Design dashboard projection');
    setText('privacyNotice', projection.privacy_notice || 'Privacy-first public projection.');
    setText('nodeCount', summary.node_count || 0);
    setText('taskTypeCount', summary.task_type_count || 0);
    setText('toolCount', summary.tool_count || 0);
    setText('autoAcceptCount', summary.auto_accept_count || 0);
    setText('nodesDrawerTitle', projection.nodes_drawer?.title || 'Registered nodes');
    setText('nodesDrawerCopy', projection.nodes_drawer?.copy || 'Names, declared capabilities, and current online status only.');
    renderActions(projection.actions);
    renderBoard(projection.kanban);
    document.getElementById('nodesStat')?.addEventListener('click', () => openDrawer(projection.nodes_drawer?.endpoint));
  }

  document.querySelectorAll('[data-close-nodes]').forEach((button) => button.addEventListener('click', () => { document.getElementById('nodesDrawer').hidden = true; }));
  document.addEventListener('keydown', (event) => { const drawer = document.getElementById('nodesDrawer'); if (event.key === 'Escape' && drawer && !drawer.hidden) drawer.hidden = true; });
  init().catch((err) => { setText('privacyNotice', `Unable to load /api/ui/dashboard: ${err.message}`); });
}());
