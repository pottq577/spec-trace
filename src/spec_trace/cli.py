from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .analysis import AnalysisService
from .config import SettingsService
from .devflow import DevFlowService
from .errors import SpecTraceError, ValidationError
from .notion import NotionCliClient
from .planning_documents import PlanningDocumentService
from .projection import ProjectionService
from .repositories import RepositoryService
from .review import FinalSpecService, ReviewService
from .runtime import RuntimeService, StatusService
from .smoke import LiveSmokeService
from .workspace import Workspace, WorkspaceLock


def _workspace(args: argparse.Namespace) -> Workspace:
    return Workspace(Path(args.workspace).resolve())


def _emit(args: argparse.Namespace, result: Any) -> None:
    if args.json:
        print(
            json.dumps(
                {"ok": True, "code": 0, "result": result, "errors": []},
                ensure_ascii=False,
            )
        )
    elif isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spec-trace")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    repo = sub.add_parser("repo")
    repo_sub = repo.add_subparsers(dest="repo_command", required=True)
    repo_add = repo_sub.add_parser("add")
    repo_add.add_argument("--name", required=True)
    repo_add.add_argument("--path", required=True)
    repo_sub.add_parser("list")

    source = sub.add_parser("source")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_sync = source_sub.add_parser("sync")
    source_sync.add_argument("--database-id", required=True)
    source_sync.add_argument("--data-source-id", required=True)
    source_sync.add_argument("--parent-property", default="상위 항목")

    document = sub.add_parser("document")
    document_sub = document.add_subparsers(dest="document_command", required=True)
    document_register = document_sub.add_parser("register")
    document_register.add_argument("--database-id", required=True)
    document_register.add_argument("--page-id", required=True)

    collect = sub.add_parser("collect")
    collect_target = collect.add_mutually_exclusive_group(required=True)
    collect_target.add_argument("--document")
    collect_target.add_argument("--all", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("--document", required=True)

    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=300.0)
    watch.add_argument("--once", action="store_true")

    live_smoke = sub.add_parser("live-smoke")
    live_smoke.add_argument("--allow-write", action="store_true")

    analysis = sub.add_parser("analysis")
    analysis_sub = analysis.add_subparsers(dest="analysis_command", required=True)
    analysis_export = analysis_sub.add_parser("export")
    analysis_export.add_argument(
        "--type", required=True, choices=["source-diff", "impact", "review"]
    )
    analysis_export.add_argument("--change-set")
    analysis_export.add_argument("--document")
    analysis_import = analysis_sub.add_parser("import")
    analysis_import.add_argument("path")

    proposal = sub.add_parser("proposal")
    proposal_sub = proposal.add_subparsers(dest="proposal_command", required=True)
    proposal_show = proposal_sub.add_parser("show")
    proposal_show.add_argument("proposal_id")
    proposal_review = proposal_sub.add_parser("review")
    proposal_review.add_argument("--proposal", required=True)
    proposal_review.add_argument("--candidate", required=True)
    proposal_review.add_argument(
        "--action", required=True, choices=["adopt", "edit-and-adopt", "reject"]
    )
    proposal_review.add_argument("--payload")
    proposal_review.add_argument("--reason")
    proposal_review.add_argument("--reviewer", default="developer")

    decision = sub.add_parser("decision")
    decision_sub = decision.add_subparsers(dest="decision_command", required=True)
    decision_adopt = decision_sub.add_parser("adopt")
    decision_adopt.add_argument("--finding", required=True)
    decision_adopt.add_argument("--payload", required=True)

    question = sub.add_parser("question")
    question_sub = question.add_subparsers(dest="question_command", required=True)
    question_publish = question_sub.add_parser("publish")
    question_publish.add_argument("--finding", required=True)
    question_publish.add_argument("--payload", required=True)

    answer = sub.add_parser("answer")
    answer_sub = answer.add_subparsers(dest="answer_command", required=True)
    answer_verify = answer_sub.add_parser("verify")
    answer_verify.add_argument("--answer", required=True)
    answer_verify.add_argument("--payload")
    answer_verify.add_argument("--reopen", action="store_true")
    answer_verify.add_argument("--reason")

    blocker = sub.add_parser("blocker")
    blocker_sub = blocker.add_subparsers(dest="blocker_command", required=True)
    blocker_set = blocker_sub.add_parser("set")
    blocker_set.add_argument("--finding", required=True)
    blocker_set.add_argument("--payload", required=True)
    blocker_resolve = blocker_sub.add_parser("resolve")
    blocker_resolve.add_argument("blocker_id")
    blocker_resolve.add_argument("--reason")

    final_spec = sub.add_parser("final-spec")
    final_sub = final_spec.add_subparsers(dest="final_spec_command", required=True)
    final_create = final_sub.add_parser("create")
    final_create.add_argument("--document", required=True)
    final_create.add_argument("--content", required=True)

    sync = sub.add_parser("sync")
    sync.add_argument("--document", required=True)

    devflow = sub.add_parser("devflow")
    devflow_sub = devflow.add_subparsers(dest="devflow_command", required=True)
    devflow_export = devflow_sub.add_parser("export")
    devflow_export.add_argument("--revision", required=True)
    devflow_import = devflow_sub.add_parser("import")
    devflow_import.add_argument("path")
    return parser


