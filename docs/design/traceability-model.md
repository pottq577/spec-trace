---
meta:
  title: "기획 변경부터 실제 구현까지 어떻게 역추적하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 기획 변경부터 실제 구현까지 어떻게 역추적하는가

이 문서는 기획 원문 변경, 검토 결과, 결정, 최종설계, 실제 구현을 연결하는 추적 모델을 정의한다. [문서 계획](../00_INDEX.md#문서-계획)에 따라 이 관계를 확정한 뒤 Notion I/O 계약을 설계한다.

## 추적의 기준 경로

시스템은 다음 경로를 양방향으로 탐색할 수 있어야 한다:

```text
PlanningDocument
→ SourceSnapshot
→ ChangeSet / ReviewCycle
→ Finding
→ Decision
→ FinalSpecRevision
→ ImplementationRef
```

개발자는 이 경로를 통해 “왜 이 코드가 현재 형태인가”와 “이번 기획 변경이 어디까지 영향을 주는가”를 확인한다.

## 원문 변경 추적

원문 변경은 물리적 변경과 의미 변경을 분리한다.

### 물리적 변경

`SourceSnapshot.content_hash`가 이전 Snapshot과 다르면 물리적 변경이 발생한 것이다. 같은 hash를 다시 수집하면 변경 검토를 만들지 않는다.

### 의미 변경

`ChangeSet`은 두 Snapshot 사이에서 의미가 달라진 항목을 묶는다.

최소 속성은 다음과 같다:

- `change_set_id`: 변경 분석 식별자
- `planning_document_id`: 대상 기획 문서
- `baseline_snapshot_id`: 이전 Snapshot
- `target_snapshot_id`: 신규 Snapshot
- `analysis_status`: 의미 분석 진행 상태

`ChangeItem`은 `ChangeSet` 안의 개별 의미 변경이다.

변경 유형은 다음 값을 사용한다:

- `POLICY_CHANGED`: 기존 정책의 의미가 바뀜
- `REQUIREMENT_ADDED`: 새 요구사항이 추가됨
- `REQUIREMENT_REMOVED`: 기존 요구사항이 삭제됨
- `CONDITION_CHANGED`: 조건, 범위, 예외가 바뀜
- `WORDING_ONLY`: 표현만 달라지고 의미는 유지됨
- `IRRELEVANT`: 현재 제품 또는 개발 범위와 무관한 변경

각 `ChangeItem`은 이전 원문 위치와 신규 원문 위치를 모두 근거로 가져야 한다.

## 영향 분석

`ImpactLink`는 하나의 `ChangeItem`이 기존 추적 객체에 미치는 영향을 연결한다.

대상 유형은 다음과 같다:

- `FINDING`
- `DECISION`
- `FINAL_SPEC_REVISION`
- `IMPLEMENTATION`
- `RELATED_DOCUMENT`

영향 판정은 다음 값을 사용한다:

- `UNAFFECTED`: 기존 결과를 그대로 유지함
- `REVIEW_REQUIRED`: 기존 결과의 유효성을 다시 검토해야 함
- `INVALIDATED`: 기존 결과의 근거가 깨져 더 이상 사용할 수 없음
- `IMPLEMENTATION_CHANGE_REQUIRED`: 실제 코드나 작업 변경이 필요함

`ImpactLink`는 자동 분석 결과만으로 최종 확정하지 않는다. 개발자가 근거를 확인한 뒤 판정을 채택한다.

## 기존 결정의 재검토

신규 기획이 기존 Decision에 영향을 주면 다음 순서를 따른다:

1. `ChangeItem`과 기존 Decision을 `ImpactLink`로 연결한다
2. `REVIEW_REQUIRED`면 관련 Finding을 `REOPENED`하거나 새 Finding을 만든다
3. 기존 Decision이 더 이상 유효하지 않으면 `INVALIDATED`로 전환한다
4. 새 결정을 확정하면 새 Decision을 만들고 `supersedes_decision_id`로 이전 결정을 연결한다
5. 변경된 Decision을 반영한 새 `FinalSpecRevision`을 만든다

변경과 관계없는 Decision은 수정하지 않는다.

## 최종설계 추적

`FinalSpecRevision`은 당시 실제 구현 기준을 고정한다.

각 Revision은 최소한 다음 관계를 가져야 한다:

- 반영한 `SourceSnapshot`
- 반영한 `Decision`
- 대체한 이전 `FinalSpecRevision`
- 이후 생성된 `ImplementationRef`

최종설계의 중요한 정책에서 관련 Decision과 Evidence를 역추적할 수 있어야 한다. 반대로 특정 Decision이 어느 Revision과 구현에 반영됐는지도 조회할 수 있어야 한다.

## 코드 근거와 구현 연결

코드 관련 추적은 두 종류를 구분한다.

### `CodeEvidenceRef`

`CodeEvidenceRef`는 검토 당시 판단 근거로 읽은 코드를 고정한다.

최소 정보는 다음과 같다:

- `repository`: 저장소 식별자
- `commit_sha`: 검토 시점 commit
- `path`: 파일 경로
- `symbol`: 관련 클래스, 메서드, 함수 또는 선언
- `line_start`, `line_end`: 필요한 경우의 보조 위치

줄 번호는 보조 정보다. commit SHA와 path를 기본 기준점으로 사용한다.

### `ImplementationRef`

`ImplementationRef`는 최종설계가 실제 개발 결과에 반영된 위치를 연결한다.

최소 속성은 다음과 같다:

- `implementation_ref_id`: 내부 식별자
- `final_spec_revision_id`: 구현 기준 Revision
- `decision_ids`: 이 구현에 직접 반영한 Decision
- `repository`: 구현 저장소
- `commit_sha`: 실제 반영 commit
- `path_refs`: 관련 파일 또는 symbol
- `work_ref`: DevFlow PLAN/WORK 같은 개발 작업 참조

브랜치명이나 PR 번호는 탐색용 보조 정보로 저장할 수 있다. 장기 추적 기준은 immutable commit SHA다.

## 추적 불변 규칙

추적 정보는 다음 규칙을 지켜야 한다:

1. 모든 의미 변경은 기준 Snapshot과 대상 Snapshot을 가져야 한다
2. `WORDING_ONLY` 변경은 기존 Decision을 자동 재개하지 않는다
3. Decision을 무효화하거나 대체할 때 이전 Decision을 삭제하지 않는다
4. 최종설계 Revision은 반영한 Snapshot과 Decision을 고정한다
5. 코드 근거와 구현 결과는 commit SHA로 기준점을 고정한다
6. 자동 영향 분석 결과는 개발자 채택 전까지 확정 이력으로 취급하지 않는다
7. 하나의 기획 변경이 여러 Decision과 구현에 영향을 줄 수 있다
8. 하나의 구현은 여러 Decision을 동시에 반영할 수 있다

## 변경 검토 완료 조건

변경 검토는 다음 조건을 모두 만족하면 완료할 수 있다:

- 모든 `ChangeItem`의 의미 분류가 끝남
- `REVIEW_REQUIRED` 또는 `INVALIDATED` 영향의 재검토가 끝남
- 필요한 Open Question이 재검증됨
- 활성 Blocker의 `BlockedScope`가 최종설계 확정을 막지 않음
- 새 구현 기준이 필요하면 `FinalSpecRevision`이 생성됨

Notion에서 이 결과를 어떻게 표시하고 답변을 어떻게 수집할지는 다음 단계인 Notion I/O 계약에서 정의한다.
