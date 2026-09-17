---
meta:
  title: "AI 분석 후보를 언제 추적 이력으로 채택하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# AI 분석 후보를 언제 추적 이력으로 채택하는가

이 문서는 Source Diff와 Impact Analysis 후보를 개발자가 검증하고 채택하는 공통 계약을 정의한다. 자동 분석 결과는 이 절차를 통과하기 전까지 원문 이력이나 개발 상태를 바꾸지 않는다.

## 분석 결과와 도메인 이력을 분리한다

시스템은 분석기가 만든 결과를 `AnalysisProposal`로 보존한다. `AnalysisProposal`은 AI, Codex, ChatGPT, 규칙 기반 분석기, 개발자 수동 입력처럼 어떤 생성 경로에서도 같은 검증 절차를 사용한다.

정식 도메인 객체는 채택 결과에서만 생성한다:

- Source Diff proposal 채택 결과 → `ChangeItem`
- Impact Analysis proposal 채택 결과 → `ImpactLink`
- 검토 중 발견한 문제 채택 결과 → `Finding`
- 개발자가 결론까지 확정한 결과 → `Decision(owner=DEVELOPER)`

## AnalysisProposal은 생성 출처와 기준점을 고정한다

proposal은 최소한 다음 값을 가진다:

- `analysis_proposal_id`
- `analysis_type`: `SOURCE_DIFF` 또는 `IMPACT`
- `subject_ref`: `change_set_id` 또는 분석 대상 참조
- `input_snapshot_refs`
- `code_baseline`, 사용하지 않으면 빈 값
- `contract_version`
- `producer_type`: `AI`, `RULE`, `DEVELOPER`
- `producer_ref`: 모델명, agent 실행 ID, 규칙 버전 같은 출처
- `payload`
- `payload_hash`
- `status`
- `created_at`

prompt 전문이나 모델의 비공개 추론 과정은 필수 이력으로 저장하지 않는다. 채택 판단에 필요한 구조화 결과와 근거만 보존한다.

## proposal 상태는 네 값으로 관리한다

`AnalysisProposal.status`는 다음 값을 사용한다:

- `PROPOSED`: 검증 대기
- `STALE`: 기준 Snapshot이나 코드 freshness를 다시 확인해야 함
- `RESOLVED`: 모든 후보가 채택 또는 거절돼 검토가 끝남
- `SUPERSEDED`: 새 proposal이 이 실행 결과를 대체함

proposal 안의 각 candidate는 `PENDING`, `ADOPTED`, `REJECTED`, `SUPERSEDED` 상태를 가진다. candidate 일부만 처리한 상태에서도 proposal 자체는 `PROPOSED`를 유지한다.

## 개발자는 근거와 기준점부터 검증한다

후보를 채택하기 전에 시스템은 다음 검증을 실행한다:

1. proposal이 참조한 Snapshot이 존재한다
2. proposal payload hash가 저장 당시 값과 같다
3. candidate evidence가 해당 Snapshot이나 commit에서 다시 해석된다
4. Source Diff coverage가 완전하다
5. Impact Analysis scope manifest가 완전하다
6. 코드 관련 후보는 현재 개발 기준과 freshness 조건을 만족한다

검증이 실패하면 candidate를 채택할 수 없다. 기준점이 오래된 경우 proposal을 `STALE`로 전환한다.

## 개발자 검토 동작은 불변 ReviewAction으로 기록한다

후보별 검토는 `ReviewAction`으로 남긴다. 최소 필드는 다음과 같다:

- `review_action_id`
- `analysis_proposal_id`
- `candidate_id`
- `action`: `ADOPT`, `EDIT_AND_ADOPT`, `REJECT`, `SPLIT`, `MERGE`
- `reviewed_payload`: 수정 채택 시 최종 payload
- `reviewer`
- `reason`, 거절 또는 수정 시 필수
- `created_at`

ReviewAction은 수정하지 않는다. 검토를 다시 해야 하면 새 action을 추가하고 이전 결과를 대체 관계로 연결한다.

## Source Diff 채택은 ChangeItem을 만든다

`ADOPT` 또는 `EDIT_AND_ADOPT`된 Source Diff candidate마다 불변 `ChangeItem`을 만든다. `ChangeItem`은 다음 정보를 고정한다:

