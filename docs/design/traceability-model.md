---
meta:
  title: "기획 변경부터 실제 구현까지 어떻게 역추적하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 기획 변경부터 실제 구현까지 어떻게 역추적하는가

이 문서는 기획 전체 Snapshot의 변경, 검토 결과, 결정, 최종설계, 실제 구현을 연결하는 추적 모델을 정의한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 추적 경계를 고정하며, 원문 페이지 수집 기준은 [Notion 원문 계약](../integration/notion-source-contract.md)을 따른다.

## 추적은 논리 기획 버전을 기준으로 시작한다

시스템은 다음 경로를 양방향으로 탐색할 수 있어야 한다:

```text
PlanningDocument
→ PlanningDocumentSnapshot
→ ChangeSet / ReviewCycle
→ Finding
→ Decision
→ FinalSpecRevision
→ ImplementationRef
```

개별 Notion 페이지 근거가 필요하면 PlanningDocumentSnapshot에서 `SourcePageSnapshot`까지 내려간다. 개발자는 이 경로로 현재 코드의 근거와 신규 기획 변경의 영향을 확인한다.

## 물리적 변경과 의미 변경을 분리한다

`PlanningDocumentSnapshot.aggregate_hash`가 직전 버전과 다르면 물리적 변경이 발생한 것이다. 같은 aggregate hash를 다시 수집하면 새 변경 검토를 만들지 않는다.

`ChangeSet`은 두 PlanningDocumentSnapshot 사이의 의미 차이를 묶는다.

최소 속성은 다음과 같다:

- `change_set_id`: 변경 분석 식별자
- `planning_document_id`: 대상 기획 문서
- `baseline_planning_snapshot_id`: 이전 기획 전체 Snapshot
- `target_planning_snapshot_id`: 신규 기획 전체 Snapshot
- `analysis_status`: 의미 분석 진행 상태

각 `ChangeItem`은 실제로 달라진 `SourcePageSnapshot`과 원문 위치를 근거로 연결한다.

## 의미 변경은 여섯 유형으로 분류한다

ChangeItem은 다음 값을 사용한다:

- `POLICY_CHANGED`: 기존 정책의 의미가 바뀜
- `REQUIREMENT_ADDED`: 새 요구사항이 추가됨
- `REQUIREMENT_REMOVED`: 기존 요구사항이 삭제됨
- `CONDITION_CHANGED`: 조건, 범위, 예외가 바뀜
- `WORDING_ONLY`: 표현만 달라지고 의미는 유지됨
- `IRRELEVANT`: 현재 제품 또는 개발 범위와 무관한 변경

`WORDING_ONLY`는 기존 Decision을 자동으로 다시 열지 않는다. `IRRELEVANT`도 Impact Analysis 대상에서 제외할 수 있다.

## 페이지 단위 변경은 의미 분석 범위를 좁힌다

시스템은 PlanningDocumentSnapshot을 비교하기 전에 포함된 SourcePageSnapshot을 비교한다. 변경된 페이지, 추가된 페이지, 제거된 페이지를 먼저 식별한다.

의미 분석은 다음 범위를 우선 처리한다:

- content hash가 달라진 SourcePage
- 새로 추가된 COMPOSED_CHILD
- 트리에서 제거된 COMPOSED_CHILD
- 부모 관계가 바뀐 SourcePage

변경 없는 SourcePage 전체를 매번 다시 분석할 필요는 없다.

## 다른 기획 문서 참조는 별도 영향 후보로 연결한다

`SourceReference` 대상이 다른 모니터링 PlanningDocument라면 대상 문서의 새 Snapshot이 현재 문서의 원문 버전을 올리지는 않는다. 대신 역참조를 사용해 Impact Analysis 후보를 만든다.

이 후보는 “참조 정책 변경이 현재 기획과 구현에 영향을 주는가”를 별도로 검토한다. 영향이 없으면 현재 PlanningDocumentSnapshot과 Decision을 그대로 유지한다.

## `ImpactLink`는 변경이 기존 추적 객체에 미치는 영향을 연결한다

ImpactLink 대상 유형은 다음과 같다:

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

인공지능(AI) 분석 결과만으로 ImpactLink를 확정하지 않는다. 개발자가 근거를 확인한 뒤 판정을 채택한다.

## 기존 Decision은 영향받은 경우에만 다시 연다

신규 기획이 기존 Decision에 영향을 주면 다음 순서를 따른다:

