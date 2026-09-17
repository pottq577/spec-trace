---
meta:
  title: "신규 설계서를 어떻게 검토하고 개발 기준으로 확정하는가"
  contentType: "How-to"
  category: "Internal planning"
status: "Draft"
---

# 신규 설계서를 어떻게 검토하고 개발 기준으로 확정하는가

이 문서는 신규 기획 원문을 처음 검토해 `Finding`과 Decision을 확정하고, 첫 `FinalSpecRevision`을 만드는 업무 순서를 설명한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 신규 검토 절차를 다루며, 객체와 상태 규칙은 [도메인 모델](../design/domain-model.md)과 [상태 모델](../design/state-model.md)을 따른다.

## 업무 시나리오 1: 신규 설계서를 검토하고 개발한다

이 시나리오는 기획자가 작성한 설계서를 처음 검토하고 최종설계서를 확정한 뒤 개발을 시작하는 흐름이다.

### 1. 기획자가 설계서를 작성한다

**Actor:** 기획자

기획자는 기존 방식대로 GPT 등 기존 도구로 설계서를 작성하고 결과 전문을 Notion에 등록한다.

기획자는 별도의 검토 양식이나 변경 이력을 작성하지 않는다.

### 2. 시스템이 기획 원문을 수집하고 로컬 미러를 만든다

**Actor:** 시스템 + 개발자

시스템은 Notion 데이터베이스 행 페이지를 `PlanningDocument`로 식별하고 ROOT와 모든 COMPOSED_CHILD를 재귀 수집한다. 일관된 수집이 끝나면 `PlanningDocumentSnapshot`을 확정한다.

개발자가 로컬 Markdown으로 검토할 수 있도록 원문 미러와 `.spec-trace/source-manifest.json` mapping을 생성한다. 같은 디렉터리에 있는 개발자 작성 문서는 `DerivedArtifact`로 분리한다.

### 3. 개발자가 AI와 함께 설계서를 검토한다

**Actor:** 개발자 + 인공지능(AI)

개발자는 다음 자료를 함께 검토한다:

- 현재 코드베이스
- 이미 구현된 선행 기능
- 기존 도메인 정책
- 관련 설계서
- 이전 개발 결정
- 현재 설계서 내부의 조건과 결과

검토에서 발견한 각 항목은 `Finding`으로 만든다. Finding은 `finding_type`, `decision_owner`, `blocking`을 독립적으로 가진다.

### 4. 개발자가 결정 가능한 항목을 확정한다

**Actor:** 개발자 + AI

코드베이스, 선행 작업, 기존 정책, 도메인 지식만으로 판단할 수 있으면 `decision_owner=DEVELOPER`로 분류한다.

예시는 다음과 같다:

- **문제**: 기획서에서 새로운 소수점 처리 정책을 정의함
- **발견 유형**: `POLICY_CONFLICT`
- **결정 주체**: `DEVELOPER`
- **근거**: 근태환경설정에서 같은 정책이 이미 확정됨
- **선택지 A**: 현재 설계서 기준으로 별도 정책 추가
- **선택지 B**: 기존 근태환경설정 정책 재사용
- **개발 권장**: B
- **채택**: 기존 근태환경설정 정책 재사용
- **설계서와의 차이**: 현재 설계서의 별도 소수점 정책은 구현 기준에서 제외

개발자가 결과를 채택하면 `Decision(owner=DEVELOPER)`을 만든다. 기획자는 이 결정을 승인하지 않는다.

### 5. 제품 결정이 필요한 항목을 Open Question으로 만든다

**Actor:** 개발자 + AI

제품 정책이나 사용자 경험을 개발자가 확정할 수 없으면 `decision_owner=PLANNER`로 분류하고 Open Question을 만든다.

예시는 다음과 같다:

- **질문**: 주 15시간 근무자에게 공휴일이 포함되면 주간 기준시간을 어떻게 계산해야 하는가?
- **발견 유형**: `REQUIREMENT_GAP`
- **결정 주체**: `PLANNER`
- **발생 이유**: 현재 설계서에 공휴일 처리 규칙이 없음
- **선택지 A**: 공휴일과 관계없이 15시간 유지
- **선택지 B**: 실제 근무 가능 일수에 따라 조정
- **개발 권장**: A
- **기획자 답변**: 대기

