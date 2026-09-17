---
meta:
  title: "기획 변경이 현재 개발 기준에 어떤 영향을 주는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 기획 변경이 현재 개발 기준에 어떤 영향을 주는가

이 문서는 채택된 `ChangeItem`을 현재 개발 기준과 대조해 `ImpactLink` 후보를 만드는 입출력 계약을 정의한다. 개발자가 후보를 검증해 채택하기 전에는 기존 상태를 바꾸지 않는다.

## Impact Analysis는 현재 개발 기준과 비교한다

Impact Analysis의 질문은 “채택된 원문 의미 변경 때문에 현재 무엇을 다시 검토하거나 수정해야 하는가”다. Source Diff가 원문끼리 비교했다면 이 단계는 대상 Snapshot과 현재 구현 기준을 함께 본다.

기본 분석 대상은 다음과 같다:

- 현재 `FinalSpecRevision`
- 해당 Revision이 반영한 활성 Decision
- 현재 ReviewCycle의 해결된 Finding
- 실제 구현을 가리키는 `ImplementationRef`
- 분석 시작 시점의 코드 commit
- `SourceReference`로 연결된 관련 `PlanningDocumentSnapshot`

## 분석 시작 시 개발 기준점을 고정한다

하나의 Impact Analysis 요청은 다음 값을 가진다:

- `impact_request_id`
- `change_set_id`
- `target_planning_snapshot_id`
- `change_item_ids`
- `final_spec_revision_id`, 아직 없으면 빈 값
- `active_decision_ids`
- `code_baseline`: repository와 commit SHA
- `implementation_ref_ids`
- `related_planning_snapshot_ids`
- `analysis_contract_version`

분석 중 Git branch가 이동해도 `code_baseline.commit_sha`를 바꾸지 않는다. 개발자가 결과를 채택할 때 현재 기준점과 달라졌다면 freshness 검증을 다시 수행한다.

## 분석 범위 manifest가 확인한 대상을 기록한다

분석기는 임의의 전체 시스템 상태를 숨겨서 조회하지 않는다. 요청에 포함한 기준점에서 확인한 대상 목록을 `scope_manifest`로 반환한다.

manifest는 다음 대상을 구분한다:

- `DECISION`
- `FINDING`
- `FINAL_SPEC_REVISION`
- `IMPLEMENTATION`
- `RELATED_DOCUMENT`
- `CODE`

코드 항목은 repository, commit SHA, path, symbol을 포함한다. 이 목록을 사용하면 개발자는 분석기가 어떤 범위를 확인했는지 검증할 수 있다.

## 영향 후보는 변경과 대상의 관계 하나를 표현한다

`ImpactCandidate`는 다음 필드를 가진다:

- `candidate_id`
- `change_item_id`
- `target_type`
- `target_ref`
- `assessment`
- `summary`
- `rationale`
- `evidence_refs`
- `proposed_action`

같은 `change_item_id + target_type + target_ref`에는 하나의 현재 후보만 둔다. 재분석 결과가 바뀌면 이전 candidate를 대체 관계로 보존한다.

## 영향 판정은 네 값으로 고정한다

`assessment`는 다음 값을 사용한다:

- `UNAFFECTED`: 변경을 검토했으며 대상의 현재 결과를 유지할 수 있음
- `REVIEW_REQUIRED`: 기존 결과의 유효성을 개발자가 다시 검토해야 함
- `INVALIDATED`: 기존 결과의 근거가 깨져 현재 구현 기준으로 사용할 수 없음
- `IMPLEMENTATION_CHANGE_REQUIRED`: 실제 코드, 데이터, 테스트, 마이그레이션 작업이 필요함

하나의 대상이 여러 효과를 동시에 받으면 후속 행동을 가장 직접적으로 표현하는 판정을 사용한다. 예를 들어 Decision 근거가 깨지고 코드 수정도 필요하면 Decision에는 `INVALIDATED`, 구현에는 `IMPLEMENTATION_CHANGE_REQUIRED`를 각각 연결한다.

## 대상 유형별 판정 기준을 구분한다

각 대상은 다음 기준으로 판정한다:

- `DECISION`: 채택 근거와 결론이 새 요구사항에서도 유효한지 확인
- `FINDING`: 해결 조건이 새 요구사항 때문에 다시 열려야 하는지 확인
- `FINAL_SPEC_REVISION`: 현재 구현 기준 문서가 새 Snapshot을 반영하는지 확인
- `IMPLEMENTATION`: 실제 반영 코드나 작업이 수정돼야 하는지 확인
- `RELATED_DOCUMENT`: 관련 기획과 정책 정의가 충돌하거나 재검토돼야 하는지 확인
- `CODE`: 현재 commit의 동작이 새 요구사항과 맞는지 확인

`UNAFFECTED`도 근거를 가져야 한다. 단순히 후보가 없다는 이유로 영향 없음으로 간주하지 않는다.

