---
meta:
  title: "기획 검토부터 개발까지 업무는 어떻게 진행되는가"
  contentType: "Conceptual"
  category: "Internal planning"
status: "Draft"
---

# 기획 검토부터 개발까지 업무는 어떻게 진행되는가

이 문서는 신규 기획 검토와 개발 중 변경 검토가 공유하는 전체 업무 흐름을 설명한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 공통 절차를 요약하며, 내부 상태의 정확한 값과 전이 규칙은 [상태 모델](../design/state-model.md)을 따른다.

## 전체 업무 흐름

신규 기획서와 개발 중 변경은 같은 검토 구조를 사용한다. 변경이 발생하면 실제 변경점과 영향받은 결정부터 다시 검토한다.

1. 기획자가 Notion에 기획 원문 작성
2. 시스템이 Notion 원문과 하위 페이지를 수집해 PlanningDocumentSnapshot 확정
3. 코드, 정책, 선행 문서와 대조
4. 검토 명세서 작성
5. Finding의 유형, 결정 주체, blocking 여부 분류
6. 개발 결정, Open Question, Blocker 처리
7. ROOT 아래 `개발 검토` 페이지에 기획자용 결과 제공
8. 기획자 답변과 개발자 재검증
9. 최종설계서 Revision 확정
10. DevFlow로 개발
11. 기획 원문 변경 감지
12. 변경 의미와 개발 영향 분석
13. 영향받은 Finding과 Decision 재검토
14. 최종설계서 Revision 갱신 후 개발 계속

## 검토와 결정의 반복 규칙

기획자 답변은 즉시 최종 결정으로 취급하지 않는다. 개발자는 답변을 현재 코드베이스, 기존 정책, 관련 설계와 다시 대조한다.

검토 반복은 다음 순서를 따른다:

1. Open Question 또는 Blocker
2. 기획자 답변
3. 개발자 재검증
4. 충돌 여부 판정
5. Decision 확정 또는 질문 재개

재검증에서 새로운 충돌이나 누락이 발견되면 관련 Finding만 다시 열고 필요한 정보만 기획자에게 전달한다.

## 업무 상태와 내부 상태를 구분한다

기획자와 개발자가 보는 업무 상태는 여러 내부 객체의 상태를 요약한다.

- **검토 대기**: `ReviewCycle=PENDING`
- **검토 중**: `ReviewCycle=REVIEWING`
- **기획 확인 필요**: `ReviewCycle=AWAITING_PLANNER`
- **재검증 중**: `ReviewCycle=REVERIFYING`
- **검토 완료**: `ReviewCycle=COMPLETED`
- **개발 중**: 완료된 최종설계 Revision을 기준으로 DevFlow 작업이 진행 중
- **변경 재검토**: 신규 `PlanningDocumentSnapshot`으로 `CHANGE` ReviewCycle이 생성됨

기획자용 상태 문구와 표시 위치는 [Notion 검토 결과 계약](../integration/notion-review-contract.md)을 따른다. 내부 상태값을 그대로 노출하지 않는다.

## 변경 시 기존 결과를 유지한다

새 기획 원문이 등록돼도 기존 검토 결과를 모두 초기화하지 않는다.

- 물리적 내용이 같으면 변경 검토를 만들지 않는다
- 의미가 바뀌지 않은 결정은 유지한다
- 영향받은 Finding만 재개한다
- 무효화되거나 대체된 Decision도 이력으로 보존한다
- 활성 Blocker의 `BlockedScope` 밖의 작업은 계속 진행한다

변경 판정과 추적 규칙은 [추적 모델](../design/traceability-model.md)을 따른다.
