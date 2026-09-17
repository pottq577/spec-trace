---
meta:
  title: "현재 단계에서 무엇을 확정하고 무엇을 후속 설계로 남기는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 현재 단계에서 무엇을 확정하고 무엇을 후속 설계로 남기는가

이 문서는 현재 설계 단계에서 확정한 내부 모델과 다음 단계로 넘기는 구현 계약을 구분한다. [문서 계획](../00_INDEX.md#문서-계획)의 상세 설계 범위를 관리하는 기준 문서다.

## 현재 단계에서 확정한 내부 모델

Notion I/O 계약에 들어가기 전에 다음 개념과 관계를 확정한다:

- `PlanningDocument`와 불변 `SourceSnapshot`
- `ReviewCycle`과 `Finding`
- `finding_type`, `decision_owner`, `blocking`의 독립 분류
- 개발자 Decision과 기획자 Decision
- `OpenQuestion`과 불변 `PlannerAnswer`
- `Blocker`와 복수 `BlockedScope`
- `FinalSpec`과 불변 `FinalSpecRevision`
- `ChangeSet`, `ChangeItem`, `ImpactLink`
- `EvidenceRef`와 commit SHA 기반 코드 근거
- `ImplementationRef`를 통한 최종설계와 실제 구현 연결
- 객체별 상태와 재개, 대체, 무효화 규칙

세부 정의는 [도메인 모델](./domain-model.md), [상태 모델](./state-model.md), [추적 모델](./traceability-model.md)을 따른다.

## 다음 단계에서 확정할 Notion I/O 계약

다음 상세 설계는 기획자의 기존 작업방식을 유지하면서 내부 모델을 Notion과 연결하는 계약을 정의한다:

- 신규 기획 원문을 어떤 Notion 단위로 식별하는가
- 기획 원문을 언제 Snapshot으로 수집하는가
- 같은 전문 재등록과 실제 변경을 어떻게 구분하는가
- 개발 검토 결과를 기존 기획 페이지의 어디에 연결하는가
- 같은 ReviewCycle 결과를 재실행할 때 생성과 갱신을 어떻게 구분하는가
- Open Question과 Blocker를 어떤 구조로 표시하는가
- 기획자가 답변을 작성할 위치와 형식은 무엇인가
- 답변 수정 시 새 `PlannerAnswer`를 어떻게 식별하는가
- 시스템 생성 영역과 기획자 작성 영역을 어떻게 구분하는가
- 중복 실행 시 idempotency를 어떻게 보장하는가

이 계약을 확정하기 전에는 Notion API 호출 방식이나 페이지 템플릿을 구현 기준으로 고정하지 않는다.

## Notion I/O 이후의 상세 설계

Notion 계약이 확정된 다음 다음 구현 사항을 순서대로 설계한다:

1. 변경 감지와 Source Diff 구현
2. 시스템 아키텍처와 실행 경계
3. 저장소와 데이터베이스 스키마
4. 검토 명세서 등록 인터페이스
5. DevFlow 연동 계약
6. MVP acceptance scenario와 검증 전략

의미 기반 분석에 사용할 모델이나 프롬프트 세부 구현은 변경 감지 설계에서 결정한다.

## 제품의 완료 기준

이 시스템은 다음 조건을 만족해야 한다:

- 기획자는 기존 Notion 작성 방식을 유지한다
- 개발자는 신규 또는 변경된 기획을 놓치지 않는다
- 개발자는 기획의 의미 변경을 이전 원문과 비교할 수 있다
- 개발자는 현재 코드와 정책에 대한 영향을 확인할 수 있다
- 기술적 결정은 근거와 함께 개발자가 확정할 수 있다
- 제품 결정은 충분한 맥락과 선택지를 포함해 기획자에게 전달된다
- Blocker는 복수 `BlockedScope`와 함께 관리할 수 있다
- Blocker가 있어도 Scope 밖의 작업은 계속할 수 있다
- 기획자는 Notion에서 자신이 확인하거나 결정할 항목을 바로 파악할 수 있다
- 기획자 답변은 개발자가 재검증한 뒤 Decision으로 채택된다
- 변경된 답변과 Decision의 과거 이력을 보존한다
- 최종설계 Revision은 반영한 원문과 Decision을 역추적할 수 있다
- 최종설계서는 DevFlow의 구현 기준으로 사용된다
- 개발 중 기획 변경은 원문 변경과 구현 영향을 분리해 추적한다
- 실제 구현 commit에서 최종설계와 결정 근거까지 역추적할 수 있다
