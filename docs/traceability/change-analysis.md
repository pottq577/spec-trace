---
meta:
  title: "기획 변경과 개발 영향을 어떻게 구분해 분석하는가"
  contentType: "Conceptual"
  category: "Internal planning"
status: "Draft"
---

# 기획 변경과 개발 영향을 어떻게 구분해 분석하는가

이 문서는 기획자가 바꾼 내용과 그 변경이 현재 설계와 코드에 미치는 영향을 두 단계로 분리하는 이유를 설명한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 변경 분석 책임을 구분하며, 원문 버전은 [Notion 원문 계약](../integration/notion-source-contract.md)의 PlanningDocumentSnapshot을 기준으로 한다.

## 원문 변경 비교는 기획자가 실제로 바꾼 의미를 찾는다

원문 변경 비교(Source Diff)는 기준 PlanningDocumentSnapshot과 신규 PlanningDocumentSnapshot을 비교한다. 먼저 변경된 SourcePageSnapshot만 식별하고 해당 범위의 의미를 분석한다.

의미 분류는 다음 값을 사용한다:

- `POLICY_CHANGED`
- `REQUIREMENT_ADDED`
- `REQUIREMENT_REMOVED`
- `CONDITION_CHANGED`
- `WORDING_ONLY`
- `IRRELEVANT`

`WORDING_ONLY`는 기존 Decision을 자동으로 다시 열지 않는다. 새 하위 페이지 추가, 기존 하위 페이지 제거, 부모 관계 변경도 Source Diff 입력이 된다.

## 영향 분석은 현재 구현 기준에서 무엇이 달라지는지 찾는다

영향 분석(Impact Analysis)은 신규 PlanningDocumentSnapshot과 현재 개발 기준을 대조한다.

대상은 다음과 같다:

- 현재 FinalSpecRevision
- 기존 Finding과 Decision
- 현재 코드베이스
- 선행 작업
- 관련 PlanningDocument
- 실제 구현을 나타내는 ImplementationRef

각 ChangeItem은 ImpactLink를 통해 영향받은 객체와 연결한다.

영향 판정은 다음 값을 사용한다:

- `UNAFFECTED`
- `REVIEW_REQUIRED`
- `INVALIDATED`
- `IMPLEMENTATION_CHANGE_REQUIRED`

이 구분을 사용하면 기획자가 새로 수정한 내용과 기존 개발 Decision 때문에 이미 달라진 내용을 섞지 않는다.

## 다른 기획 문서 참조 변경은 원문 변경과 구분한다

현재 PlanningDocument가 `SourceReference`로 다른 모니터링 기획을 참조하면 참조 대상의 변경은 현재 문서 Snapshot을 자동 생성하지 않는다. 시스템은 역참조 관계를 사용해 Impact Analysis 후보를 만든다.

이 방식은 공용 정책 문서 변경을 감지하면서 현재 원문 자체가 수정된 것처럼 기록하는 문제를 피한다.

## 자동 분석과 확정 판정을 구분한다

AI는 의미 변경과 영향 범위를 제안할 수 있다. 개발자는 원문, 현재 코드, 기존 Decision을 확인하고 최종 판정을 채택한다.

자동 분석만으로 기존 Decision을 무효화하거나 Blocker를 만들지 않는다. 확정된 ImpactLink가 후속 재검토와 상태 전이의 기준이 된다.
