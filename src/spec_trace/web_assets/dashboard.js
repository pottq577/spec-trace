let state = null;
let folderState = null;
const collapsedDocumentIds = new Set();
let cyclePolling = false;
let collapseInitialized = false;
let selectedDocumentId = null;
let selectedReviewDocumentId = null;
let selectedDetailTab = 'changes';
let toastTimer = null;
const $ = (id) => document.getElementById(id);

async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '요청에 실패했습니다.');
  return body;
}
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
}
function formatTimestamp(value) {
  if (!value) return '-';
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat('ko-KR', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}).format(parsed);
}
function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total}초`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${minutes}분 ${rest}초` : `${minutes}분`;
}
function liveDuration(startedAt, fallback=0) {
  if (!startedAt) return fallback;
  const started = Date.parse(startedAt);
  if (!Number.isFinite(started)) return fallback;
  return Math.max(0, (Date.now() - started) / 1000);
}
function showToast(message, tone='info') {
  const toast = $('toast');
  toast.textContent = message;
  toast.dataset.tone = tone;
  toast.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, tone === 'error' ? 6500 : 4200);
}
function walkDocuments(nodes, callback) {
  for (const node of nodes) {
    callback(node);
    walkDocuments(node.children || [], callback);
  }
}
function findNode(nodes, id) {
  for (const node of nodes) {
    if (node.planning_document_id === id) return node;
    const nested = findNode(node.children || [], id);
    if (nested) return nested;
  }
  return null;
}
function findFirstDocument(nodes, predicate) {
  for (const node of nodes) {
    if (predicate(node)) return node;
    const nested = findFirstDocument(node.children || [], predicate);
    if (nested) return nested;
  }
  return null;
}
function initializeTreeCollapse(nodes, depth=0) {
  for (const node of nodes) {
    if ((node.children || []).length && depth >= 1) collapsedDocumentIds.add(node.planning_document_id);
    initializeTreeCollapse(node.children || [], depth + 1);
  }
}
function renderOverview() {
  const summary = {total:0, review:0, changed:0, uncollected:0, unavailable:0};
  let latestCollection = null;
  walkDocuments(state.documents || [], (node) => {
    summary.total += 1;
    if (node.needs_review) summary.review += 1;
    if (node.latest_change) summary.changed += 1;
    if (!node.current_snapshot_id) summary.uncollected += 1;
    if (node.source_status !== 'AVAILABLE') summary.unavailable += 1;
    if (node.last_collected_at && (!latestCollection || node.last_collected_at > latestCollection)) latestCollection = node.last_collected_at;
  });
  $('totalCount').textContent = summary.total;
  $('reviewCount').textContent = summary.review;
  $('changedCount').textContent = summary.changed;
  $('uncollectedCount').textContent = summary.uncollected;
  $('unavailableCount').textContent = summary.unavailable;
  $('lastCollection').textContent = latestCollection ? `마지막 수집 ${formatTimestamp(latestCollection)}` : '마지막 수집 -';
  syncMetricSelection();
}
function syncMetricSelection() {
  const value = $('documentFilter').value;
  document.querySelectorAll('.metric').forEach((button) => button.classList.toggle('active', button.dataset.filter === value));
}
function matchesDocumentFilter(node, filterValue) {
  if (filterValue === 'review') return Boolean(node.needs_review);
  if (filterValue === 'changed') return Boolean(node.latest_change);
  if (filterValue === 'uncollected') return !node.current_snapshot_id;
  if (filterValue === 'unavailable') return node.source_status !== 'AVAILABLE';
  return true;
}
function filteredTree(nodes, q, filterValue) {
  return nodes.map((node) => {
    const children = filteredTree(node.children || [], q, filterValue);
    const titleMatches = !q || node.title.toLowerCase().includes(q);
    if ((titleMatches && matchesDocumentFilter(node, filterValue)) || children.length) return {...node, children};
    return null;
  }).filter(Boolean);
}
function revealBranches(nodes) {
  for (const node of nodes) {
    if ((node.children || []).length) collapsedDocumentIds.delete(node.planning_document_id);
    revealBranches(node.children || []);
  }
}
function nodeHtml(node) {
  const latest = node.latest_change;
  const subtree = node.subtree_change;
  const ownChanged = latest ? 1 : 0;
  const ownReview = node.needs_review ? 1 : 0;
  const descendantChanged = Math.max(0, Number(subtree?.changed_documents || 0) - ownChanged);
  const descendantReview = Math.max(0, Number(subtree?.review_documents || 0) - ownReview);
  const signals = [];
  if (node.source_status !== 'AVAILABLE') signals.push('<span class="badge off">원본 없음</span>');
  else if (!node.current_snapshot_id) signals.push('<span class="badge muted">미수집</span>');
  if (node.needs_review) signals.push('<span class="badge accent">검토 필요</span>');
  else if (latest) signals.push('<span class="badge">변경 이력</span>');
  if (descendantReview) signals.push(`<span class="badge accent">하위 검토 ${descendantReview}</span>`);
  else if (descendantChanged) signals.push(`<span class="badge">하위 변경 ${descendantChanged}</span>`);
  let meta = '';
  if (latest) meta = `최근 변경 ${Number(latest.change_count || 0)}건 · ${formatTimestamp(latest.created_at)}`;
  else if (subtree) meta = `하위 ${Number(subtree.changed_documents || 0)}개 문서 변경 · 최근 ${formatTimestamp(subtree.latest_created_at)}`;
  else if (!node.current_snapshot_id) meta = '아직 수집되지 않음';
  else if (node.last_collected_at) meta = `마지막 수집 ${formatTimestamp(node.last_collected_at)}`;
  const childNodes = node.children || [];
  const hasChildren = childNodes.length > 0;
  const collapsed = hasChildren && collapsedDocumentIds.has(node.planning_document_id);
  const toggle = hasChildren
    ? `<button type="button" class="tree-toggle" aria-expanded="${collapsed ? 'false' : 'true'}" aria-label="${collapsed ? '펼치기' : '접기'}: ${escapeHtml(node.title)}" onclick="toggleTreeNode('${node.planning_document_id}')">${collapsed ? '▸' : '▾'}</button>`
    : '<span class="tree-toggle-placeholder" aria-hidden="true"></span>';
  const children = childNodes.map(nodeHtml).join('');
  const selected = selectedDocumentId === node.planning_document_id ? ' selected' : '';
  return `<li><div class="node${selected}">${toggle}<button type="button" class="node-select" onclick="selectDocument('${node.planning_document_id}')"><div class="node-main"><span class="node-title">${escapeHtml(node.title)}</span><span class="node-signals">${signals.join('')}</span></div>${meta ? `<div class="node-meta">${escapeHtml(meta)}</div>` : ''}</button></div>${children ? `<ul class="tree-children"${collapsed ? ' hidden' : ''}>${children}</ul>` : ''}</li>`;
}
function renderTree({reveal=false}={}) {
  const q = $('search').value.trim().toLowerCase();
  const filterValue = $('documentFilter').value;
  const nodes = filteredTree(state.documents || [], q, filterValue);
  if (reveal && (q || filterValue !== 'all')) revealBranches(nodes);
  $('tree').innerHTML = nodes.length ? `<ul class="tree">${nodes.map(nodeHtml).join('')}</ul>` : '<div class="status">표시할 문서가 없습니다.</div>';
  syncMetricSelection();
}
function toggleTreeNode(id) {
  if (collapsedDocumentIds.has(id)) collapsedDocumentIds.delete(id);
  else collapsedDocumentIds.add(id);
  renderTree();
}
window.toggleTreeNode = toggleTreeNode;
function setAllTreeCollapsed(collapsed) {
  walkDocuments(state.documents || [], (node) => {
    if (!(node.children || []).length) return;
    if (collapsed) collapsedDocumentIds.add(node.planning_document_id);
    else collapsedDocumentIds.delete(node.planning_document_id);
  });
  renderTree();
}
function detailMeta(node) {
  const parts = [];
  if (node.source_status !== 'AVAILABLE') parts.push('Notion 원본 없음');
  else if (!node.current_snapshot_id) parts.push('미수집');
  else parts.push('수집됨');
  if (node.latest_change) parts.push(`최근 변경 ${formatTimestamp(node.latest_change.created_at)}`);
  if (node.needs_review) parts.push(analysisStatusLabel(node.latest_change?.analysis_status));
  return parts.join(' · ');
}
async function selectDocument(id, options={}) {
  const node = findNode(state.documents || [], id);
  if (!node) return;
  selectedDocumentId = id;
  selectedReviewDocumentId = id;
  $('detailEmpty').hidden = true;
  $('detailContent').hidden = false;
  $('detailTitle').textContent = node.title;
  $('detailMeta').textContent = detailMeta(node);
  $('selectedExport').disabled = node.source_status !== 'AVAILABLE';
  renderTree();
  await setDetailTab(options.tab || selectedDetailTab, true);
}
window.selectDocument = selectDocument;
function clearSelection() {
  selectedDocumentId = null;
  selectedReviewDocumentId = null;
  $('detailEmpty').hidden = false;
  $('detailContent').hidden = true;
  renderTree();
}
async function setDetailTab(tab, loadContent=false) {
  selectedDetailTab = tab === 'review' ? 'review' : 'changes';
  const review = selectedDetailTab === 'review';
  $('changesTab').classList.toggle('active', !review);
  $('reviewTab').classList.toggle('active', review);
  $('changesPanel').hidden = review;
  $('reviewPanel').hidden = !review;
  if (!selectedDocumentId || !loadContent) return;
  if (review) await loadReviewDocuments(selectedDocumentId);
  else await loadChanges(selectedDocumentId);
}
function changeTypeLabel(value) {
  return ({PAGE_ADDED:'문서 추가', PAGE_REMOVED:'문서 삭제', CONTENT_CHANGED:'내용 변경', PARENT_CHANGED:'위치 변경', ROLE_CHANGED:'역할 변경'})[value] || value;
}
function analysisStatusLabel(value) {
  return ({PENDING_SOURCE_DIFF:'의미 분석 대기', SOURCE_DIFF_PROPOSED:'변경 분석 제안', SOURCE_DIFF_ADOPTED:'변경 분석 반영', IMPACT_PROPOSED:'영향 분석 제안', COMPLETED:'분석 완료', FAILED:'분석 실패'})[value] || value || '';
}
function changeStructureHtml(change) {
  if (change.change_type === 'PARENT_CHANGED') return `<div class="change-structure">상위 페이지: ${escapeHtml(change.baseline_parent_notion_page_id || '없음')} → ${escapeHtml(change.target_parent_notion_page_id || '없음')}</div>`;
  if (change.change_type === 'ROLE_CHANGED') return `<div class="change-structure">역할: ${escapeHtml(change.baseline_role || '없음')} → ${escapeHtml(change.target_role || '없음')}</div>`;
  if (change.change_type === 'PAGE_ADDED') return '<div class="change-structure">새 페이지가 Snapshot에 추가되었습니다.</div>';
  if (change.change_type === 'PAGE_REMOVED') return '<div class="change-structure">페이지가 Snapshot에서 제거되었습니다.</div>';
  return '';
}
function changeDiffHtml(change) {
  const lines = change.content_diff || [];
  if (!lines.length) return '<div class="diff-empty">표시할 본문 diff가 없습니다.</div>';
  const rendered = lines.map((line) => {
    let klass = 'diff-line';
    if (line.startsWith('@@')) klass += ' hunk';
    else if (line.startsWith('+')) klass += ' added';
    else if (line.startsWith('-')) klass += ' removed';
    return `<span class="${klass}">${escapeHtml(line)}</span>`;
  }).join('');
  const truncated = change.content_diff_truncated ? '<div class="change-structure">diff가 길어 일부 줄만 표시합니다.</div>' : '';
  return `${truncated}<pre class="diff">${rendered}</pre>`;
}
function changeRowHtml(change) {
  const diffAvailable = (change.content_diff || []).length > 0;
  const counts = diffAvailable ? `<span class="badge">+${Number(change.added_lines || 0)} / -${Number(change.deleted_lines || 0)}</span>` : '';
  return `<details class="change-row"${diffAvailable ? ' open' : ''}><summary><div class="change-row-copy"><strong>${escapeHtml(change.page_title || change.notion_page_id)}</strong><div class="node-meta">${escapeHtml(change.notion_page_id)}</div></div><div class="change-row-badges"><span class="badge">${escapeHtml(changeTypeLabel(change.change_type))}</span>${counts}</div></summary><div class="change-detail">${changeStructureHtml(change)}${changeDiffHtml(change)}</div></details>`;
}
async function loadChanges(id) {
  const node = findNode(state.documents || [], id);
  $('changesTitle').textContent = '변경 이력';
  $('changesSummary').textContent = '변경 이력을 불러오는 중…';
  $('changesList').innerHTML = '';
  try {
    const result = await api(`/api/changes?planning_document_id=${encodeURIComponent(id)}`);
    if (selectedDocumentId !== id) return;
    const changeSets = result.change_sets || [];
    if (!changeSets.length) {
      $('changesSummary').textContent = node?.current_snapshot_id ? '수집 이후 기록된 변경 이력이 없습니다.' : '아직 Snapshot이 없습니다. 먼저 문서를 수집하세요.';
      return;
    }
    const latest = changeSets[0];
    $('changesSummary').textContent = `변경 이력 ${changeSets.length}회 · 최근 변경 ${latest.change_count}건 · ${formatTimestamp(latest.created_at)}`;
    $('changesList').innerHTML = changeSets.map((changeSet) => {
      const changes = changeSet.physical_changes || [];
      const rows = changes.length ? changes.map(changeRowHtml).join('') : '<div class="status">기록된 물리 변경이 없습니다.</div>';
      return `<div class="change-set"><div class="change-set-head"><div><strong>${escapeHtml(formatTimestamp(changeSet.target_captured_at || changeSet.created_at))}</strong><div class="node-meta">${escapeHtml(formatTimestamp(changeSet.baseline_captured_at))} → ${escapeHtml(formatTimestamp(changeSet.target_captured_at))}</div></div><div class="change-set-badges"><span class="badge">${Number(changeSet.change_count || 0)}건</span><span class="badge">${escapeHtml(analysisStatusLabel(changeSet.analysis_status))}</span></div></div><div class="change-list">${rows}</div></div>`;
    }).join('');
  } catch (e) {
    $('changesSummary').textContent = `변경 이력 조회 실패: ${e.message}`;
  }
}
async function loadReviewDocuments(id) {
  selectedReviewDocumentId = id;
  $('reviewTitle').textContent = '개발 검토 전달';
  $('reviewStatus').textContent = '개발자가 작성한 로컬 Markdown을 확인하는 중…';
  $('reviewDirectory').textContent = '';
  $('reviewDocuments').innerHTML = '';
  try {
    const result = await api('/api/local-documents', {method:'POST', body:JSON.stringify({planning_document_id:id})});
    if (selectedDocumentId !== id) return;
    $('reviewDirectory').textContent = result.directory;
    $('reviewDocuments').innerHTML = result.documents.length
      ? result.documents.map((doc) => {
          const docState = doc.published ? '게시됨' : (doc.dirty ? '수정됨' : '미게시');
          return `<div class="review-row"><input type="checkbox" class="review-check" value="${escapeHtml(doc.path)}"><label>${escapeHtml(doc.path)}</label><span class="badge">${docState}</span></div>`;
        }).join('')
      : '<div class="status">전달할 개발 검토 Markdown이 없습니다. Notion 원본과 같은 폴더에 직접 작성한 .md 파일을 추가하세요.</div>';
    $('reviewStatus').textContent = '';
  } catch (e) {
    $('reviewStatus').textContent = e.message;
  }
}
async function openChanges(id) { await selectDocument(id, {tab:'changes'}); }
async function openReviewDocuments(id) { await selectDocument(id, {tab:'review'}); }
window.openChanges = openChanges;
window.openReviewDocuments = openReviewDocuments;
async function exportDocument(id) {
  showToast('선택한 문서를 최신 수집하고 Markdown으로 저장하는 중…');
  try {
    const result = await api('/api/export', {method:'POST', body:JSON.stringify({planning_document_id:id})});
    showToast(`저장됨: ${result.export.path}`);
    await load();
  } catch (e) {
    showToast(e.message, 'error');
  }
}
window.exportDocument = exportDocument;

