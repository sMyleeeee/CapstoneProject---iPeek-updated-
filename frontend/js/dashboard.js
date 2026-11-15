const byDashboardId = id => document.getElementById(id);
const dashboardCounts = new Intl.NumberFormat();
const validCount = value => Number.isInteger(value) && value >= 0;
const displayCount = value => validCount(value) ? dashboardCounts.format(value) : 'Unavailable';
function dashboardNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function dashboardLoading(container, label, rows = 0) {
  // Keep the measured panel size during refreshes.
  const height = container.getBoundingClientRect().height;
  if (height) container.style.minHeight = height + 'px';
  container.replaceChildren(dashboardNode('span', 'dashboard-sr-only', label));
  const placeholder = dashboardNode('span', rows ? 'dashboard-skeleton-lines' : 'dashboard-skeleton');
  placeholder.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < rows; i++) {
    const row = dashboardNode('span', 'dashboard-skeleton-row');
    row.style.display = 'block';
    row.append(dashboardNode('span', 'dashboard-skeleton dashboard-skeleton-line'), dashboardNode('span', 'dashboard-skeleton dashboard-skeleton-line'));
    placeholder.appendChild(row);
  }
  container.appendChild(placeholder);
}
function dashboardTimestamp(value) {
  if (!value) return NaN;
  // Backend DateTime columns store UTC, including ISO strings without a timezone.
  const normalized = /T/.test(value) && !/(Z|[+-]\d{2}:\d{2})$/i.test(value) ? value + 'Z' : value;
  return new Date(normalized).getTime();
}
function dashboardDate(value, time = false) {
  const stamp = dashboardTimestamp(value);
  return Number.isFinite(stamp) ? new Intl.DateTimeFormat(undefined, {dateStyle:'medium', ...(time ? {timeStyle:'short'} : {})}).format(stamp) : 'Date unavailable';
}
function panelMessage(container, text, retry) {
  const message = dashboardNode('p', 'dashboard-panel-message', text);
  message.setAttribute('role', 'status');
  container.replaceChildren(message);
  if (retry) {
    const button = dashboardNode('button', 'btn btn-ghost btn-sm', 'Retry');
    button.type = 'button';
    button.addEventListener('click', retry);
    container.appendChild(button);
  }
}
async function initPage() {
  try {
    const me = await apiMe();
    if (me.role !== 'librarian') { location.replace(landingPage(me)); return; }
    applyNav(me.role);
    const name = me.full_name || me.username || 'Librarian';
    byDashboardId('greeting').textContent = `Welcome, ${name}.`;
    if (byDashboardId('avatarEl')) byDashboardId('avatarEl').textContent = name.substring(0, 2).toUpperCase();
    if (byDashboardId('rolePill')) byDashboardId('rolePill').textContent = 'Librarian';
    document.body.style.visibility = 'visible';
  } catch { location.replace('index.html'); return; }
  byDashboardId('refreshHealth').addEventListener('click', loadRepositoryStatus);
  byDashboardId('retryMetrics').addEventListener('click', loadDashboardMetrics);
  await Promise.allSettled([loadDashboardMetrics(), loadRecentSubmissions(), loadRepositoryStatus()]);
}
let metricsLoading = false;
async function loadDashboardMetrics() {
  if (metricsLoading) return;
  metricsLoading = true;
  const ids = ['statTotal', 'statPending', 'statApproved', 'statUsers', 'statReturned'];
  ids.forEach(id => dashboardLoading(byDashboardId(id), 'Loading...'));
  byDashboardId('dashboardMetrics').setAttribute('aria-busy', 'true');
  byDashboardId('retryMetrics').hidden = true;
  byDashboardId('metricsStatus').textContent = '';
  renderReviewQueue(null, true);
  try {
    const data = await apiDashboardStats();
    // The existing endpoint names returned papers "rejected_research"; the model is returned.
    const values = [data.total_research, data.ongoing_research, data.approved_research, data.total_users, data.rejected_research];
    ids.forEach((id, i) => byDashboardId(id).textContent = displayCount(values[i]));
    renderReviewQueue(data.ongoing_research);
    if (values.some(value => !validCount(value))) {
      byDashboardId('metricsStatus').textContent = 'Some counts are unavailable. Try refreshing the counts.';
      byDashboardId('retryMetrics').hidden = false;
    }
  } catch {
    ids.forEach(id => byDashboardId(id).textContent = 'Unavailable');
    byDashboardId('metricsStatus').textContent = 'Repository counts could not be loaded.';
    byDashboardId('retryMetrics').hidden = false;
    renderReviewQueue(null);
  } finally {
    metricsLoading = false;
    byDashboardId('dashboardMetrics').setAttribute('aria-busy', 'false');
  }
}
function renderReviewQueue(pending, loading = false) {
  const panel = byDashboardId('reviewQueueStatus');
  const known = validCount(pending);
  panel.dataset.state = loading ? 'loading' : !known ? 'unavailable' : pending ? 'pending' : 'clear';
  byDashboardId('queueTitle').textContent = loading ? 'Checking review queue…' : !known ? 'Review queue status unavailable' : pending ? `${displayCount(pending)} ${pending === 1 ? 'submission needs' : 'submissions need'} review` : 'Review queue is clear';
  byDashboardId('pendingMsg').textContent = loading ? 'Loading submission counts.' : !known ? 'Open the review queue to check submissions.' : pending ? 'Review the latest submissions and provide a decision.' : 'No submissions are waiting for review.';
  byDashboardId('reviewQueueAction').hidden = loading || (known && pending === 0);
}
let recentLoading = false;
async function loadRecentSubmissions() {
  if (recentLoading) return;
  recentLoading = true;
  const list = byDashboardId('recentList');
  list.setAttribute('aria-busy', 'true');
  dashboardLoading(list, 'Loading recent submissions...', 3);
  try {
    const data = await apiSubmissions();
    if (!Array.isArray(data.submissions)) throw new Error('Invalid submissions response');
    const submissions = [...data.submissions].sort((a,b) => (dashboardTimestamp(b.submitted_at) || 0) - (dashboardTimestamp(a.submitted_at) || 0));
    renderRecentList(submissions.slice(0, 5));
  } catch { panelMessage(list, 'Recent submissions could not be loaded.', loadRecentSubmissions); }
  finally { recentLoading = false; list.setAttribute('aria-busy', 'false'); }
}
function renderRecentList(submissions) {
  const list = byDashboardId('recentList');
  if (!submissions.length) { panelMessage(list, 'No submissions yet. New research will appear here.'); return; }
  const table = dashboardNode('table', 'dashboard-table');
  const caption = dashboardNode('caption', 'dashboard-sr-only', 'Five most recent research submissions, newest first');
  const head = document.createElement('thead'), headings = document.createElement('tr');
  ['Research', 'Submitted', 'Status', 'Open'].forEach(label => { const cell = dashboardNode('th', '', label); cell.scope = 'col'; headings.appendChild(cell); });
  head.appendChild(headings);
  const body = document.createElement('tbody');
  submissions.forEach(paper => {
    const row = document.createElement('tr');
    const research = dashboardNode('td', 'dashboard-research');
    research.append(dashboardNode('span', 'dashboard-research-title', paper.title || 'Untitled research'), dashboardNode('span', 'dashboard-muted', [paper.lead_researcher || 'Authors unavailable', paper.department || 'College unavailable'].join(' · ')));
    const submitted = dashboardNode('td', 'dashboard-date', dashboardDate(paper.submitted_at));
    submitted.dataset.label = 'Submitted';
    const status = document.createElement('td'); status.dataset.label = 'Status';
    const statuses = {approved:'Approved', pending:'Pending review', returned:'Returned'};
    const badge = dashboardNode('span', `dashboard-badge ${Object.hasOwn(statuses, paper.status) ? paper.status : ''}`, statuses[paper.status] || 'Status unavailable');
    status.appendChild(badge);
    const action = document.createElement('td'); action.className = 'dashboard-open';
    const href = paper.status === 'approved' && paper.source ? `detail.html?id=${encodeURIComponent(paper.id)}` : `review.html?id=${encodeURIComponent(paper.id)}`;
    const link = dashboardNode('a', '', 'Open ');
    link.href = href;
    link.setAttribute('aria-label', `Open ${paper.title || 'research submission'}`);
    link.appendChild(createUiIcon('arrow-right'));
    action.appendChild(link); row.append(research, submitted, status, action); body.appendChild(row);
  });
  table.append(caption, head, body); list.replaceChildren(table);
}
let healthLoading = false;
async function loadRepositoryStatus() {
  if (healthLoading) return;
  healthLoading = true;
  const panel = byDashboardId('repositoryStatus'), badge = byDashboardId('healthState'), refresh = byDashboardId('refreshHealth');
  refresh.disabled = true; badge.textContent = 'Loading'; badge.dataset.state = 'loading';
  dashboardLoading(panel, 'Loading repository status...', 1);
  try {
    const data = await apiStatus();
    if (!validCount(data.chunks_indexed)) {
      badge.textContent = 'Degraded'; badge.dataset.state = 'degraded';
      panel.textContent = 'The search index is unavailable. Search and research insights may be affected. The index size could not be checked.';
    } else if (data.chunks_indexed === 0 && data.approved > 0) {
      badge.textContent = 'Degraded'; badge.dataset.state = 'degraded';
      panel.textContent = 'Approved papers are present, but the search index is empty. These papers may not appear in search or research insights. Please ask the repository maintainer to check the index.';
    } else if (data.status !== 'ok') {
      badge.textContent = 'Degraded'; badge.dataset.state = 'degraded';
      panel.textContent = data.message || 'Some repository services need attention.';
    } else {
      badge.textContent = 'Healthy'; badge.dataset.state = 'healthy';
      panel.textContent = data.chunks_indexed === 0 ? 'No approved papers yet. The search index is empty.' : `${displayCount(data.chunks_indexed)} searchable passages indexed. Repository search is available.`;
    }
  } catch {
    badge.textContent = 'Unavailable'; badge.dataset.state = 'unavailable';
    panel.textContent = 'Repository health could not be checked. Try refreshing the status.';
  } finally { healthLoading = false; refresh.disabled = false; }
}
initPage();