- `change_item_id`
- `change_set_id`
- `classification`
- `summary`
- 기준·대상 `SourceLocator`
- `physical_change_refs`
- `source_proposal_id`
- `source_review_action_id`

`SPLIT`과 `MERGE`는 수정된 후보를 새 proposal revision으로 만든 뒤 각각 다시 채택한다. 원본 candidate를 직접 덮어쓰지 않는다.

## Impact 채택은 ImpactLink와 상태 전이를 분리한다

Impact candidate를 채택하면 먼저 불변 `ImpactLink`를 만든다. 이후 별도 transition 단계가 `proposed_action`과 대상 상태를 검증해 변경한다.

판정별 기본 동작은 다음과 같다:

- `UNAFFECTED`: 대상 상태 유지
- `REVIEW_REQUIRED`: 기존 Finding을 `REOPENED`하거나 새 Finding 생성
- `INVALIDATED`: 대상 Decision을 `INVALIDATED`로 전환하고 관련 Finding 재검토
- `IMPLEMENTATION_CHANGE_REQUIRED`: 변경 작업과 연결할 Finding 또는 구현 작업 참조 생성

상태 전이 중 실패해도 채택된 `ImpactLink`를 삭제하지 않는다. 재실행은 같은 link를 기준으로 필요한 transition만 이어서 처리한다.

## 개발자 Decision은 별도 채택 행위다

Impact Analysis가 특정 해결안을 제안해도 자동으로 Decision을 만들지 않는다. 개발자가 Finding의 근거, 선택지, 트레이드오프를 검토하고 결론을 채택해야 `Decision(owner=DEVELOPER)`을 만든다.

기획자 결정이 필요한 Finding은 `OpenQuestion`을 만든다. 분석기가 기획자 대신 제품 정책을 확정할 수 없다.

## ChangeSet 완료는 모든 후보와 후속 상태를 확인한다

`ChangeSet`은 다음 순서로 상태를 진행한다:

1. `PENDING_SOURCE_DIFF`
2. `SOURCE_DIFF_PROPOSED`
3. `SOURCE_DIFF_ADOPTED`
4. `IMPACT_PROPOSED`
5. `COMPLETED`

`WORDING_ONLY`와 `IRRELEVANT`만 존재하고 개발자가 영향 분석 생략을 채택하면 `SOURCE_DIFF_ADOPTED`에서 바로 `COMPLETED`로 전환할 수 있다.

Impact Analysis가 필요한 경우 모든 Impact candidate를 해결하고 필요한 Finding 생성·재개까지 완료해야 `COMPLETED`로 전환한다. OpenQuestion 답변 대기는 ChangeSet 완료를 막지 않고 해당 ReviewCycle 상태가 이어서 관리한다.

## 외부 에이전트 결과도 같은 import 계약을 사용한다

MVP는 특정 모델 API에 종속되지 않는다. 시스템은 분석 입력 packet을 내보내고 구조화된 proposal을 다시 가져올 수 있어야 한다.

개발자가 ChatGPT, Codex, 다른 agent를 사용해 분석해도 `contract_version`, 기준 Snapshot, 근거 locator가 맞으면 같은 `AnalysisProposal`로 등록한다. 이 구조는 기존 에이전트 사용 방식을 유지하면서 시스템이 채택 이력만 책임지게 한다.

## 실행 계약은 다음 시나리오를 만족해야 한다

구현은 최소한 다음 사례를 검증해야 한다:

- AI가 만든 candidate는 개발자 action 전까지 도메인 상태를 바꾸지 않는다
- 수정 채택은 원본 candidate와 최종 payload를 모두 추적한다
- Source Diff 채택 결과만 `ChangeItem`이 된다
- Impact 채택 결과를 먼저 저장하고 상태 전이가 실패해도 재개할 수 있다
- 코드 기준점이 오래된 candidate는 `STALE` 상태에서 채택을 막는다
- `WORDING_ONLY`만 있는 ChangeSet은 명시적 생략 후 완료할 수 있다
- 외부 ChatGPT나 Codex 결과를 같은 proposal contract로 가져올 수 있다

## 다음 단계는 실패와 재시도 정책을 통합하는 일이다

다음 patch는 Notion 호출, 수집, 분석 import, 저장, projection 동기화의 오류 분류와 재시도 책임을 하나의 실행 정책으로 고정한다.