function renderCycleResult(result) {
  const sync = result.source_sync || {};
  const collections = result.collections || [];
  const answers = result.answers || [];
  const reviewResponses = result.review_responses || [];
  const failedStatuses = new Set(['SOURCE_UNAVAILABLE', 'SOURCE_UNSTABLE', 'COLLECTION_FAILED']);
  const failures = collections.filter((item) => failedStatuses.has(item.status));
  const skipped = collections.filter((item) => item.status === 'SKIPPED_UNCHANGED');
  const collected = collections.length - failures.length - skipped.length;
  const firstFailure = failures.find((item) => item.failure_detail);
  const failureDetail = firstFailure ? `\n첫 실패: ${firstFailure.failure_detail}` : '';
  $('cycleStatus').textContent = `메뉴 ${sync.active_pages ?? 0}개 · 확인 ${collections.length}개 · 변경 없음 ${skipped.length}개 · 수집 성공 ${collected}개 · 실패 ${failures.length}개 · 구조화 답변 ${answers.reduce((n,x)=>n+(x.answers_collected || 0),0)}건 · 문서 답변 ${reviewResponses.length}건${failureDetail}`;
}
function renderCycleProgress(job) {
  const progress = job.progress || {};
  const panel = $('cycleProgress');
  if (!progress.phase) { panel.hidden = true; return; }
  panel.hidden = false;
  $('cyclePhase').textContent = progress.phase_label || progress.phase;
  const total = Number(progress.total || 0);
  const processed = Number(progress.processed || 0);
  if (total > 0) {
    const percent = Math.min(100, Math.max(0, processed / total * 100));
    $('cycleCount').textContent = `${processed} / ${total} (${percent.toFixed(1)}%)`;
    $('cycleBar').style.width = `${percent}%`;
  } else {
    $('cycleCount').textContent = '';
    $('cycleBar').style.width = '0%';
  }
  const labels = {CHECKING:'변경 확인 중', COLLECTING:'원문 수집 중', SKIPPED_UNCHANGED:'변경 없음', SNAPSHOT_CREATED:'수집 완료', UNCHANGED:'수집 완료 · 내용 동일', SOURCE_UNAVAILABLE:'원문 없음', SOURCE_UNSTABLE:'수집 불안정', COLLECTION_FAILED:'수집 실패'};
  let itemDuration = progress.duration_seconds;
  if (['CHECKING', 'COLLECTING'].includes(progress.item_status)) itemDuration = liveDuration(progress.item_started_at, itemDuration || 0);
  const currentParts = [];
  if (progress.title) currentParts.push(`현재 ${progress.current ?? '-'} / ${progress.total ?? '-'} · ${progress.title}`);
  if (progress.item_status) currentParts.push(labels[progress.item_status] || progress.item_status);
  if (itemDuration != null && progress.item_status) currentParts.push(formatDuration(itemDuration));
  $('cycleCurrent').textContent = currentParts.join(' · ');
  const elapsed = liveDuration(job.started_at, progress.elapsed_seconds || 0);
  $('cycleStats').textContent = total > 0
    ? `변경 없음 ${progress.skipped || 0}개 · 수집 ${progress.collected || 0}개 · 실패 ${progress.failed || 0}개 · 경과 ${formatDuration(elapsed)}`
    : `경과 ${formatDuration(elapsed)}`;
}
async function restoreCycleState() {
  const job = await api('/api/cycle');
  if (job.status === 'RUNNING') {
    $('cycle').disabled = true;
    renderCycleProgress(job);
    $('cycleStatus').textContent = '실행 중… 새로고침해도 서버에서 계속 진행됩니다.';
    if (!cyclePolling) void monitorCycle(job);
    return;
  }
  $('cycle').disabled = false;
  if (job.status === 'FAILED') {
    $('cycleStatus').textContent = `실패: ${job.error || '전체 최신화에 실패했습니다.'}`;
    return;
  }
  if (job.status === 'COMPLETED') {
    renderCycleProgress(job);
    renderCycleResult(job.result || {});
  }
}
async function monitorCycle(initialJob) {
  if (cyclePolling) return;
  cyclePolling = true;
  $('cycle').disabled = true;
  try {
    let job = initialJob;
    while (job.status === 'RUNNING') {
      renderCycleProgress(job);
      $('cycleStatus').textContent = '실행 중… 새로고침해도 서버에서 계속 진행됩니다.';
      await new Promise((resolve) => setTimeout(resolve, 1000));
      job = await api('/api/cycle');
    }
    if (job.status === 'FAILED') throw new Error(job.error || '전체 최신화에 실패했습니다.');
    if (job.status === 'COMPLETED') {
      renderCycleProgress(job);
      renderCycleResult(job.result || {});
      await load();
    }
  } catch (e) {
    $('cycleStatus').textContent = `실패: ${e.message}`;
  } finally {
    cyclePolling = false;
    $('cycle').disabled = false;
  }
}

