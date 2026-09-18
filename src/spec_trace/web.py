from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .config import SettingsService
from .errors import SpecTraceError, ValidationError
from .notion import NotionCliClient, NotionPort
from .runtime import RuntimeService
from .review_documents import ReviewDocumentService
from .source_export import SourceExportService
from .workspace import Workspace, WorkspaceLock


HTML = r'''<!doctype html>
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
.actions { display: flex; gap: 8px; margin-top: 14px; align-items: center; }
.status { min-height: 22px; margin-top: 12px; color: #475467; font-size: 13px; white-space: pre-wrap; }
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
      <div id="cycleStatus" class="status"></div>
    </section>
    <section class="card">
      <h2>로컬 문서 저장 위치</h2>
      <label for="exportRoot">문서 저장 루트</label>
      <input id="exportRoot" placeholder="/home/.../PEOPLO/docs/PRD_Notion">
      <div class="path" id="suggestion"></div>
      <div class="actions"><button id="saveExport">저장</button></div>
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
<script>
let state = null;
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
  $('exportRoot').value = settings.export_root || state.suggested_export_root || '';
  $('suggestion').textContent = state.suggested_export_root ? `자동 제안: ${state.suggested_export_root}` : '';
  renderTree();
}
function nodeHtml(node) {
  const badge = node.source_status === 'AVAILABLE'
    ? '<span class="badge">AVAILABLE</span>'
    : '<span class="badge off">UNAVAILABLE</span>';
  const action = node.source_status === 'AVAILABLE'
    ? `<div class="node-actions"><button class="secondary mini" onclick="exportDocument('${node.planning_document_id}')">원문 가져오기</button><button class="secondary mini" onclick="openReviewDocuments('${node.planning_document_id}')">검토 문서</button></div>`
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
$('search').addEventListener('input', renderTree);
$('refresh').addEventListener('click', load);
$('saveExport').addEventListener('click', async () => {
  try {
    const result = await api('/api/settings', {method:'POST', body:JSON.stringify({export_root:$('exportRoot').value, create_export_root:false})});
    $('exportStatus').textContent = `저장됨: ${result.settings.export_root}`;
    await load();
  } catch (e) { $('exportStatus').textContent = e.message; }
});
$('saveSource').addEventListener('click', async () => {
  try {
    await api('/api/settings', {method:'POST', body:JSON.stringify({database_id:$('databaseId').value, data_source_id:$('dataSourceId').value, parent_property:$('parentProperty').value})});
    $('sourceStatus').textContent = 'Notion 원본 설정을 저장했습니다.';
    await load();
  } catch (e) { $('sourceStatus').textContent = e.message; }
});
$('cycle').addEventListener('click', async () => {
  $('cycle').disabled = true;
  $('cycleStatus').textContent = '실행 중…';
  try {
    const result = await api('/api/cycle', {method:'POST', body:'{}'});
    const sync = result.source_sync || {};
    $('cycleStatus').textContent = `메뉴 ${sync.active_pages ?? 0}개 · 수집 ${result.collections.length}개 · 구조화 답변 ${result.answers.reduce((n,x)=>n+x.answers_collected,0)}건 · 문서 답변 ${result.review_responses.length}건`;
    await load();
  } catch (e) { $('cycleStatus').textContent = e.message; }
  finally { $('cycle').disabled = false; }
});
load().catch((e) => { $('tree').innerHTML = `<div class="status">${escapeHtml(e.message)}</div>`; });
</script>
</body>
</html>'''


class WebApplication:
    def __init__(
        self,
        workspace: Workspace,
        *,
        notion_factory: Callable[[], NotionPort] = NotionCliClient.from_environment,
    ):
        self.workspace = workspace
        self.settings = SettingsService(workspace)
        self.notion_factory = notion_factory

    def state(self) -> dict[str, Any]:
        settings = self.settings.load()
        return {
            "settings": settings.to_dict(),
            "suggested_export_root": self.settings.suggested_export_root(),
            "documents": self._document_tree(),
        }

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

    def run_cycle(self) -> dict[str, Any]:
        notion = self.notion_factory()
        with WorkspaceLock(self.workspace):
            return RuntimeService(self.workspace, notion).run_cycle()

    def export_document(self, planning_document_id: str) -> dict[str, Any]:
        document_id = str(planning_document_id or "").strip()
        if not document_id:
            raise ValidationError("planning_document_id is required")
        notion = self.notion_factory()
        with WorkspaceLock(self.workspace):
            collection = RuntimeService(self.workspace, notion).collect_document(document_id)
            if collection["status"] in {
                "SOURCE_UNAVAILABLE",
                "SOURCE_UNSTABLE",
                "COLLECTION_FAILED",
            }:
                raise ValidationError(
                    f"cannot export document after collection status {collection['status']}"
                )
            exported = SourceExportService(self.workspace).export(document_id)
        return {"collection": collection, "export": exported}

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
            return ReviewDocumentService(
                self.workspace, notion
            ).publish(document_id, [str(path) for path in paths])

    def collect_review_responses(
        self, planning_document_id: str
    ) -> list[dict[str, Any]]:
        document_id = str(planning_document_id or "").strip()
        if not document_id:
            raise ValidationError("planning_document_id is required")
        notion = self.notion_factory()
        with WorkspaceLock(self.workspace):
            return ReviewDocumentService(
                self.workspace, notion
            ).collect_responses(document_id)

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
        path = urlparse(self.path).path
        if path == "/":
            self._send_text(HTML, "text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._handle_json(self.app.state)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/settings":
            self._handle_json(lambda: self.app.save_settings(self._read_json()))
            return
        if path == "/api/cycle":
            self._read_json()
            self._handle_json(self.app.run_cycle)
            return
        if path == "/api/export":
            payload = self._read_json()
            self._handle_json(
                lambda: self.app.export_document(payload.get("planning_document_id", ""))
            )
            return
        if path == "/api/local-documents":
            payload = self._read_json()
            self._handle_json(
                lambda: self.app.local_documents(payload.get("planning_document_id", ""))
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
        return

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
            self._send_json({"error": str(exc), "code": exc.exit_code}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json(
                {"error": f"internal error: {type(exc).__name__}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _send_json(self, value: Any, status: HTTPStatus) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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
    app = WebApplication(workspace)
    handler = type("SpecTraceRequestHandler", (RequestHandler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}"
    print(f"spec-trace web: {url}")
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
