from __future__ import annotations

import argparse
import json
import logging
import threading
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import SettingsService
from .errors import SpecTraceError, ValidationError
from .notion import NotionCliClient, NotionPort
from .review_documents import ReviewDocumentService
from .runtime import RuntimeService
from .source_export import SourceExportService
from .util import utc_now
from .workspace import Workspace, WorkspaceLock

logger = logging.getLogger(__name__)


HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>spec-trace</title>
<style>
:root { color-scheme: light; font-family: Inter, Pretendard, system-ui, sans-serif; }
body { margin: 0; background: #f6f7f9; color: #17191c; }
main { max-width: 1080px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { margin: 0 0 6px; font-size: 28px; }
.lead { margin: 0 0 28px; color: #667085; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 18px; box-shadow: 0 1px 2px rgba(0,0,0,.03); }
.card h2 { margin: 0 0 14px; font-size: 17px; }
label { display: block; margin: 12px 0 5px; font-size: 13px; color: #475467; }
input { width: 100%; box-sizing: border-box; border: 1px solid #d0d5dd; border-radius: 8px; padding: 10px 11px; font: inherit; }
button { border: 0; border-radius: 8px; padding: 10px 14px; font: inherit; cursor: pointer; background: #17191c; color: white; }
button.secondary { background: #eef0f3; color: #17191c; }
button:disabled { cursor: default; opacity: .55; }
.input-row { display: flex; gap: 8px; align-items: center; }
.input-row input { flex: 1; }
dialog { width: min(680px, calc(100vw - 40px)); border: 0; border-radius: 14px; padding: 0; box-shadow: 0 24px 64px rgba(0,0,0,.24); }
dialog::backdrop { background: rgba(17,24,39,.42); }
.dialog-body { padding: 18px; }
.dialog-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.dialog-head h3 { margin: 0; font-size: 17px; }
.folder-list { border: 1px solid #e5e7eb; border-radius: 10px; max-height: 420px; overflow: auto; margin-top: 12px; }
.folder-row { width: 100%; display: flex; align-items: center; gap: 8px; background: #fff; color: #17191c; text-align: left; border-radius: 0; border-bottom: 1px solid #eef0f3; padding: 10px 12px; }
.folder-row:last-child { border-bottom: 0; }
.folder-row:hover { background: #f6f7f9; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.actions { display: flex; gap: 8px; margin-top: 14px; align-items: center; }
.status { min-height: 22px; margin-top: 12px; color: #475467; font-size: 13px; white-space: pre-wrap; }
.cycle-progress { margin-top: 14px; padding: 12px; border: 1px solid #e5e7eb; border-radius: 10px; background: #f9fafb; }
.progress-head { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; }
.progress-track { height: 8px; margin-top: 9px; overflow: hidden; border-radius: 999px; background: #e5e7eb; }
.progress-bar { height: 100%; width: 0; border-radius: inherit; background: #17191c; transition: width .25s ease; }
.progress-current { margin-top: 9px; font-size: 13px; font-weight: 600; word-break: break-word; }
.progress-stats { margin-top: 5px; color: #667085; font-size: 12px; }
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; }
.toolbar input { flex: 1; }
ul.tree { list-style: none; padding-left: 0; margin: 0; }
ul.tree ul { list-style: none; padding-left: 22px; margin: 5px 0; }
.node { display: flex; gap: 8px; align-items: center; justify-content: space-between; padding: 7px 8px; border-radius: 8px; }
.node-main { display: flex; gap: 8px; align-items: center; min-width: 0; }
button.mini { padding: 6px 9px; font-size: 12px; white-space: nowrap; }
.node-actions { display: flex; gap: 6px; }
.review-row { display: flex; gap: 10px; align-items: center; padding: 8px 4px; border-bottom: 1px solid #eef0f3; }
.review-row label { margin: 0; flex: 1; }
.review-row input { width: auto; }
.node:hover { background: #f6f7f9; }
.badge { font-size: 11px; padding: 2px 6px; border-radius: 999px; background: #eef0f3; color: #475467; }
.badge.off { background: #fff1f1; color: #b42318; }
.path { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: #667085; word-break: break-all; }
.full { grid-column: 1 / -1; }
@media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<main>
  <h1>spec-trace</h1>
  <p class="lead">Notion 기획 원문 수집, 변경 추적, 로컬 문서 작업을 한 화면에서 관리합니다.</p>
  <div class="grid">
    <section class="card">
      <h2>전체 최신화</h2>
      <p>메뉴 동기화 → 실패한 projection 복구 → AVAILABLE 문서 수집 → 기획자 답변 회수 순서로 실행합니다.</p>
      <div class="actions"><button id="cycle">전체 최신화</button></div>
      <div id="cycleProgress" class="cycle-progress" hidden>
        <div class="progress-head">
          <strong id="cyclePhase"></strong>
          <span id="cycleCount"></span>
        </div>
        <div class="progress-track"><div id="cycleBar" class="progress-bar"></div></div>
        <div id="cycleCurrent" class="progress-current"></div>
        <div id="cycleStats" class="progress-stats"></div>
      </div>
      <div id="cycleStatus" class="status"></div>
    </section>
    <section class="card">
      <h2>로컬 문서 저장</h2>
      <p>수집된 Notion 원문을 Markdown으로 저장할 위치를 지정하고, 현재 Snapshot을 한 번에 내보냅니다.</p>
      <label for="exportRoot">저장 위치</label>
      <div class="input-row">
        <input id="exportRoot" placeholder="/home/.../PEOPLO/docs/PRD_Notion">
        <button class="secondary" id="chooseExport" type="button">폴더 선택</button>
      </div>
      <div class="path" id="suggestion"></div>
      <div class="path" id="savedExportRoot"></div>
      <div class="actions">
        <button class="secondary" id="saveExportRoot">저장 위치 적용</button>
        <button id="exportAll">수집된 문서 전체 저장</button>
      </div>
      <div id="exportStatus" class="status"></div>
    </section>
    <section class="card full">
      <h2>Notion 원본 설정</h2>
      <div class="grid">
        <div><label for="databaseId">Database ID</label><input id="databaseId"></div>
        <div><label for="dataSourceId">Data Source ID</label><input id="dataSourceId"></div>
      </div>
      <label for="parentProperty">상위 메뉴 relation 속성</label>
      <input id="parentProperty" value="상위 항목">
      <div class="actions"><button id="saveSource">저장</button></div>
      <div id="sourceStatus" class="status"></div>
    </section>
    <section class="card full">
      <h2>Notion 문서</h2>
      <div class="toolbar"><input id="search" placeholder="문서 제목 검색"><button class="secondary" id="refresh">새로고침</button></div>
      <div id="tree"></div>
      <div id="documentStatus" class="status"></div>
    </section>
    <section class="card full" id="reviewPanel" style="display:none">
      <h2 id="reviewTitle">로컬 검토 문서</h2>
      <div class="path" id="reviewDirectory"></div>
      <div id="reviewDocuments"></div>
      <div class="actions">
        <button id="publishSelected">선택 게시</button>
        <button class="secondary" id="collectResponses">기획자 답변 확인</button>
      </div>
      <div id="reviewStatus" class="status"></div>
    </section>
  </div>
</main>
<dialog id="folderDialog">
  <div class="dialog-body">
    <div class="dialog-head">
      <h3>문서 저장 폴더 선택</h3>
      <button class="secondary mini" id="closeFolderDialog" type="button">닫기</button>
    </div>
    <div class="path" id="folderPath"></div>
    <div class="folder-list" id="folderList"></div>
    <div id="folderStatus" class="status"></div>
    <div class="dialog-actions">
      <button class="secondary" id="folderUp" type="button">상위 폴더</button>
      <button id="selectFolder" type="button">이 폴더 선택</button>
    </div>
  </div>
</dialog>
<script>
let state = null;
let folderState = null;
let cyclePolling = false;
const $ = (id) => document.getElementById(id);
async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '요청에 실패했습니다.');
  return body;
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
  $('suggestion').textContent = !configuredExportRoot && state.suggested_export_root
    ? `자동 제안: ${state.suggested_export_root}`
    : '';
  $('savedExportRoot').textContent = configuredExportRoot
    ? `현재 적용됨: ${configuredExportRoot}`
    : '현재 적용된 저장 위치가 없습니다.';
  renderTree();
  await restoreCycleState();
}

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

function renderCycleProgress(job) {
  const progress = job.progress || {};
  const panel = $('cycleProgress');
  if (!progress.phase) {
    panel.hidden = true;
    return;
  }
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

  const labels = {
    CHECKING: '변경 확인 중',
    COLLECTING: '원문 수집 중',
    SKIPPED_UNCHANGED: '변경 없음',
    SNAPSHOT_CREATED: '수집 완료',
    UNCHANGED: '수집 완료 · 내용 동일',
    SOURCE_UNAVAILABLE: '원문 없음',
    SOURCE_UNSTABLE: '수집 불안정',
    COLLECTION_FAILED: '수집 실패',
  };
  let itemDuration = progress.duration_seconds;
  if (['CHECKING', 'COLLECTING'].includes(progress.item_status)) {
    itemDuration = liveDuration(progress.item_started_at, itemDuration || 0);
  }
  const currentParts = [];
  if (progress.title) {
    currentParts.push(`현재 ${progress.current ?? '-'} / ${progress.total ?? '-'} · ${progress.title}`);
  }
  if (progress.item_status) currentParts.push(labels[progress.item_status] || progress.item_status);
  if (itemDuration != null && progress.item_status) currentParts.push(formatDuration(itemDuration));
  $('cycleCurrent').textContent = currentParts.join(' · ');

  const elapsed = liveDuration(job.started_at, progress.elapsed_seconds || 0);
  if (total > 0) {
    $('cycleStats').textContent = `변경 없음 ${progress.skipped || 0}개 · 수집 ${progress.collected || 0}개 · 실패 ${progress.failed || 0}개 · 경과 ${formatDuration(elapsed)}`;
  } else {
    $('cycleStats').textContent = `경과 ${formatDuration(elapsed)}`;
  }
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
  if (job.status === 'FAILED') {
    $('cycle').disabled = false;
    $('cycleStatus').textContent = `실패: ${job.error || '전체 최신화에 실패했습니다.'}`;
    return;
  }
  if (job.status === 'COMPLETED') {
    $('cycle').disabled = false;
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
    if (job.status === 'FAILED') {
      throw new Error(job.error || '전체 최신화에 실패했습니다.');
    }
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

function nodeHtml(node) {
  const badge = node.source_status === 'AVAILABLE'
    ? '<span class="badge">AVAILABLE</span>'
    : '<span class="badge off">UNAVAILABLE</span>';
  const action = node.source_status === 'AVAILABLE'
    ? `<div class="node-actions"><button class="secondary mini" onclick="exportDocument('${node.planning_document_id}')">로컬에 저장</button><button class="secondary mini" onclick="openReviewDocuments('${node.planning_document_id}')">검토 문서</button></div>`
    : '';
  const children = (node.children || []).map(nodeHtml).join('');
  return `<li data-title="${escapeHtml(node.title.toLowerCase())}"><div class="node"><div class="node-main"><span>${escapeHtml(node.title)}</span>${badge}</div>${action}</div>${children ? `<ul>${children}</ul>` : ''}</li>`;
}
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
}
function renderTree() {
  const q = $('search').value.trim().toLowerCase();
  const filter = (node) => {
    const children = (node.children || []).map(filter).filter(Boolean);
    if (!q || node.title.toLowerCase().includes(q) || children.length) return {...node, children};
    return null;
  };
  const nodes = (state.documents || []).map(filter).filter(Boolean);
  $('tree').innerHTML = nodes.length ? `<ul class="tree">${nodes.map(nodeHtml).join('')}</ul>` : '<div class="status">표시할 문서가 없습니다.</div>';
}
async function exportDocument(id) {
  $('documentStatus').textContent = '선택한 문서를 최신 수집하고 Markdown으로 저장하는 중…';
  try {
    const result = await api('/api/export', {method:'POST', body:JSON.stringify({planning_document_id:id})});
    $('documentStatus').textContent = `저장됨: ${result.export.path}`;
    await load();
  } catch (e) { $('documentStatus').textContent = e.message; }
}
window.exportDocument = exportDocument;
let selectedReviewDocumentId = null;
async function openReviewDocuments(id) {
  selectedReviewDocumentId = id;
  $('reviewPanel').style.display = 'block';
  const title = findNodeTitle(state.documents || [], id) || id;
  $('reviewTitle').textContent = `${title} · 로컬 검토 문서`;
  $('reviewStatus').textContent = '로컬 Markdown을 확인하는 중…';
  try {
    const result = await api('/api/local-documents', {method:'POST', body:JSON.stringify({planning_document_id:id})});
    $('reviewDirectory').textContent = result.directory;
    $('reviewDocuments').innerHTML = result.documents.length
      ? result.documents.map((doc) => {
          const state = doc.published ? '게시됨' : (doc.dirty ? '수정됨' : '미게시');
          return `<div class="review-row"><input type="checkbox" class="review-check" value="${escapeHtml(doc.path)}"><label>${escapeHtml(doc.path)}</label><span class="badge">${state}</span></div>`;
        }).join('')
      : '<div class="status">게시할 로컬 Markdown이 없습니다.</div>';
    $('reviewStatus').textContent = '';
  } catch (e) { $('reviewStatus').textContent = e.message; }
}
function findNodeTitle(nodes, id) {
  for (const node of nodes) {
    if (node.planning_document_id === id) return node.title;
    const nested = findNodeTitle(node.children || [], id);
    if (nested) return nested;
  }
  return null;
}
window.openReviewDocuments = openReviewDocuments;
$('publishSelected').addEventListener('click', async () => {
  if (!selectedReviewDocumentId) return;
  const paths = [...document.querySelectorAll('.review-check:checked')].map((el) => el.value);
  try {
    const result = await api('/api/publish', {method:'POST', body:JSON.stringify({planning_document_id:selectedReviewDocumentId, paths})});
    $('reviewStatus').textContent = result.map((item) => `${item.status}: ${item.path}`).join('\n');
    await openReviewDocuments(selectedReviewDocumentId);
  } catch (e) { $('reviewStatus').textContent = e.message; }
});
$('collectResponses').addEventListener('click', async () => {
  if (!selectedReviewDocumentId) return;
  try {
    const result = await api('/api/responses', {method:'POST', body:JSON.stringify({planning_document_id:selectedReviewDocumentId})});
    $('reviewStatus').textContent = result.length ? result.map((item) => `회수됨: ${item.path}`).join('\n') : '새 기획자 답변이 없습니다.';
  } catch (e) { $('reviewStatus').textContent = e.message; }
});
$('chooseExport').addEventListener('click', openFolderPicker);
$('closeFolderDialog').addEventListener('click', () => $('folderDialog').close());
$('folderUp').addEventListener('click', () => { if (folderState?.parent) browseFolders(folderState.parent); });
$('selectFolder').addEventListener('click', () => {
  if (!folderState?.path) return;
  $('exportRoot').value = folderState.path;
  $('folderDialog').close();
  $('exportStatus').textContent = '폴더를 선택했습니다. 저장 위치 적용 또는 전체 저장을 눌러 반영하세요.';
});
$('search').addEventListener('input', renderTree);
$('refresh').addEventListener('click', load);
async function applyExportRoot() {
  const exportRoot = $('exportRoot').value.trim();
  if (!exportRoot) throw new Error('저장 위치를 입력하거나 폴더를 선택하세요.');
  const result = await api('/api/settings', {
    method:'POST',
    body:JSON.stringify({export_root:exportRoot, create_export_root:false}),
  });
  return result.settings.export_root;
}
$('saveExportRoot').addEventListener('click', async () => {
  try {
    const exportRoot = await applyExportRoot();
    $('savedExportRoot').textContent = `현재 적용됨: ${exportRoot}`;
    $('exportStatus').textContent = `저장 위치를 적용했습니다: ${exportRoot}`;
    await load();
  } catch (e) { $('exportStatus').textContent = e.message; }
});
$('exportAll').addEventListener('click', async () => {
  const button = $('exportAll');
  button.disabled = true;
  try {
    const exportRoot = await applyExportRoot();
    $('savedExportRoot').textContent = `현재 적용됨: ${exportRoot}`;
    $('exportStatus').textContent = '수집된 문서를 현재 Snapshot 기준으로 저장하는 중…';
    const result = await api('/api/export-all', {method:'POST', body:'{}'});
    const firstFailure = (result.failures || [])[0];
    const failureDetail = firstFailure
      ? `\n첫 실패: ${firstFailure.title || firstFailure.planning_document_id} · ${firstFailure.error}`
      : '';
    $('exportStatus').textContent = `전체 저장 완료: ${result.exported}/${result.total}개 · 실패 ${result.failed}개\n저장 위치: ${result.root}${failureDetail}`;
    await load();
  } catch (e) {
    $('exportStatus').textContent = `전체 저장 실패: ${e.message}`;
  } finally {
    button.disabled = false;
  }
});
$('saveSource').addEventListener('click', async () => {
  try {
    await api('/api/settings', {method:'POST', body:JSON.stringify({database_id:$('databaseId').value, data_source_id:$('dataSourceId').value, parent_property:$('parentProperty').value})});
    $('sourceStatus').textContent = 'Notion 원본 설정을 저장했습니다.';
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
load().catch((e) => { $('tree').innerHTML = `<div class="status">${escapeHtml(e.message)}</div>`; });
</script>
</body>
</html>"""


class WebApplication:
    def __init__(
        self,
        workspace: Workspace,
        *,
        notion_factory: Callable[[], NotionPort] = NotionCliClient.from_environment,
        browse_root: Path | None = None,
        cycle_runner: Callable[[], dict[str, Any]] | None = None,
    ):
        self.workspace = workspace
        self.settings = SettingsService(workspace)
        self.notion_factory = notion_factory
        self.browse_root = (browse_root or self._default_browse_root()).resolve()
        self.cycle_runner = cycle_runner
        self._cycle_guard = threading.Lock()
        self._cycle_job: dict[str, Any] = {
            "status": "IDLE",
            "result": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            "progress": None,
        }

    def state(self) -> dict[str, Any]:
        settings = self.settings.load()
        return {
            "settings": settings.to_dict(),
            "suggested_export_root": self.settings.suggested_export_root(),
            "browse_root": str(self.browse_root),
            "documents": self._document_tree(),
        }

    def browse_directories(self, path: str | None = None) -> dict[str, Any]:
        root = self.browse_root
        if not root.exists() or not root.is_dir():
            raise ValidationError(f"filesystem browse root is not a directory: {root}")

        requested = str(path or "").strip()
        candidate = Path(requested).expanduser() if requested else root
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            current = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ValidationError(f"directory does not exist: {candidate}") from exc
        if not current.is_dir():
            raise ValidationError(f"path is not a directory: {current}")
        if current != root and not current.is_relative_to(root):
            raise ValidationError(f"directory is outside browse root: {root}")

        directories: list[dict[str, str]] = []
        try:
            children = sorted(
                current.iterdir(), key=lambda value: value.name.casefold()
            )
        except OSError as exc:
            raise ValidationError(f"cannot read directory: {current}") from exc
        for child in children:
            if child.name.startswith("."):
                continue
            try:
                resolved = child.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_dir():
                continue
            if resolved != root and not resolved.is_relative_to(root):
                continue
            directories.append({"name": child.name, "path": str(resolved)})

        parent: str | None = None
        if current != root:
            resolved_parent = current.parent.resolve()
            if resolved_parent == root or resolved_parent.is_relative_to(root):
                parent = str(resolved_parent)
        return {
            "root": str(root),
            "path": str(current),
            "parent": parent,
            "directories": directories,
        }

    def _default_browse_root(self) -> Path:
        workspace_parent = self.workspace.root.parent
        if workspace_parent.name == "workspaces" and workspace_parent.is_dir():
            return workspace_parent
        home_workspaces = Path.home() / "workspaces"
        if home_workspaces.is_dir():
            return home_workspaces
        return Path.home()

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        database_id = str(payload.get("database_id") or "").strip()
        data_source_id = str(payload.get("data_source_id") or "").strip()
        if database_id or data_source_id:
            if not database_id or not data_source_id:
                raise ValidationError("Database ID와 Data Source ID를 함께 입력하세요")
            self.settings.set_notion_source(
                database_id,
                data_source_id,
                parent_property=str(payload.get("parent_property") or "상위 항목"),
            )

        if "export_root" in payload:
            export_root = str(payload.get("export_root") or "").strip()
            if not export_root:
                self.settings.clear_export_root()
            else:
                self.settings.set_export_root(
                    export_root,
                    create=bool(payload.get("create_export_root", False)),
                )
        return {"settings": self.settings.load().to_dict()}

    def start_cycle(self) -> dict[str, Any]:
        with self._cycle_guard:
            if self._cycle_job["status"] == "RUNNING":
                return dict(self._cycle_job)
            self._cycle_job = {
                "status": "RUNNING",
                "result": None,
                "error": None,
                "started_at": utc_now(),
                "finished_at": None,
                "progress": {
                    "phase": "starting",
                    "phase_label": "전체 최신화 시작",
                    "state": "RUNNING",
                },
            }
            thread = threading.Thread(
                target=self._run_cycle_job,
                name="spec-trace-cycle",
                daemon=True,
            )
            thread.start()
            return dict(self._cycle_job)

    def cycle_status(self) -> dict[str, Any]:
        with self._cycle_guard:
            payload = dict(self._cycle_job)
            if isinstance(payload.get("progress"), dict):
                payload["progress"] = dict(payload["progress"])
            return payload

    def _update_cycle_progress(self, progress: dict[str, Any]) -> None:
        with self._cycle_guard:
            if self._cycle_job["status"] != "RUNNING":
                return
            self._cycle_job["progress"] = dict(progress)

    def _run_cycle_job(self) -> None:
        logger.info("cycle job started")
        try:
            result = self.run_cycle()
        except SpecTraceError as exc:
            logger.exception("cycle job failed")
            with self._cycle_guard:
                self._cycle_job = {
                    "status": "FAILED",
                    "result": None,
                    "error": str(exc),
                    "started_at": self._cycle_job.get("started_at"),
                    "finished_at": utc_now(),
                    "progress": self._cycle_job.get("progress"),
                }
        except Exception as exc:
            logger.exception("cycle job failed with unexpected error")
            with self._cycle_guard:
                self._cycle_job = {
                    "status": "FAILED",
                    "result": None,
                    "error": f"internal error: {type(exc).__name__}",
                    "started_at": self._cycle_job.get("started_at"),
                    "finished_at": utc_now(),
                    "progress": self._cycle_job.get("progress"),
                }
        else:
            logger.info("cycle job completed")
            with self._cycle_guard:
                self._cycle_job = {
                    "status": "COMPLETED",
                    "result": result,
                    "error": None,
                    "started_at": self._cycle_job.get("started_at"),
                    "finished_at": utc_now(),
                    "progress": self._cycle_job.get("progress"),
                }

    def run_cycle(self) -> dict[str, Any]:
        if self.cycle_runner is not None:
            return self.cycle_runner()
        notion = self.notion_factory()
        with WorkspaceLock(self.workspace):
            return RuntimeService(
                self.workspace,
                notion,
                progress_callback=self._update_cycle_progress,
            ).run_cycle()

    def export_document(self, planning_document_id: str) -> dict[str, Any]:
        document_id = str(planning_document_id or "").strip()
        if not document_id:
            raise ValidationError("planning_document_id is required")
        logger.info("document export started document=%s", document_id)
        notion = self.notion_factory()
        with WorkspaceLock(self.workspace):
            collection = RuntimeService(self.workspace, notion).collect_document(
                document_id
            )
            if collection["status"] in {
                "SOURCE_UNAVAILABLE",
                "SOURCE_UNSTABLE",
                "COLLECTION_FAILED",
            }:
                message = (
                    "cannot export document after collection status "
                    f"{collection['status']}"
                )
                if collection.get("failure_detail"):
                    message += f": {collection['failure_detail']}"
                raise ValidationError(message)
            exported = SourceExportService(self.workspace).export(document_id)
        logger.info(
            "document export completed document=%s path=%s pages=%s",
            document_id,
            exported["path"],
            exported["pages"],
        )
        return {"collection": collection, "export": exported}

    def export_all_documents(self) -> dict[str, Any]:
        root = self.settings.resolve_export_root()
        connection = self.workspace.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT planning_document_id, title
                FROM planning_documents
                WHERE current_snapshot_id IS NOT NULL
                ORDER BY title, planning_document_id
                """
            ).fetchall()
        finally:
            connection.close()

        export_service = SourceExportService(self.workspace)
        exports: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        logger.info("bulk export started root=%s documents=%d", root, len(rows))
        with WorkspaceLock(self.workspace):
            for row in rows:
                document_id = row["planning_document_id"]
                title = row["title"]
                try:
                    exported = export_service.export(document_id, output_root=root)
                except SpecTraceError as exc:
                    logger.warning(
                        "bulk export failed document=%s title=%r error=%s",
                        document_id,
                        title,
                        exc,
                    )
                    failures.append(
                        {
                            "planning_document_id": document_id,
                            "title": title,
                            "error": str(exc),
                        }
                    )
                    continue
                except Exception as exc:
                    logger.exception(
                        "bulk export failed with unexpected error document=%s title=%r",
                        document_id,
                        title,
                    )
                    failures.append(
                        {
                            "planning_document_id": document_id,
                            "title": title,
                            "error": f"internal error: {type(exc).__name__}",
                        }
                    )
                    continue
                exports.append(exported)

        logger.info(
            "bulk export completed root=%s exported=%d failed=%d",
            root,
            len(exports),
            len(failures),
        )
        return {
            "root": str(root),
            "total": len(rows),
            "exported": len(exports),
            "failed": len(failures),
            "exports": exports,
            "failures": failures,
        }

    def local_documents(self, planning_document_id: str) -> dict[str, Any]:
        document_id = str(planning_document_id or "").strip()
        if not document_id:
            raise ValidationError("planning_document_id is required")
        return ReviewDocumentService(self.workspace).list_local(document_id)

    def publish_documents(
        self, planning_document_id: str, paths: list[str]
    ) -> list[dict[str, Any]]:
        document_id = str(planning_document_id or "").strip()
        if not document_id:
            raise ValidationError("planning_document_id is required")
        notion = self.notion_factory()
        with WorkspaceLock(self.workspace):
            return ReviewDocumentService(self.workspace, notion).publish(
                document_id, [str(path) for path in paths]
            )

    def collect_review_responses(
        self, planning_document_id: str
    ) -> list[dict[str, Any]]:
        document_id = str(planning_document_id or "").strip()
        if not document_id:
            raise ValidationError("planning_document_id is required")
        notion = self.notion_factory()
        with WorkspaceLock(self.workspace):
            return ReviewDocumentService(self.workspace, notion).collect_responses(
                document_id
            )

    def _document_tree(self) -> list[dict[str, Any]]:
        connection = self.workspace.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT planning_document_id, root_notion_page_id, title,
                       source_status, current_snapshot_id,
                       menu_parent_notion_page_id, last_collected_at
                FROM planning_documents
                ORDER BY title, planning_document_id
                """
            ).fetchall()
        finally:
            connection.close()

        nodes: dict[str, dict[str, Any]] = {}
        for row in rows:
            nodes[row["root_notion_page_id"]] = {
                "planning_document_id": row["planning_document_id"],
                "notion_page_id": row["root_notion_page_id"],
                "title": row["title"],
                "source_status": row["source_status"],
                "current_snapshot_id": row["current_snapshot_id"],
                "last_collected_at": row["last_collected_at"],
                "parent_notion_page_id": row["menu_parent_notion_page_id"],
                "children": [],
            }

        roots: list[dict[str, Any]] = []
        for node in nodes.values():
            parent_id = node["parent_notion_page_id"]
            parent = nodes.get(parent_id) if parent_id else None
            if parent is None:
                roots.append(node)
            else:
                parent["children"].append(node)

        def sort_tree(values: list[dict[str, Any]]) -> None:
            values.sort(key=lambda value: (value["title"], value["notion_page_id"]))
            for value in values:
                sort_tree(value["children"])

        sort_tree(roots)
        return roots


class RequestHandler(BaseHTTPRequestHandler):
    app: WebApplication

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_text(HTML, "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send_no_content()
            return
        if path == "/api/state":
            self._handle_json(self.app.state)
            return
        if path == "/api/cycle":
            self._handle_json(self.app.cycle_status)
            return
        if path == "/api/directories":
            query = parse_qs(parsed.query)
            requested = (query.get("path") or [""])[0]
            self._handle_json(lambda: self.app.browse_directories(requested))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/settings":
            self._handle_json(lambda: self.app.save_settings(self._read_json()))
            return
        if path == "/api/cycle":
            self._handle_json(self.app.start_cycle)
            return
        if path == "/api/export":
            payload = self._read_json()
            self._handle_json(
                lambda: self.app.export_document(
                    payload.get("planning_document_id", "")
                )
            )
            return
        if path == "/api/export-all":
            self._handle_json(self.app.export_all_documents)
            return
        if path == "/api/local-documents":
            payload = self._read_json()
            self._handle_json(
                lambda: self.app.local_documents(
                    payload.get("planning_document_id", "")
                )
            )
            return
        if path == "/api/publish":
            payload = self._read_json()
            paths = payload.get("paths") or []
            if not isinstance(paths, list):
                raise ValidationError("paths must be a JSON array")
            self._handle_json(
                lambda: self.app.publish_documents(
                    payload.get("planning_document_id", ""), paths
                )
            )
            return
        if path == "/api/responses":
            payload = self._read_json()
            self._handle_json(
                lambda: self.app.collect_review_responses(
                    payload.get("planning_document_id", "")
                )
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        path = urlparse(self.path).path
        if self.command == "GET" and path == "/api/cycle":
            return
        logger.info(
            "http method=%s path=%s %s",
            self.command,
            self.path,
            format % args,
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > 1_000_000:
            raise ValidationError("request body is too large")
        if length == 0:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValidationError("request body must be a JSON object")
        return value

    def _handle_json(self, action: Callable[[], Any]) -> None:
        try:
            self._send_json(action(), HTTPStatus.OK)
        except SpecTraceError as exc:
            logger.warning(
                "request failed method=%s path=%s error=%s",
                self.command,
                self.path,
                exc,
            )
            self._send_json(
                {"error": str(exc), "code": exc.exit_code},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception(
                "request failed method=%s path=%s",
                self.command,
                self.path,
            )
            self._send_json(
                {"error": "internal error"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _send_json(self, value: Any, status: HTTPStatus) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_no_content(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_text(self, value: str, content_type: str) -> None:
        payload = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(workspace: Workspace, port: int = 8788, *, open_browser: bool = True) -> None:
    if not 1 <= port <= 65535:
        raise ValidationError("port must be between 1 and 65535")
    workspace.initialize()
    with WorkspaceLock(workspace):
        RuntimeService.recover_interrupted_collections(workspace)
    app = WebApplication(workspace)
    handler = type("SpecTraceRequestHandler", (RequestHandler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}"
    logger.info("server started url=%s workspace=%s", url, workspace.root)
    if open_browser:
        threading.Timer(0.15, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spec-trace-web")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_parser().parse_args(argv)
    workspace = Workspace(Path(args.workspace).resolve())
    try:
        serve(workspace, args.port, open_browser=not args.no_browser)
        return 0
    except SpecTraceError as exc:
        print(str(exc))
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