async function browseFolders(path='') {
  $('folderStatus').textContent = '폴더를 불러오는 중…';
  try {
    const query = path ? `?path=${encodeURIComponent(path)}` : '';
    folderState = await api(`/api/directories${query}`);
    $('folderPath').textContent = folderState.path;
    $('folderUp').disabled = !folderState.parent;
    const list = $('folderList');
    list.innerHTML = '';
    if (!folderState.directories.length) {
      const empty = document.createElement('div');
      empty.className = 'status';
      empty.style.padding = '12px';
      empty.textContent = '하위 폴더가 없습니다.';
      list.appendChild(empty);
    } else {
      for (const directory of folderState.directories) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'folder-row';
        button.textContent = `📁 ${directory.name}`;
        button.addEventListener('click', () => browseFolders(directory.path));
        list.appendChild(button);
      }
    }
    $('folderStatus').textContent = '';
    return true;
  } catch (e) {
    $('folderStatus').textContent = e.message;
    return false;
  }
}
async function openFolderPicker() {
  const dialog = $('folderDialog');
  if (!dialog.open) dialog.showModal();
  const current = $('exportRoot').value.trim();
  const initial = current || state.settings?.export_root || state.suggested_export_root || state.browse_root || '';
  const loaded = await browseFolders(initial);
  if (!loaded && initial !== state.browse_root) await browseFolders(state.browse_root || '');
}
async function applyExportRoot() {
  const exportRoot = $('exportRoot').value.trim();
  if (!exportRoot) throw new Error('저장 위치를 입력하거나 폴더를 선택하세요.');
  const result = await api('/api/settings', {method:'POST', body:JSON.stringify({export_root:exportRoot, create_export_root:false})});
  return result.settings.export_root;
}

