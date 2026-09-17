---
meta:
  title: "첫 MVP의 이력을 SQLite에 어떻게 저장하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 첫 MVP의 이력을 SQLite에 어떻게 저장하는가

이 문서는 첫 MVP의 SQLite schema, content-addressed artifact 저장, unique constraint, transaction 경계를 정의한다. 저장 구조는 불변 이력을 보존하고 process restart 뒤 미완료 실행을 재개할 수 있어야 한다.

## 식별자와 시간 표현을 통일한다

모든 내부 ID는 lowercase UUID 문자열을 `TEXT`로 저장한다. application layer가 ID를 생성하고 database는 외부 ID를 내부 primary key로 사용하지 않는다.

시간은 UTC RFC 3339 문자열로 저장한다. enum 값은 문서에 정의한 대문자 문자열을 그대로 사용하고 schema migration으로만 값을 확장한다.

## database는 foreign key와 WAL mode를 사용한다

connection 초기화 시 다음 설정을 적용한다:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

하나의 write transaction은 하나의 도메인 확정 단위만 처리한다. Notion HTTP 호출과 Git subprocess는 database transaction 안에서 실행하지 않는다.

## 큰 원문과 문서 내용은 content-addressed store에 둔다

Notion canonical content, raw capture, analysis packet, FinalSpec 본문은 SQLite BLOB으로 반복 저장하지 않는다. workspace의 `.spec-trace/content` 아래에 SHA-256을 key로 저장한다.

경로 규칙은 다음과 같다:

```text
.spec-trace/content/ab/cd/abcdef...json
```

파일은 임시 경로에 쓴 뒤 hash를 검증하고 atomic rename한다. database의 `content_ref`는 workspace 상대 경로와 `content_hash`를 함께 가진다.

## source 계층은 Snapshot과 현재 mapping을 분리한다

원문 계층은 다음 table을 사용한다:

| table | 책임 | 주요 unique constraint |
| --- | --- | --- |
| `planning_documents` | 논리 기획과 현재 source 상태 | `notion_database_id, root_notion_page_id` |
| `source_pages` | Notion page와 내부 ID mapping | `planning_document_id, notion_page_id` |
| `source_page_snapshots` | page canonical content 불변 사본 | `source_page_id, content_hash` |
| `planning_document_snapshots` | 기획 전체 불변 버전 | `planning_document_id, aggregate_hash` |
| `planning_snapshot_pages` | Snapshot 시점의 page 포함·부모·role | `planning_snapshot_id, notion_page_id` |
| `source_references` | Snapshot 시점의 외부 page 참조 | reference tuple 전체 |

`source_pages.parent_source_page_id`는 현재 편의를 위한 값으로만 사용한다. 과거 부모 관계는 반드시 `planning_snapshot_pages`에서 읽는다.

## PlanningDocument는 현재 pointer만 mutable하다

`planning_documents`는 다음 mutable current field를 가진다:

- `title`
- `source_status`
- `current_snapshot_id`
- `last_collected_at`
- `attention_required`

과거 Snapshot은 current pointer 변경과 무관하게 유지한다. `current_snapshot_id`는 새 Snapshot transaction의 마지막 단계에서만 갱신한다.

## collection 실행 이력을 별도 보존한다

`collection_runs`는 실행 결과와 재시도 상태를 저장한다. 주요 필드는 다음과 같다:

- `collection_run_id`
- `planning_document_id`
- `trigger_type`
- `status`
- `baseline_snapshot_id`
- `created_snapshot_id`
- `attempt`
- `failure_code`
- `retryable`
- `started_at`
- `completed_at`

같은 document에 활성 run이 둘 생기지 않도록 application lock과 partial unique index를 함께 사용한다.

## ChangeSet은 Snapshot 쌍을 unique하게 연결한다

변경 분석은 다음 table을 사용한다:

| table | 책임 | 주요 unique constraint |
| --- | --- | --- |
| `change_sets` | 두 연속 Snapshot의 변경 분석 | `planning_document_id, baseline_snapshot_id, target_snapshot_id` |
| `physical_changes` | 결정론적 page 변화 | `change_set_id, notion_page_id, change_type` |
| `analysis_proposals` | 외부·자동 분석 결과 원본 | `analysis_proposal_id` |
| `analysis_candidates` | proposal 안의 후보 | `analysis_proposal_id, candidate_key` |
| `review_actions` | 개발자 검토 이력 | `review_action_id` |
| `change_items` | 채택된 의미 변경 | `change_item_id` |
| `impact_links` | 채택된 영향 판정 | `impact_link_id` |

`analysis_proposals.payload_hash`는 동일 payload 중복 import를 막기 위한 unique 보조 key로 사용한다. 같은 분석을 다른 기준점에 적용하지 않도록 `subject_ref`와 contract version도 함께 검증한다.

## proposal과 review action은 원본을 보존한다

`analysis_proposals`는 provider 응답이나 import JSON의 canonical payload를 `content_ref`로 저장한다. `analysis_candidates`는 candidate ID, type, current status, payload slice를 저장한다.

`review_actions`는 append-only다. candidate의 현재 표시 상태는 마지막 ReviewAction에서 계산하거나 cache field로 갱신할 수 있지만 과거 action은 수정하지 않는다.

## ChangeItem과 ImpactLink evidence를 정규화한다

채택된 결과의 근거는 별도 join table로 연결한다:

- `change_item_source_evidence`
- `impact_link_evidence`

