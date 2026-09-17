---
meta:
  title: "확정된 최종설계를 DevFlow와 어떻게 연결하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 확정된 최종설계를 DevFlow와 어떻게 연결하는가

이 문서는 `FinalSpecRevision`을 DevFlow 계획 입력으로 내보내고 실제 구현 결과를 `ImplementationRef`로 다시 연결하는 handoff 계약을 정의한다. spec-trace는 DevFlow의 PLAN, WORK, STATE를 직접 수정하지 않는다.

## FinalSpecRevision이 DevFlow 입력 기준이다

DevFlow handoff는 확정된 `FinalSpecRevision` 하나를 기준으로 만든다. 원본 Notion page, 미채택 analysis proposal, 미검증 PlannerAnswer를 직접 개발 입력으로 넘기지 않는다.

handoff를 만들려면 다음 조건을 만족해야 한다:

- 대상 `FinalSpecRevision`이 존재한다
- Revision이 반영한 PlanningDocumentSnapshot을 추적할 수 있다
- Revision이 반영한 Decision을 추적할 수 있다
- FinalSpec 확정을 막는 active Blocker가 없다
- content artifact hash 검증이 성공한다

## export는 DevFlow 내부 파일을 생성하지 않는다

spec-trace는 DevFlow plugin repository나 실행 중 STATE를 직접 쓰지 않는다. 대신 독립적인 handoff directory를 생성한다.

기본 경로는 다음과 같다:

```text
.spec-trace/exports/devflow/
└── final_spec_revision_id_here/
    ├── manifest.json
    ├── final-spec.md
    ├── decisions.json
    ├── blockers.json
    ├── traceability.json
    └── implementation-receipt.schema.json
```

개발자는 이 directory와 `final-spec.md`를 DevFlow 계획 입력으로 전달한다. DevFlow는 자신의 plan, work, audit, run lifecycle을 그대로 소유한다.

## manifest가 handoff identity를 고정한다

`manifest.json`은 다음 필드를 가진다:

- `contract_version`
- `export_id`
- `planning_document_id`
- `final_spec_revision_id`
- `final_spec_content_hash`
- `planning_snapshot_ids`
- `decision_ids`
- `active_blocker_ids`
- `generated_at`
- `source_workspace`

같은 Revision과 content hash를 다시 export하면 같은 logical handoff로 취급한다. 파일을 재생성해도 Revision identity는 바뀌지 않는다.

## final-spec.md는 구현자가 읽는 단일 기준 문서다

`final-spec.md`는 `FinalSpecRevision.content_ref`의 내용을 그대로 내보낸다. export 과정에서 요구사항을 다시 요약하거나 AI가 재작성하지 않는다.

추가 추적 정보는 별도 JSON file에 둔다. DevFlow plan이 구현 기준을 읽을 때 원문과 부가 metadata를 혼동하지 않게 한다.

## decisions.json은 최종설계의 결정 근거를 제공한다

`decisions.json`은 Revision이 반영한 Decision만 포함한다. 각 항목은 다음 값을 가진다:

- `decision_id`
- `owner`
- `adopted_option`
- `rationale`
- `evidence_refs`
- `supersedes_decision_id`

기획자에게 보여주기 위한 축약 설명보다 개발 근거를 우선한다. EvidenceRef는 source Snapshot, 관련 Decision, immutable code commit을 다시 찾을 수 있어야 한다.

## blockers.json은 남은 진행 제한을 명시한다

FinalSpec 확정을 막지 않는 active Blocker가 존재할 수 있다면 handoff에 포함한다. 각 Blocker는 `BlockedScope`, resume condition, 현재 가능한 범위를 제공한다.

DevFlow는 BlockedScope에 포함된 work를 계획에서 제외하거나 blocked 상태로 유지해야 한다. spec-trace가 DevFlow STATE를 직접 바꾸지 않으므로 developer 또는 DevFlow adapter가 이 constraint를 계획 입력에 반영한다.

## traceability.json은 원문부터 Revision까지 연결한다

`traceability.json`은 DevFlow가 필요할 때 근거를 탐색할 수 있는 최소 graph를 제공한다:

```text
PlanningDocumentSnapshot
→ ChangeItem
→ ImpactLink
→ Finding
→ Decision
→ FinalSpecRevision
```

전체 database dump를 export하지 않는다. 현재 Revision이 참조하는 객체와 직접 근거만 포함한다.

## 계획 시작 전 handoff freshness를 확인한다

DevFlow 계획을 만들기 직전에 다음 조건을 확인한다:

- export의 `final_spec_revision_id`가 해당 FinalSpec의 current Revision이다
- Revision content hash가 manifest와 같다
- 더 최신 PlanningDocumentSnapshot이 pending review 상태가 아니다
- 새 active Blocker가 현재 Revision의 계획 범위를 막지 않는다

조건이 깨지면 handoff를 `STALE`로 취급하고 현재 Revision으로 다시 export한다.

## 실행 중 새 Revision이 생겨도 기존 handoff를 덮어쓰지 않는다

DevFlow가 Revision A를 기준으로 개발 중일 때 Revision B가 확정될 수 있다. spec-trace는 A의 export directory를 유지하고 B용 새 export를 만든다.

영향 분석은 A를 기준으로 진행 중인 구현이 B에 의해 수정돼야 하는지 판정한다. 기존 PLAN이나 WORK를 자동으로 B 기준으로 바꾸지 않는다.

## DevFlow 작업 참조는 opaque string으로 저장한다

spec-trace는 DevFlow 내부 ID 형식을 소유하지 않는다. PLAN, phase, WORK 참조는 `work_ref` 문자열로 저장하고 사람이 식별할 수 있는 label을 함께 둘 수 있다.

예시는 다음과 같다:

```text
work_ref = "attendance/work-standard-registration/WORK-03"
```

DevFlow 내부 naming rule이 바뀌어도 기존 ImplementationRef의 commit 연결은 유지된다.

## 구현 완료는 receipt로 다시 가져온다

DevFlow 또는 개발자는 구현 완료 시 `implementation-receipt.json`을 만든다. 최소 필드는 다음과 같다:

- `contract_version`
- `final_spec_revision_id`
- `repository`
- `commit_sha`
- `path_refs`
- `work_ref`
- `decision_ids`
- `verified_at`

spec-trace는 다음 명령으로 receipt를 가져온다:

```bash
spec-trace devflow import .spec-trace/exports/implementation-receipt.json
```

import가 성공하면 `ImplementationRef`를 만들고 Revision, Decision, commit을 연결한다.

## receipt는 immutable commit을 검증한다

import 시 local Git repository에서 `commit_sha`가 존재하는지 확인한다. `path_refs`가 있으면 해당 commit에서 path가 존재하는지도 검증한다.

working tree 또는 branch tip의 현재 상태는 receipt 검증 기준이 아니다. commit이 존재하지 않으면 `ImplementationRef`를 생성하지 않는다.

## 한 구현은 여러 Decision을 반영할 수 있다

receipt의 `decision_ids`는 Revision이 포함한 Decision의 부분집합이어야 한다. 하나의 commit이 여러 Decision을 함께 구현하면 하나의 `ImplementationRef`에서 여러 ID를 연결할 수 있다.

반대로 하나의 Decision이 여러 commit에 나뉘면 여러 `ImplementationRef`가 같은 Decision을 참조할 수 있다.

## DevFlow 연동은 두 시스템의 책임을 분리한다

spec-trace의 책임은 다음과 같다:

- 검증된 최종설계와 결정 근거 export
- handoff freshness 확인
- BlockedScope 전달
- implementation receipt 검증
- `ImplementationRef` 생성과 역추적

DevFlow의 책임은 다음과 같다:

- repository-grounded PLAN 생성
- WORK 분해와 lifecycle 관리
- 구현, audit, remediation, finalize
- 실제 commit 생성과 검증 evidence 확보

각 시스템은 상대 시스템의 내부 state file을 직접 수정하지 않는다.

## 다음 단계는 전체 MVP round trip을 acceptance scenario로 고정하는 일이다

다음 patch는 신규 기획 검토, 기획자 답변, 구현 handoff, 개발 중 기획 변경까지 한 흐름으로 실행해 첫 MVP 완료 여부를 판정하는 acceptance scenario를 정의한다.
