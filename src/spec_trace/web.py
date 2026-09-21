from __future__ import annotations

import argparse
import difflib
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
from .dashboard import HTML as DASHBOARD_HTML
from .dashboard import asset_text as dashboard_asset_text
from .errors import ResourceNotFound, SpecTraceError, ValidationError
from .notion import NotionCliClient, NotionPort
from .review_documents import ReviewDocumentService
from .runtime import RuntimeService
from .source_export import SourceExportService
from .util import utc_now
from .workspace import Workspace, WorkspaceLock

logger = logging.getLogger(__name__)


HTML = DASHBOARD_HTML


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

    def document_changes(self, planning_document_id: str) -> dict[str, Any]:
        document_id = str(planning_document_id or "").strip()
        if not document_id:
            raise ValidationError("planning_document_id is required")

        connection = self.workspace.database.connect()
        try:
            document = connection.execute(
                """
                SELECT planning_document_id, title
                FROM planning_documents
                WHERE planning_document_id = ?
                """,
                (document_id,),
            ).fetchone()
            if document is None:
                raise ResourceNotFound(
                    f"planning document not found: {document_id}"
                )

            change_sets = connection.execute(
                """
                SELECT cs.change_set_id, cs.baseline_snapshot_id,
                       cs.target_snapshot_id, cs.analysis_status, cs.created_at,
                       baseline.captured_at AS baseline_captured_at,
                       target.captured_at AS target_captured_at,
                       COUNT(pc.physical_change_id) AS change_count
                FROM change_sets cs
                JOIN planning_document_snapshots baseline
                  ON baseline.planning_document_snapshot_id = cs.baseline_snapshot_id
                JOIN planning_document_snapshots target
                  ON target.planning_document_snapshot_id = cs.target_snapshot_id
                LEFT JOIN physical_changes pc
                  ON pc.change_set_id = cs.change_set_id
                WHERE cs.planning_document_id = ?
                GROUP BY cs.change_set_id, cs.baseline_snapshot_id,
                         cs.target_snapshot_id, cs.analysis_status, cs.created_at,
                         baseline.captured_at, target.captured_at
                ORDER BY cs.created_at DESC, cs.change_set_id DESC
                """,
                (document_id,),
            ).fetchall()
            physical_changes = connection.execute(
                """
                SELECT pc.*, COALESCE(sp.title, pc.notion_page_id) AS page_title,
                       baseline_source.content_ref AS baseline_content_ref,
                       target_source.content_ref AS target_content_ref,
                       baseline_page.role AS baseline_role,
                       target_page.role AS target_role
                FROM physical_changes pc
                JOIN change_sets cs ON cs.change_set_id = pc.change_set_id
                LEFT JOIN source_pages sp
                  ON sp.planning_document_id = cs.planning_document_id
                 AND sp.notion_page_id = pc.notion_page_id
                LEFT JOIN source_page_snapshots baseline_source
                  ON baseline_source.source_page_snapshot_id =
                     pc.baseline_source_page_snapshot_id
                LEFT JOIN source_page_snapshots target_source
                  ON target_source.source_page_snapshot_id =
                     pc.target_source_page_snapshot_id
                LEFT JOIN planning_snapshot_pages baseline_page
                  ON baseline_page.planning_document_snapshot_id =
                     cs.baseline_snapshot_id
                 AND baseline_page.notion_page_id = pc.notion_page_id
                LEFT JOIN planning_snapshot_pages target_page
                  ON target_page.planning_document_snapshot_id =
                     cs.target_snapshot_id
                 AND target_page.notion_page_id = pc.notion_page_id
                WHERE cs.planning_document_id = ?
                ORDER BY cs.created_at DESC, cs.change_set_id DESC,
                         page_title, pc.notion_page_id, pc.change_type
                """,
                (document_id,),
            ).fetchall()
        finally:
            connection.close()

        by_change_set: dict[str, list[dict[str, Any]]] = {}
        renderer = SourceExportService(self.workspace)
        for row in physical_changes:
            payload = dict(row)
            change_set_id = payload.pop("change_set_id")
            baseline_ref = payload.pop("baseline_content_ref", None)
            target_ref = payload.pop("target_content_ref", None)
            payload["content_diff"] = []
            payload["content_diff_truncated"] = False
            payload["added_lines"] = 0
            payload["deleted_lines"] = 0
            if payload["change_type"] in {
                "PAGE_ADDED",
                "PAGE_REMOVED",
                "CONTENT_CHANGED",
            }:
                before = (
                    renderer.render_canonical_page(
                        self.workspace.content_store.read_json(baseline_ref)
                    )
                    if baseline_ref
                    else ""
                )
                after = (
                    renderer.render_canonical_page(
                        self.workspace.content_store.read_json(target_ref)
                    )
                    if target_ref
                    else ""
                )
                diff = list(
                    difflib.unified_diff(
                        before.splitlines(),
                        after.splitlines(),
                        fromfile="before",
                        tofile="after",
                        n=3,
                        lineterm="",
                    )
                )[2:]
                payload["added_lines"] = sum(1 for line in diff if line.startswith("+"))
                payload["deleted_lines"] = sum(1 for line in diff if line.startswith("-"))
                payload["content_diff_truncated"] = len(diff) > 800
                payload["content_diff"] = diff[:800]
            by_change_set.setdefault(change_set_id, []).append(payload)

        history: list[dict[str, Any]] = []
        for row in change_sets:
            payload = dict(row)
            payload["physical_changes"] = by_change_set.get(
                payload["change_set_id"], []
            )
            history.append(payload)

        return {
            "planning_document_id": document["planning_document_id"],
            "title": document["title"],
            "change_sets": history,
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
            latest_change_rows = connection.execute(
                """
                SELECT cs.planning_document_id, cs.change_set_id,
                       cs.analysis_status, cs.created_at,
                       COUNT(pc.physical_change_id) AS change_count
                FROM change_sets cs
                LEFT JOIN physical_changes pc
                  ON pc.change_set_id = cs.change_set_id
                WHERE cs.change_set_id = (
                    SELECT candidate.change_set_id
                    FROM change_sets candidate
                    WHERE candidate.planning_document_id = cs.planning_document_id
                    ORDER BY candidate.created_at DESC, candidate.change_set_id DESC
                    LIMIT 1
                )
                GROUP BY cs.planning_document_id, cs.change_set_id,
                         cs.analysis_status, cs.created_at
                """
            ).fetchall()
        finally:
            connection.close()

        latest_by_document = {
            row["planning_document_id"]: {
                "change_set_id": row["change_set_id"],
                "analysis_status": row["analysis_status"],
                "created_at": row["created_at"],
                "change_count": row["change_count"],
            }
            for row in latest_change_rows
        }
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
                "latest_change": latest_by_document.get(
                    row["planning_document_id"]
                ),
                "children": [],
            }

        for node in nodes.values():
            latest_change = node["latest_change"]
            node["needs_review"] = bool(
                latest_change and latest_change["analysis_status"] != "COMPLETED"
            )

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

        def annotate_subtree_changes(node: dict[str, Any]) -> dict[str, Any] | None:
            changed_documents = 1 if node["latest_change"] else 0
            review_documents = 1 if node["needs_review"] else 0
            latest_created_at = (
                node["latest_change"]["created_at"] if node["latest_change"] else None
            )
            for child in node["children"]:
                child_summary = annotate_subtree_changes(child)
                if child_summary is None:
                    continue
                changed_documents += child_summary["changed_documents"]
                review_documents += child_summary["review_documents"]
                child_latest = child_summary["latest_created_at"]
                if child_latest and (
                    latest_created_at is None or child_latest > latest_created_at
                ):
                    latest_created_at = child_latest
            if not changed_documents:
                node["subtree_change"] = None
                return None
            summary = {
                "changed_documents": changed_documents,
                "review_documents": review_documents,
                "latest_created_at": latest_created_at,
            }
            node["subtree_change"] = summary
            return summary

        sort_tree(roots)
        for root in roots:
            annotate_subtree_changes(root)
        return roots


class RequestHandler(BaseHTTPRequestHandler):
    app: WebApplication

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_text(HTML, "text/html; charset=utf-8")
            return
        if path == "/assets/dashboard.css":
            self._send_text(
                dashboard_asset_text("dashboard.css"),
                "text/css; charset=utf-8",
            )
            return
        if path == "/assets/dashboard.js":
            self._send_text(
                dashboard_asset_text("dashboard.js"),
                "text/javascript; charset=utf-8",
            )
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
        if path == "/api/changes":
            query = parse_qs(parsed.query)
            document_id = (query.get("planning_document_id") or [""])[0]
            self._handle_json(lambda: self.app.document_changes(document_id))
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