## 코드 근거는 immutable commit에 고정한다

코드 evidence는 최소한 다음 값을 가진다:

- `repository`
- `commit_sha`
- `path`
- `symbol`, 특정 symbol이 없으면 빈 값
- `line_start`, `line_end`, 탐색용 보조 위치
- `content_hash`, 해당 근거 조각의 hash

줄 번호는 commit SHA와 함께 사용할 때만 의미가 있다. branch 이름만으로 근거를 저장하지 않는다.

## 관련 문서 변경은 역참조 후보로 분석할 수 있다

현재 문서의 원문이 바뀌지 않았어도 `SourceReference` 대상 문서에 새 Snapshot이 생기면 Impact Analysis 요청을 만들 수 있다. 이 경우 `change_item_id` 대신 `reference_change_ref`를 사용하고 현재 문서의 새 `PlanningDocumentSnapshot`은 만들지 않는다.

역참조 분석도 같은 네 영향 판정을 사용한다. 영향이 없으면 `UNAFFECTED`를 채택해 후보 처리를 닫는다.

## 분석 응답은 후보와 확인 범위를 함께 반환한다

Impact Analysis 응답은 다음 상위 구조를 사용한다:

```json
{
  "change_set_id": "change_set_id_here",
  "analysis_contract_version": "1",
  "status": "PROPOSED",
  "scope_manifest": [],
  "candidates": []
}
```

응답은 기존 Decision, Finding, Blocker를 직접 수정하지 않는다. `proposed_action`은 개발자가 결과를 이해하기 위한 제안값이다.

## 후보 결과는 정확한 후속 행동을 제안한다

`proposed_action`은 다음 값 중 하나를 사용한다:

- `KEEP`: 현재 결과 유지
- `REOPEN_FINDING`: 기존 Finding 재검토
- `INVALIDATE_DECISION`: 기존 Decision 무효화 검토
- `CREATE_FINDING`: 새 문제 등록
- `UPDATE_FINAL_SPEC`: 새 `FinalSpecRevision` 필요
- `CREATE_WORK_ITEM`: 코드, 테스트, 데이터 작업 필요
- `REVIEW_RELATED_DOCUMENT`: 관련 기획 검토 필요

제안 행동은 자동 상태 전이가 아니다. 개발자가 영향 판정을 채택한 뒤 상태 전이 규칙을 실행한다.

## 분석 completeness는 대상과 근거를 검증한다

Impact Analysis 결과는 다음 조건을 만족해야 채택 단계로 넘어간다:

1. 모든 candidate가 존재하는 `ChangeItem` 또는 reference change를 가리킨다
2. 모든 target이 scope manifest에 존재한다
3. 모든 evidence가 고정된 Snapshot 또는 commit에서 다시 해석된다
4. `INVALIDATED`와 `IMPLEMENTATION_CHANGE_REQUIRED`는 하나 이상의 직접 근거를 가진다
5. 현재 `FinalSpecRevision`이 존재하면 manifest에 반드시 포함된다
6. 현재 Revision이 반영한 활성 Decision은 모두 manifest에 포함된다

조건을 만족하지 못하면 `IMPACT_ANALYSIS_INCOMPLETE`로 처리한다.

## 최신 코드 기준과 달라지면 채택 전에 재확인한다

분석 완료 뒤 대상 repository의 현재 개발 기준 commit이 `code_baseline.commit_sha`와 달라질 수 있다. 개발자가 후보를 채택할 때 시스템은 현재 기준점을 비교한다.

코드 기준점이 달라졌고 candidate가 `CODE` 또는 `IMPLEMENTATION`을 참조하면 해당 후보를 `STALE`로 표시한다. 개발자는 새 commit으로 재분석하거나 근거가 여전히 유효함을 직접 확인해 채택한다.

## 실행 계약은 다음 시나리오를 만족해야 한다

구현은 최소한 다음 사례를 검증해야 한다:

- 활성 Decision과 현재 FinalSpecRevision을 항상 scope manifest에 포함한다
- 영향받지 않은 Decision도 근거와 함께 `UNAFFECTED`로 기록할 수 있다
- Decision 근거가 깨지고 코드 수정도 필요하면 대상별로 다른 판정을 만든다
- 코드 근거는 분석 당시 commit SHA에 고정한다
- 분석 후 코드 기준 commit이 바뀌면 관련 후보를 `STALE`로 표시한다
- 역참조 문서 변경은 현재 문서 Snapshot을 만들지 않고 별도 영향 후보로 닫는다

## 다음 단계는 후보를 개발자 결정으로 채택하는 일이다

다음 patch는 Source Diff와 Impact Analysis 후보를 개발자가 검증하고 채택하는 공통 인터페이스를 정의한다. 같은 patch에서 실패 분류와 재시도 책임도 고정해 실행 파이프라인을 닫는다.