Source evidence는 `source_page_snapshot_id`, `block_path`, `field`, `quote_hash`를 저장한다. Code evidence는 공통 `evidence_refs` table을 통해 repository, commit SHA, path, symbol을 저장한다.

하나의 evidence를 여러 Finding과 Decision에서 재사용할 수 있다.

## 검토와 결정 계층은 기존 도메인 객체를 그대로 저장한다

검토 계층은 다음 table을 사용한다:

- `review_cycles`
- `findings`
- `finding_evidence`
- `decisions`
- `decision_evidence`
- `open_questions`
- `planner_answers`
- `blockers`
- `blocked_scopes`

`review_cycles`는 target Snapshot과 code baseline commit을 고정한다. `findings`의 `finding_type`, `decision_owner`, `blocking`은 서로 다른 column으로 저장한다.

## 상태값은 CHECK constraint로 제한한다

MVP schema는 문서에 고정한 enum을 `CHECK` constraint로 제한한다. 예를 들어 `decisions.status`는 `ADOPTED`, `SUPERSEDED`, `INVALIDATED`만 허용한다.

상태 전이는 application service가 검증한다. database constraint는 허용되지 않은 문자열과 필수 관계 누락을 막는 마지막 방어선이다.

## 불변 객체는 update와 delete 경로를 제공하지 않는다

다음 table의 도메인 row는 append-only로 취급한다:

- `source_page_snapshots`
- `planning_document_snapshots`
- `planner_answers`
- `decisions`
- `final_spec_revisions`
- `change_items`
- `impact_links`
- `review_actions`

상태 전이가 필요한 객체는 status column만 명시적 repository method에서 갱신한다. 원문 내용, 채택 결과, evidence payload는 update하지 않는다.

## FinalSpec과 구현 연결을 별도 계층으로 둔다

최종설계와 구현은 다음 table을 사용한다:

- `final_specs`
- `final_spec_revisions`
- `final_spec_revision_snapshots`
- `final_spec_revision_decisions`
- `implementation_refs`
- `implementation_paths`

`final_specs.current_revision_id`만 현재 pointer로 변경한다. Revision 본문은 content-addressed store에 저장하고 이전 Revision을 유지한다.

## Git repository는 workspace 설정으로 등록한다

`repositories`는 분석 대상 local repository를 관리한다. 최소 필드는 다음과 같다:

- `repository_id`
- `name`
- `local_path`
- `remote_identity`, 확인 가능하면 저장
- `default_ref`
- `created_at`

code baseline과 EvidenceRef는 `repository_id + commit_sha`를 사용한다. working tree 상태는 근거로 저장하지 않는다.

## Notion output mapping은 내부 상태와 분리한다

projection 복구를 위해 다음 table을 사용한다:

- `notion_review_pages`: `planning_document_id → review_page_id`
- `notion_question_blocks`: `open_question_id → question_section_block_id, answer_slot_block_id`
- `notion_blocker_blocks`: `blocker_id → blocker_section_block_id`
- `projection_runs`: reconcile 실행 상태

Notion block이 삭제돼 새 block을 만들면 mapping을 새 ID로 갱신한다. 과거 Decision과 PlannerAnswer는 mapping 변경에 영향받지 않는다.

## pending operation이 process restart를 복구한다

`pending_operations`는 내부 확정 이후 필요한 후속 transition과 외부 projection을 저장한다. 최소 필드는 다음과 같다:

- `operation_id`
- `operation_type`
- `subject_ref`
- `dedupe_key`
- `status`
- `attempt`
- `available_at`
- `failure_code`
- `created_at`
- `completed_at`

`dedupe_key`는 같은 후속 작업이 여러 번 예약되는 것을 막는다. watch process는 미완료 operation을 정기 polling보다 먼저 실행한다.

## Snapshot 확정은 하나의 transaction으로 공개한다

새 Snapshot 저장 순서는 다음과 같다:

1. raw·canonical content artifact를 임시 파일로 기록
2. content hash 검증 후 artifact atomic rename
3. database transaction 시작
4. 필요한 `SourcePage`와 `SourcePageSnapshot` insert 또는 재사용
5. `PlanningDocumentSnapshot` insert
6. `planning_snapshot_pages`와 `source_references` insert
7. `planning_documents.current_snapshot_id` 갱신
8. 후속 `pending_operation`과 필요 시 `ChangeSet` 예약
9. commit

중간 database 단계가 실패하면 transaction 전체를 rollback한다. 이미 저장된 content-addressed file은 orphan cleanup 대상이 될 수 있지만 도메인 이력으로 노출되지 않는다.

## 채택 transaction은 결과와 후속 작업을 함께 기록한다

Source Diff 채택은 `ReviewAction + ChangeItem + pending Impact Analysis`를 하나의 transaction으로 저장한다. Impact 채택은 `ReviewAction + ImpactLink + pending state transition`을 하나의 transaction으로 저장한다.

이 구조는 process가 commit 직후 종료돼도 다음 watch 실행이 후속 작업을 찾을 수 있게 한다.

## schema migration은 단방향 version file로 관리한다

migration은 `migrations/0001_initial.sql`처럼 순번 파일로 저장한다. 적용한 migration의 filename과 checksum을 `schema_migrations`에 기록한다.

기존 migration file은 적용 후 수정하지 않는다. schema 변경은 새 migration을 추가한다.

## 다음 단계는 개발자가 이 상태를 조작하는 CLI 계약이다

다음 patch는 등록, 수집, analysis export/import, review, Decision, FinalSpec 확정, status 명령의 입력과 출력 형식을 고정한다.