1. ChangeItem과 기존 Decision을 ImpactLink로 연결한다
2. `REVIEW_REQUIRED`면 관련 Finding을 `REOPENED`하거나 새 Finding을 만든다
3. 기존 Decision의 근거가 깨지면 `INVALIDATED`로 전환한다
4. 새 결정을 확정하면 새 Decision에서 `supersedes_decision_id`로 이전 결정을 연결한다
5. 변경된 Decision을 반영한 새 FinalSpecRevision을 만든다

변경과 관계없는 Decision은 수정하지 않는다.

## FinalSpecRevision은 당시 구현 기준을 고정한다

각 FinalSpecRevision은 최소한 다음 관계를 가진다:

- 반영한 PlanningDocumentSnapshot
- 반영한 Decision
- 대체한 이전 FinalSpecRevision
- 이후 생성된 ImplementationRef

최종설계의 중요한 정책에서 관련 Decision과 EvidenceRef를 역추적할 수 있어야 한다. 특정 Decision이 어느 Revision과 구현에 반영됐는지도 조회할 수 있어야 한다.

## 코드 근거와 구현 결과를 구분한다

`CodeEvidenceRef`는 검토 당시 판단 근거로 읽은 코드를 고정한다.

최소 정보는 다음과 같다:

- `repository`: 저장소 식별자
- `commit_sha`: 검토 시점 commit
- `path`: 파일 경로
- `symbol`: 관련 클래스, 메서드, 함수 또는 선언
- `line_start`, `line_end`: 필요한 경우의 보조 위치

`ImplementationRef`는 FinalSpecRevision이 실제 개발 결과에 반영된 위치를 연결한다.

최소 속성은 다음과 같다:

- `implementation_ref_id`: 내부 식별자
- `final_spec_revision_id`: 구현 기준 Revision
- `decision_ids`: 이 구현에 직접 반영한 Decision
- `repository`: 구현 저장소
- `commit_sha`: 실제 반영 commit
- `path_refs`: 관련 파일 또는 symbol
- `work_ref`: DevFlow PLAN/WORK 같은 개발 작업 참조

줄 번호, 브랜치명, pull request 번호는 탐색용 보조 정보다. 장기 추적 기준은 immutable commit SHA다.

## DerivedArtifact는 근거로 사용할 수 있지만 원문 계보에 들어가지 않는다

개발자가 만든 코드 대조 분석서나 확정사항 정리는 EvidenceRef에서 `DERIVED_ARTIFACT`로 참조할 수 있다. DerivedArtifact 수정은 ChangeSet을 만들지 않는다.

DerivedArtifact가 잘못됐거나 갱신돼도 원문 Snapshot 이력은 바뀌지 않는다. Decision이 해당 문서를 근거로 사용했다면 개발자가 Decision 유효성을 별도로 재검토한다.

## 추적 불변 규칙

추적 정보는 다음 규칙을 지켜야 한다:

1. 모든 Source Diff는 기준 PlanningDocumentSnapshot과 대상 PlanningDocumentSnapshot을 가진다
2. 모든 ChangeItem은 하나 이상의 SourcePageSnapshot 근거를 추적할 수 있어야 한다
3. `WORDING_ONLY` 변경은 기존 Decision을 자동 재개하지 않는다
4. Decision을 무효화하거나 대체할 때 이전 Decision을 삭제하지 않는다
5. FinalSpecRevision은 반영한 PlanningDocumentSnapshot과 Decision을 고정한다
6. 코드 근거와 구현 결과는 commit SHA로 기준점을 고정한다
7. 자동 영향 분석 결과는 개발자 채택 전까지 확정 이력으로 취급하지 않는다
8. 하나의 기획 변경은 여러 Decision과 구현에 영향을 줄 수 있다
9. 하나의 구현은 여러 Decision을 동시에 반영할 수 있다
10. SourceReference 대상 변경은 참조한 문서의 원문 Snapshot을 자동 생성하지 않는다

## 변경 검토 완료 조건

변경 ReviewCycle은 다음 조건을 모두 만족하면 완료할 수 있다:

- 모든 ChangeItem의 의미 분류가 끝남
- `REVIEW_REQUIRED` 또는 `INVALIDATED` 영향의 재검토가 끝남
- 필요한 OpenQuestion이 재검증됨
- 활성 Blocker의 BlockedScope가 FinalSpecRevision 확정을 막지 않음
- 새 구현 기준이 필요하면 FinalSpecRevision이 생성됨

Notion 출력과 답변 수집의 재실행 규칙은 [Notion 동기화 규칙](../integration/notion-sync-rules.md)을 따른다.
