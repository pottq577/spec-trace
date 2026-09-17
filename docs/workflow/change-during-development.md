---
meta:
  title: "개발 중 설계서가 변경되면 어떻게 대응하는가"
  contentType: "How-to"
  category: "Internal planning"
status: "Draft"
---

# 개발 중 설계서가 변경되면 어떻게 대응하는가

이 문서는 개발 중 새 기획 원문이 등록됐을 때 Snapshot 차이, 의미 변경, 기존 결정, 실제 구현 영향을 다시 검토하는 업무 순서를 설명한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 변경 대응 절차를 다루며, 변경 링크와 판정은 [추적 모델](../design/traceability-model.md)을 따른다.

## 업무 시나리오 2: 개발 중 기획서가 수정된다

이 시나리오는 현재 `FinalSpecRevision`을 기준으로 개발하는 중 기획자가 새로운 기획 원문을 등록한 경우를 다룬다.

### 1. 기획자는 기존 방식으로 설계서를 수정한다

**Actor:** 기획자

기획자는 GPT 등 기존 도구로 내용을 수정하고 새로운 전문을 Notion에 등록한다.

변경점 작성, 버전 번호 입력, Diff 생성, 변경 영향도 작성, 기존 개발 결정 확인, 개발자용 변경 요청서 작성은 개발자와 시스템이 담당한다.

### 2. 시스템이 새로운 기획 원문을 감지한다

**Actor:** 시스템

시스템은 현재 개발 기준에 연결된 `PlanningDocumentSnapshot` 이후 Notion 원문 변화를 확인한다. ROOT와 COMPOSED_CHILD를 다시 수집해 `aggregate_hash`가 달라질 때만 새 PlanningDocumentSnapshot을 확정한다.

시스템이 생성한 `개발 검토` 페이지, 기획자 answer slot, 로컬 DerivedArtifact 변경은 원문 변경에서 제외한다.

### 3. 이전 원문과 신규 원문의 의미 변화를 확인한다

**Actor:** 시스템 + 개발자 + 인공지능(AI)

첫 번째 비교는 기획자가 실제로 바꾼 내용을 찾기 위한 원문 변경 비교(Source Diff)다.

비교 대상은 다음과 같다:

- 기준 `PlanningDocumentSnapshot`
- 신규 `PlanningDocumentSnapshot`

변경 결과는 `ChangeSet`과 `ChangeItem`으로 관리한다. `ChangeItem`은 다음 유형 중 하나를 가진다:

- `POLICY_CHANGED`
- `REQUIREMENT_ADDED`
- `REQUIREMENT_REMOVED`
- `CONDITION_CHANGED`
- `WORDING_ONLY`
- `IRRELEVANT`

각 ChangeItem은 이전 원문과 신규 원문의 근거 위치를 함께 연결한다.

### 4. 신규 기획이 현재 개발에 미치는 영향을 분석한다

**Actor:** 개발자 + AI

두 번째 비교는 신규 기획이 현재 구현 기준에 미치는 영향을 찾기 위한 영향 분석(Impact Analysis)이다.

신규 PlanningDocumentSnapshot과 다음 대상을 비교한다:

- 현재 `FinalSpecRevision`
- 기존 Decision
- 현재 코드베이스
- 선행 작업
- 관련 설계서
- 이미 생성된 `ImplementationRef`

다른 모니터링 PlanningDocument의 `SourceReference` 대상이 변경돼도 현재 원문 Snapshot을 자동 갱신하지 않는다. 시스템은 역참조 관계를 사용해 별도 Impact Analysis 후보를 만든다.

각 영향은 `ImpactLink`로 연결하고 다음 중 하나로 판정한다:

- `UNAFFECTED`
- `REVIEW_REQUIRED`
- `INVALIDATED`
- `IMPLEMENTATION_CHANGE_REQUIRED`

자동 분석 결과는 개발자가 근거를 확인한 뒤 확정한다.

### 5. 영향받은 Finding과 Decision만 다시 검토한다

**Actor:** 개발자 + AI