async function load() {
  state = await api('/api/state');
  const settings = state.settings || {};
  const source = settings.notion_source || {};
  $('databaseId').value = source.database_id || '';
  $('dataSourceId').value = source.data_source_id || '';
  $('parentProperty').value = source.parent_property || '상위 항목';
  const configuredExportRoot = settings.export_root || '';
  $('exportRoot').value = configuredExportRoot || state.suggested_export_root || '';
  $('suggestion').textContent = !configuredExportRoot && state.suggested_export_root ? `자동 제안: ${state.suggested_export_root}` : '';
  $('savedExportRoot').textContent = configuredExportRoot ? `현재 적용됨: ${configuredExportRoot}` : '현재 적용된 저장 위치가 없습니다.';
  if (!collapseInitialized) {
    initializeTreeCollapse(state.documents || []);
    collapseInitialized = true;
  }
  renderOverview();
  renderTree();
  const selected = selectedDocumentId ? findNode(state.documents || [], selectedDocumentId) : null;
  const preferred = selected
    || findFirstDocument(state.documents || [], (node) => node.needs_review)
    || findFirstDocument(state.documents || [], (node) => Boolean(node.latest_change))
    || findFirstDocument(state.documents || [], (node) => node.source_status === 'AVAILABLE')
    || findFirstDocument(state.documents || [], () => true);
  if (preferred) await selectDocument(preferred.planning_document_id, {tab:selectedDetailTab});
  else clearSelection();
  await restoreCycleState();
}