기획자는 질문, 선택지, 트레이드오프, 개발 권장안을 보고 제품 정책을 결정한다.

### 6. 진행을 막는 Finding에 Blocker를 연결한다

**Actor:** 개발자 + AI

결정 전에는 특정 작업을 진행할 수 없으면 `blocking=true`로 설정하고 Blocker를 만든다. Blocker는 결정 주체와 독립적이다.

예시는 다음과 같다:

- **Blocker**: 주 15시간 근무자의 공휴일 처리 방식 미확정
- **BlockedScope**: `IMPLEMENTATION / weekly-standard-time-calculation`
- **막힌 범위**: 주간 기준시간 계산 로직 설계 및 구현
- **현재 진행 가능**: 근무유형 조회 인터페이스, 기본 저장 구조
- **재개 조건**: 공휴일 포함 시 주간 기준시간 정책 확정

하나의 Blocker가 여러 범위를 막으면 `BlockedScope`를 여러 개 만든다. Scope 밖의 작업은 계속 진행한다.

### 7. 개발자가 검토 명세서를 확정한다

**Actor:** 개발자 + AI

개발자는 검토 결과와 근거를 로컬 검토 명세서에 기록한다.

검토 명세서는 다음 정보를 연결한다:

- `PlanningDocumentSnapshot`과 관련 `SourcePageSnapshot`
- Finding과 `EvidenceRef`
- 개발 Decision
- Open Question
- Blocker와 `BlockedScope`
- 기획자 답변
- 재검증 결과

### 8. 시스템이 기획자용 개발 검토를 Notion에 작성한다

**Actor:** 시스템

시스템은 ROOT 기획 페이지 아래의 시스템 소유 `개발 검토` 페이지에 결과를 작성한다.

기획자는 다음 정보만 확인한다:

- 현재 상태
- 개발자가 결정한 사항
- 자신이 답변해야 하는 Open Question
- Blocker
- Blocker가 막고 있는 업무 범위

페이지 구조와 answer slot 소유권은 [Notion 검토 결과 계약](../integration/notion-review-contract.md)을 따른다.

### 9. 기획자가 필요한 항목에 답변한다

**Actor:** 기획자

기획자는 개발 결정은 확인하고, 제품 결정이 필요한 Open Question에 답변한다.

기획자는 OpenQuestion의 `답변` toggle 안에 답변한다. 시스템은 내용 hash가 바뀌면 새 `PlannerAnswer`로 보존하며, 답변 자체를 확정 Decision으로 취급하지 않는다.

### 10. 개발자가 기획자 답변을 재검증한다

**Actor:** 개발자 + AI

개발자는 기획자의 답변을 코드베이스, 기존 정책, 다른 설계 결정과 다시 대조한다.

충돌이 없으면 `Decision(owner=PLANNER)`을 확정한다. 추가 충돌이나 새로운 질문이 생기면 관련 Finding과 Open Question만 다시 연다.

Blocker의 재개 조건을 충족하면 해당 Blocker를 해결하고 `BlockedScope`에 묶인 작업을 다시 진행한다.

### 11. 개발자가 첫 최종설계 Revision을 확정한다

**Actor:** 개발자

구현에 필요한 Finding이 해결되고 최종설계 확정을 막는 Blocker가 없으면 첫 `FinalSpecRevision`을 만든다.

Revision은 다음 정보를 고정한다:

- 반영한 `PlanningDocumentSnapshot`
- 개발자 Decision
- 기획자 Decision
- 기존 정책과 코드베이스 제약
- 확정된 최종설계 내용

기획 원문을 구성한 SourcePageSnapshot과 PlanningDocumentSnapshot은 그대로 보존한다.

### 12. DevFlow로 개발한다

**Actor:** 개발자 + DevFlow

개발자는 현재 `FinalSpecRevision`을 DevFlow의 입력 기준으로 사용해 계획, 구현, 검증을 진행한다.

실제 구현이 완료되면 후속 연동에서 `ImplementationRef`를 통해 Revision과 commit을 연결한다.