변경과 관계없는 기존 결정은 유지한다. `REVIEW_REQUIRED` 또는 `INVALIDATED`인 항목만 다시 검토한다.

예시는 다음과 같다:

- **신규 기획 변경**: 주 15시간 선택지 삭제
- **영향받은 Decision**: 15시간 근무유형을 기존 WeeklyPolicy 구조로 관리
- **영향 판정**: `REVIEW_REQUIRED`
- **처리**: 관련 Finding을 `REOPENED`하고 기존 Decision의 유효성을 검토

기존 소수점 정책처럼 변경과 관계없는 Decision은 그대로 유지한다.

### 6. 새로운 Blocker의 `BlockedScope`를 정한다

**Actor:** 개발자 + AI

변경으로 특정 설계나 구현을 계속할 수 없으면 Finding에 Blocker와 하나 이상의 `BlockedScope`를 연결한다.

예시는 다음과 같다:

- **Blocker**: 15시간 근무유형 삭제 후 기존 데이터 처리 정책 미확정
- **BlockedScope 1**: `WORK_ITEM / migrate-legacy-15h-data`
- **BlockedScope 2**: `IMPLEMENTATION / update-legacy-15h-api`
- **현재 진행 가능**: 40시간 근무유형 관련 구현, 공통 조회 로직
- **재개 조건**: 기존 15시간 데이터 처리 정책 확정

개발자는 Scope에 포함된 작업만 보류한다.

### 7. 신규 검토 결과를 기존 검토 흐름에 반영한다

**Actor:** 개발자 + AI

영향받은 항목은 신규 검토와 같은 규칙으로 처리한다:

1. Finding의 발견 유형, 결정 주체, blocking 여부를 정한다
2. 개발자가 결정할 항목은 새 Decision을 검토한다
3. 제품 결정이 필요한 항목은 Open Question을 만들거나 다시 연다
4. 진행을 막는 항목은 Blocker와 `BlockedScope`를 만든다
5. 검토 명세서와 추적 링크를 갱신한다

기존 Decision이 무효화되면 삭제하지 않고 `INVALIDATED`로 남긴다. 대체 결정을 확정하면 새 Decision에서 이전 Decision을 supersede한다.

### 8. 기획자에게 이번 변경으로 필요한 내용만 보여준다

**Actor:** 시스템

ROOT 아래의 `개발 검토` 페이지에는 이번 변경으로 새로 발생하거나 다시 열린 항목을 중심으로 보여준다.

```markdown
# 개발 검토

## 현재 상태

기획 변경 확인 필요

## 이번 기획 변경

- 주 15시간 근무유형 삭제
- 휴게시간 적용 조건 변경

## 변경에 따른 개발 결정

- 기존 15시간 정책 관련 구현을 제거합니다

## 확인이 필요한 사항

- 기존 15시간 근무 데이터 처리 방식

## Blocker

- 막힌 범위: 기존 데이터 마이그레이션, 관련 수정 인터페이스
- 현재 가능한 작업: 공통 조회 로직, 40시간 근무유형 구현
```

출력 위치와 답변 입력 방식은 [Notion 검토 결과 계약](../integration/notion-review-contract.md)을 따른다.

### 9. 기획자 답변을 재검증한다

**Actor:** 개발자 + AI

개발자는 새 `PlannerAnswer`를 코드베이스, 기존 정책, 관련 Decision과 다시 검증한다.

충돌이 없으면 새 `PLANNER` Decision을 채택한다. Blocker의 재개 조건을 충족하면 해당 Scope를 해제하고 중단했던 작업을 다시 진행한다.

### 10. 최종설계 Revision을 갱신하고 개발을 계속한다

**Actor:** 개발자 + DevFlow

변경으로 구현 기준이 달라지면 새 `FinalSpecRevision`을 만든다.

새 Revision은 신규 PlanningDocumentSnapshot, 유지한 기존 Decision, 새 Decision을 모두 연결한다. DevFlow의 개발 기준도 새 Revision으로 변경한다.

이미 구현한 코드 수정이 필요하면 이후 `ImplementationRef`를 새 Revision에 연결해 변경 결과까지 추적한다.