$('publishSelected').addEventListener('click', async () => {
  if (!selectedReviewDocumentId) return;
  const paths = [...document.querySelectorAll('.review-check:checked')].map((el) => el.value);
  try {
    const result = await api('/api/publish', {method:'POST', body:JSON.stringify({planning_document_id:selectedReviewDocumentId, paths})});
    showToast(result.length ? result.map((item) => `${item.status}: ${item.path}`).join('\n') : '게시할 문서를 선택하세요.');
    await loadReviewDocuments(selectedReviewDocumentId);
  } catch (e) { showToast(e.message, 'error'); }
});
$('collectResponses').addEventListener('click', async () => {
  if (!selectedReviewDocumentId) return;
  try {
    const result = await api('/api/responses', {method:'POST', body:JSON.stringify({planning_document_id:selectedReviewDocumentId})});
    showToast(result.length ? result.map((item) => `회수됨: ${item.path}`).join('\n') : '새 기획자 답변이 없습니다.');
  } catch (e) { showToast(e.message, 'error'); }
});
$('chooseExport').addEventListener('click', openFolderPicker);
$('closeFolderDialog').addEventListener('click', () => $('folderDialog').close());
$('folderUp').addEventListener('click', () => { if (folderState?.parent) void browseFolders(folderState.parent); });
$('selectFolder').addEventListener('click', () => {
  if (!folderState?.path) return;
  $('exportRoot').value = folderState.path;
  $('folderDialog').close();
  $('exportStatus').textContent = '폴더를 선택했습니다. 저장 위치 적용을 눌러 반영하세요.';
});
$('openSettings').addEventListener('click', () => $('settingsDialog').showModal());
$('closeSettings').addEventListener('click', () => $('settingsDialog').close());
$('search').addEventListener('input', () => renderTree({reveal:true}));
$('documentFilter').addEventListener('change', () => renderTree({reveal:true}));
document.querySelectorAll('.metric').forEach((button) => button.addEventListener('click', () => {
  $('documentFilter').value = button.dataset.filter;
  renderTree({reveal:true});
}));
$('collapseAll').addEventListener('click', () => setAllTreeCollapsed(true));
$('expandAll').addEventListener('click', () => setAllTreeCollapsed(false));
$('refresh').addEventListener('click', () => void load());
$('changesTab').addEventListener('click', () => void setDetailTab('changes', true));
$('reviewTab').addEventListener('click', () => void setDetailTab('review', true));
$('selectedExport').addEventListener('click', () => { if (selectedDocumentId) void exportDocument(selectedDocumentId); });
$('saveExportRoot').addEventListener('click', async () => {
  try {
    const exportRoot = await applyExportRoot();
    $('savedExportRoot').textContent = `현재 적용됨: ${exportRoot}`;
    $('exportStatus').textContent = `저장 위치를 적용했습니다: ${exportRoot}`;
    showToast('Markdown 저장 위치를 적용했습니다.');
    await load();
  } catch (e) { $('exportStatus').textContent = e.message; }
});
$('exportAll').addEventListener('click', async () => {
  const button = $('exportAll');
  button.disabled = true;
  try {
    const exportRoot = await applyExportRoot();
    $('savedExportRoot').textContent = `현재 적용됨: ${exportRoot}`;
    showToast('수집된 문서를 현재 Snapshot 기준으로 저장하는 중…');
    const result = await api('/api/export-all', {method:'POST', body:'{}'});
    const firstFailure = (result.failures || [])[0];
    const failureDetail = firstFailure ? `\n첫 실패: ${firstFailure.title || firstFailure.planning_document_id} · ${firstFailure.error}` : '';
    showToast(`전체 저장 완료: ${result.exported}/${result.total}개 · 실패 ${result.failed}개\n저장 위치: ${result.root}${failureDetail}`, result.failed ? 'error' : 'info');
    await load();
  } catch (e) { showToast(`전체 저장 실패: ${e.message}`, 'error'); }
  finally { button.disabled = false; }
});
$('saveSource').addEventListener('click', async () => {
  try {
    await api('/api/settings', {method:'POST', body:JSON.stringify({database_id:$('databaseId').value, data_source_id:$('dataSourceId').value, parent_property:$('parentProperty').value})});
    $('sourceStatus').textContent = 'Notion 원본 설정을 저장했습니다.';
    showToast('Notion 원본 설정을 저장했습니다.');
    await load();
  } catch (e) { $('sourceStatus').textContent = e.message; }
});
$('cycle').addEventListener('click', async () => {
  if (cyclePolling) return;
  $('cycle').disabled = true;
  $('cycleStatus').textContent = '전체 최신화를 시작하는 중…';
  try {
    const job = await api('/api/cycle', {method:'POST', body:'{}'});
    await monitorCycle(job);
  } catch (e) {
    $('cycleStatus').textContent = `실패: ${e.message}`;
    $('cycle').disabled = false;
  }
});
load().catch((e) => {
  $('tree').innerHTML = `<div class="status">${escapeHtml(e.message)}</div>`;
  showToast(e.message, 'error');
});