def run(args: argparse.Namespace) -> Any:
    workspace = _workspace(args)
    if args.command == "init":
        workspace.initialize()
        return {
            "workspace": str(workspace.root),
            "database": str(workspace.database_path),
        }

    workspace.initialize()
    if args.command == "repo":
        service = RepositoryService(workspace.database, workspace.root)
        if args.repo_command == "add":
            with WorkspaceLock(workspace):
                record = service.add(args.name, args.path)
            return record.__dict__
        if args.repo_command == "list":
            return [record.__dict__ for record in service.list()]
    if args.command == "source":
        notion = NotionCliClient.from_environment()
        service = PlanningDocumentService(workspace.database, notion)
        if args.source_command == "sync":
            with WorkspaceLock(workspace):
                result = service.sync_data_source(
                    args.database_id,
                    args.data_source_id,
                    parent_property=args.parent_property,
                )
                SettingsService(workspace).set_notion_source(
                    args.database_id,
                    args.data_source_id,
                    parent_property=args.parent_property,
                )
            return result
    if args.command == "document":
        notion = NotionCliClient.from_environment()
        service = PlanningDocumentService(workspace.database, notion)
        if args.document_command == "register":
            with WorkspaceLock(workspace):
                record = service.register(args.database_id, args.page_id)
            return record.__dict__
    if args.command == "collect":
        notion = NotionCliClient.from_environment()
        service = RuntimeService(workspace, notion)
        with WorkspaceLock(workspace):
            if args.all:
                return service.collect_all()
            return service.collect_document(args.document)
    if args.command == "status":
        return StatusService(workspace).document(args.document)
    if args.command == "live-smoke":
        notion = NotionCliClient.from_environment()
        database_id = os.environ.get("SPEC_TRACE_LIVE_DATABASE_ID", "")
        page_id = os.environ.get("SPEC_TRACE_LIVE_PAGE_ID", "")
        if not database_id or not page_id:
            raise ValidationError(
                "SPEC_TRACE_LIVE_DATABASE_ID and SPEC_TRACE_LIVE_PAGE_ID are required"
            )
        service = LiveSmokeService(workspace, notion)
        with WorkspaceLock(workspace):
            return service.run(database_id, page_id, allow_write=args.allow_write)
    if args.command in {"analysis", "proposal"}:
        service = AnalysisService(
            workspace.database,
            workspace.content_store,
            workspace.root,
            workspace.analysis_requests_dir,
        )
        if args.command == "analysis" and args.analysis_command == "export":
            subject = args.document if args.type == "review" else args.change_set
            if not subject:
                parser_name = "--document" if args.type == "review" else "--change-set"
                raise ValidationError(f"{parser_name} is required for {args.type}")
            path = service.export_packet(args.type, subject)
            return {"path": str(path)}
        if args.command == "analysis" and args.analysis_command == "import":
            with WorkspaceLock(workspace):
                record = service.import_proposal(Path(args.path))
            return record.__dict__
        if args.command == "proposal" and args.proposal_command == "show":
            return service.show(args.proposal_id)
        if args.command == "proposal" and args.proposal_command == "review":
            reviewed_payload = None
            if args.payload:
                reviewed_payload = json.loads(
                    Path(args.payload).read_text(encoding="utf-8")
                )
            with WorkspaceLock(workspace):
                return service.review_candidate(
                    args.proposal,
                    args.candidate,
                    args.action,
                    reviewer=args.reviewer,
                    reviewed_payload=reviewed_payload,
                    reason=args.reason,
                )
    if args.command in {"decision", "question", "answer", "blocker"}:
        service = ReviewService(workspace.database)
        if args.command == "decision" and args.decision_command == "adopt":
            payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
            with WorkspaceLock(workspace):
                decision_id = service.adopt_developer_decision(args.finding, payload)
            return {"decision_id": decision_id}
        if args.command == "question" and args.question_command == "publish":
            payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
            with WorkspaceLock(workspace):
                question_id = service.publish_question(args.finding, payload)
            return {"open_question_id": question_id}
        if args.command == "answer" and args.answer_command == "verify":
            payload = {}
            if args.payload:
                payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
            if not args.reopen and not args.payload:
                raise ValidationError("--payload is required unless --reopen is used")
            with WorkspaceLock(workspace):
                decision_id = service.verify_answer(
                    args.answer, payload, reopen=args.reopen
                )
            return {"decision_id": decision_id, "reopened": bool(args.reopen)}
        if args.command == "blocker" and args.blocker_command == "set":
            payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
            with WorkspaceLock(workspace):
                blocker_id = service.set_blocker(args.finding, payload)
            return {"blocker_id": blocker_id}
        if args.command == "blocker" and args.blocker_command == "resolve":
            with WorkspaceLock(workspace):
                resume_work = service.resolve_blocker(args.blocker_id, args.reason)
            return {"blocker_id": args.blocker_id, "resume_work": resume_work}
    if args.command == "final-spec":
        service = FinalSpecService(workspace.database, workspace.content_store)
        with WorkspaceLock(workspace):
            revision_id = service.create(args.document, Path(args.content))
        return {"final_spec_revision_id": revision_id}
    if args.command == "sync":
        notion = NotionCliClient.from_environment()
        service = ProjectionService(workspace.database, notion)
        with WorkspaceLock(workspace):
            return service.sync(args.document)
    if args.command == "devflow":
        service = DevFlowService(
            workspace.database,
            workspace.content_store,
            workspace.root,
            workspace.exports_dir,
        )
        if args.devflow_command == "export":
            with WorkspaceLock(workspace):
                path = service.export(args.revision)
            return {"path": str(path)}
        if args.devflow_command == "import":
            with WorkspaceLock(workspace):
                implementation_ref_id = service.import_receipt(Path(args.path))
            return {"implementation_ref_id": implementation_ref_id}
    raise AssertionError("unreachable")


def _run_watch(args: argparse.Namespace) -> int:
    workspace = _workspace(args)
    workspace.initialize()
    notion = NotionCliClient.from_environment()
    service = RuntimeService(workspace, notion)
    interval = service.validate_interval(args.interval)
    while True:
        with WorkspaceLock(workspace):
            result = service.run_cycle()
        _emit(args, result)
        if args.once:
            return 0
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "watch":
            return _run_watch(args)
        result = run(args)
        _emit(args, result)
        return 0
    except KeyboardInterrupt:
        return 130
    except SpecTraceError as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "code": exc.exit_code,
                        "result": None,
                        "errors": [{"message": str(exc)}],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(str(exc), file=sys.stderr)
        return exc.exit_code
