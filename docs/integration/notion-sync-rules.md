---
meta:
  title: "Notion 동기화를 어떻게 중복 없이 복구 가능하게 실행하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# Notion 동기화를 어떻게 중복 없이 복구 가능하게 실행하는가

이 문서는 Notion 원문 수집, 개발 검토 출력, 기획자 답변 수집을 반복 실행해도 같은 결과를 만드는 동기화 규칙을 정의한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 입력과 출력의 idempotency, 부분 실패, 삭제와 이동, 자기 변경 루프 방지 기준을 고정한다.

## 동기화는 입력과 출력을 독립 단계로 실행한다

하나의 동기화 실행은 다음 단계를 순서대로 처리한다:

1. 모니터링 대상 `PlanningDocument`를 확인한다
2. 원문 페이지 트리를 수집한다
3. 새 `PlanningDocumentSnapshot`이 필요한지 판정한다
4. 기획자 answer slot 변경을 수집한다
5. 내부 상태 변경을 반영한다
6. 개발 검토 projection을 Notion에 동기화한다

입력 수집이 실패해도 기존 확정 Snapshot과 Decision을 수정하지 않는다. 출력 동기화가 실패해도 내부 확정 상태를 되돌리지 않는다.

## 입력 Snapshot은 내용 기반 idempotency key를 사용한다

중복 원문 수집을 막기 위해 다음 키를 사용한다:

- `SourcePageSnapshot`: `source_page_id + content_hash`
- `PlanningDocumentSnapshot`: `planning_document_id + aggregate_hash`

같은 키가 이미 존재하면 새 Snapshot을 만들지 않는다.

Notion `last_edited_time`은 변경 후보 탐색과 수집 일관성 검증에만 사용한다. Snapshot 정체성을 결정하는 키로 사용하지 않는다.

## 개발 검토 출력은 내부 객체 ID로 같은 Notion 대상을 찾는다

출력 projection은 다음 유일 관계를 유지한다:

- `planning_document_id → review_page_id`
- `open_question_id → question_section_block_id`
- `open_question_id → current_answer_slot_block_id`
- `blocker_id → blocker_section_block_id`

동일한 내부 객체를 다시 출력할 때 새 페이지나 새 질문 섹션을 중복 생성하지 않는다. 기존 매핑이 유효하면 해당 블록을 갱신한다.

시스템 소유 블록은 내부 상태에서 렌더링한 결과를 canonical output으로 취급한다.

## 답변 수집은 answer slot 내용 hash로 중복을 막는다

시스템은 현재 `answer_slot_block_id` 아래의 블록 트리를 정규화해 `answer_hash`를 계산한다.

다음 조건을 모두 만족할 때만 새 `PlannerAnswer`를 만든다:

1. 답변 내용이 비어 있지 않다
2. 현재 `answer_hash`가 마지막으로 수집한 hash와 다르다
3. answer slot이 현재 Open Question의 active slot이다

공백, 블록 식별자, 마지막 수정 시각만 달라진 경우 새 답변 이력을 만들지 않는다.

## 시스템 출력은 원문 Snapshot에서 제외한다

시스템이 만든 `개발 검토` 페이지와 시스템 소유 블록은 `SourcePage` 수집 대상에서 제외한다. 개발 검토 페이지 생성, 상태 갱신, 질문 갱신은 `PlanningDocumentSnapshot`을 변경하지 않는다.

이 규칙으로 다음 자기 변경 루프를 차단한다:

1. 기획 변경
2. 개발 검토 출력 변경
3. 원문 변경으로 오인
4. 새 ReviewCycle 생성

기획자 answer slot 역시 원문에 포함하지 않는다. 답변은 `PlannerAnswer` 입력으로만 처리한다.

## 부분 실패는 성공한 항목을 보존하고 재실행한다

각 동기화 실행은 `sync_run_id`와 항목별 처리 상태를 기록한다. 저장 스키마는 이후 데이터베이스 설계에서 확정한다.

부분 실패가 발생하면 다음 규칙을 적용한다:

- SourcePage 하나라도 수집 중 변경되면 전체 `PlanningDocumentSnapshot` 후보를 확정하지 않는다
- 일부 Notion 출력 블록 작성에 실패하면 성공한 매핑을 유지하고 실패한 항목만 재시도한다
- answer 수집 후 출력 갱신이 실패해도 이미 생성한 `PlannerAnswer`를 취소하지 않는다
- 재실행은 내부 객체 ID와 idempotency key를 사용해 중복 생성을 막는다

실패한 동기화는 마지막으로 확정된 정상 상태를 손상시키지 않아야 한다.

## 삭제와 archive를 데이터 손실로 처리하지 않는다

Notion 객체가 사라질 때 다음 규칙을 적용한다:

- `ROOT` 페이지가 archive되거나 접근할 수 없으면 `PlanningDocument`를 source unavailable로 표시한다
- `COMPOSED_CHILD`가 archive되거나 트리에서 빠지면 다음 문서 Snapshot에서 제외한다
- `개발 검토` 페이지가 삭제되면 새 projection 페이지를 만든다
- answer slot이 삭제되면 새 active slot을 만들고 기존 `PlannerAnswer` 이력은 유지한다

과거 Snapshot과 Decision을 cascade delete하지 않는다.

## 페이지 이동은 Notion ID와 부모 관계로 판정한다

같은 `notion_page_id`가 다른 위치로 이동해도 같은 `SourcePage`다. 다만 포함 관계가 바뀌면 새 `PlanningDocumentSnapshot`을 만들 수 있다.

다음 경우를 구분한다:

- 같은 기획 트리 안에서 부모만 변경: SourcePage 유지, parent 관계 변경
- 기획 트리 밖으로 이동: 기존 기획의 다음 Snapshot에서 제외
- 다른 모니터링 `PlanningDocument`의 하위 페이지로 이동: 기존 문서에서 제거하고 새 문서에 포함
- 로컬 파일만 이동 또는 rename: Source 관계와 Snapshot에 영향 없음

## 다른 기획 문서 참조는 역참조 영향 후보를 만든다

`SourceReference` 대상이 다른 모니터링 `PlanningDocument`라면 시스템은 역참조 관계를 유지한다.

참조 대상에 새 `PlanningDocumentSnapshot`이 확정되면 참조한 문서의 원문 Snapshot을 자동 생성하지 않는다. 대신 해당 문서를 Impact Analysis 후보 큐에 추가한다.

개발자가 실제 영향이 없다고 판정하면 기존 Finding과 Decision을 그대로 유지한다.

## 동기화 재개는 내부 확정 상태에서 시작한다

프로세스 재시작이나 일시적 Notion 연동 실패 후에는 Notion 화면을 전체 진실로 재구성하지 않는다. 내부에 마지막으로 확정한 Snapshot, PlannerAnswer, Decision, output mapping을 기준으로 Notion 현재 상태와 reconcile한다.

reconcile은 다음 순서로 진행한다:

1. `ROOT`와 source page mapping을 확인한다
2. 개발 검토 `review_page_id` 존재 여부를 확인한다
3. active Open Question의 answer slot mapping을 확인한다
4. 새 원문과 새 답변을 수집한다
5. 누락된 시스템 출력만 복구한다

이 규칙은 재시작 후에도 같은 원문과 답변을 중복 등록하지 않게 한다.
