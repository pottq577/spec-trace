from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .analysis import AnalysisService
from .collector import SourceCollector
from .errors import SpecTraceError, ValidationError
from .notion import NotionHttpClient
from .planning_documents import PlanningDocumentService
from .repositories import RepositoryService
from .workspace import Workspace, WorkspaceLock


def _workspace(args: argparse.Namespace) -> Workspace:
    return Workspace(Path(args.workspace).resolve())


def _emit(args: argparse.Namespace, result: Any) -> None:
    if args.json:
        print(json.dumps({"ok": True, "code": 0, "result": result, "errors": []}, ensure_ascii=False))
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

    document = sub.add_parser("document")
    document_sub = document.add_subparsers(dest="document_command", required=True)
    document_register = document_sub.add_parser("register")
    document_register.add_argument("--database-id", required=True)
    document_register.add_argument("--page-id", required=True)

    collect = sub.add_parser("collect")
    collect.add_argument("--document", required=True)

    analysis = sub.add_parser("analysis")
    analysis_sub = analysis.add_subparsers(dest="analysis_command", required=True)
    analysis_export = analysis_sub.add_parser("export")
    analysis_export.add_argument("--type", required=True, choices=["source-diff", "impact", "review"])
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
    proposal_review.add_argument("--action", required=True, choices=["adopt", "edit-and-adopt", "reject"])
    proposal_review.add_argument("--payload")
    proposal_review.add_argument("--reason")
    proposal_review.add_argument("--reviewer", default="developer")
    return parser


def run(args: argparse.Namespace) -> Any:
    workspace = _workspace(args)
    if args.command == "init":
        workspace.initialize()
        return {"workspace": str(workspace.root), "database": str(workspace.database_path)}

    workspace.initialize()
    if args.command == "repo":
        service = RepositoryService(workspace.database, workspace.root)
        if args.repo_command == "add":
            with WorkspaceLock(workspace):
                record = service.add(args.name, args.path)
            return record.__dict__
        if args.repo_command == "list":
            return [record.__dict__ for record in service.list()]
    if args.command == "document":
        notion = NotionHttpClient.from_environment()
        service = PlanningDocumentService(workspace.database, notion)
        if args.document_command == "register":
            with WorkspaceLock(workspace):
                record = service.register(args.database_id, args.page_id)
            return record.__dict__
    if args.command == "collect":
        notion = NotionHttpClient.from_environment()
        service = SourceCollector(workspace.database, workspace.content_store, notion)
        with WorkspaceLock(workspace):
            result = service.collect(args.document)
        return result.__dict__
    if args.command in {"analysis", "proposal"}:
        service = AnalysisService(
            workspace.database, workspace.content_store, workspace.root, workspace.analysis_requests_dir
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
                reviewed_payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
            with WorkspaceLock(workspace):
                return service.review_candidate(
                    args.proposal, args.candidate, args.action, reviewer=args.reviewer,
                    reviewed_payload=reviewed_payload, reason=args.reason
                )
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
        _emit(args, result)
        return 0
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
