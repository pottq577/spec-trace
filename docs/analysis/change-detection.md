---
meta:
  title: "새 Snapshot에서 어떤 변경 분석을 시작하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 새 Snapshot에서 어떤 변경 분석을 시작하는가

이 문서는 새 `PlanningDocumentSnapshot`이 확정된 뒤 `ChangeSet`을 만드는 조건과 물리적 변경 범위를 정의한다. 의미 분류는 [Source Diff 실행 계약](./source-diff.md)에서 다룬다.

## 변경 감지는 새 Snapshot 확정 뒤에만 실행한다

변경 감지는 [Notion 원문 수집 실행](../integration/notion-source-collection.md)의 결과가 `SNAPSHOT_CREATED`일 때 시작한다. 수집 실패나 불안정 상태에서는 변경 분석 객체를 만들지 않는다.

최초 Snapshot에는 비교 기준이 없으므로 `ChangeSet`을 만들지 않는다. 최초 Snapshot은 신규 설계 검토용 `INITIAL` ReviewCycle의 입력이 된다.

두 번째 Snapshot부터 다음 조건을 모두 만족하면 `ChangeSet`을 만든다:

1. 대상 Snapshot에 `previous_snapshot_id`가 있다
2. 기준 Snapshot과 대상 Snapshot의 `planning_document_id`가 같다
3. 두 Snapshot의 `aggregate_hash`가 다르다
4. 같은 기준 Snapshot과 대상 Snapshot을 연결한 `ChangeSet`이 없다

## 비교 기준은 직전 원문 Snapshot으로 고정한다

Source Diff는 대상 Snapshot의 `previous_snapshot_id`를 기준으로 사용한다. 현재 `FinalSpecRevision`이나 마지막 검토 완료 Snapshot을 원문 비교 기준으로 사용하지 않는다.

이 규칙은 기획자가 연속으로 여러 번 수정한 경우에도 각 원문 변화의 계보를 보존한다. 구현 영향은 이후 Impact Analysis가 현재 개발 기준과 별도로 비교한다.

## ChangeSet은 Snapshot 쌍에 대해 하나만 만든다

`ChangeSet`의 멱등성 key는 다음 값으로 구성한다:

```text
(planning_document_id, baseline_planning_snapshot_id, target_planning_snapshot_id)
```

같은 key가 이미 존재하면 기존 `ChangeSet`을 재사용한다. 재시도 때문에 동일한 변경 분석이 중복 생성되면 안 된다.

`ChangeSet.analysis_status`는 다음 값을 사용한다:

- `PENDING_SOURCE_DIFF`: 물리적 변경 범위를 계산했고 의미 분석을 시작하지 않음
- `SOURCE_DIFF_PROPOSED`: Source Diff 후보가 생성됐지만 개발자가 채택하지 않음
- `SOURCE_DIFF_ADOPTED`: 모든 Source Diff 결과를 개발자가 채택함
- `IMPACT_PROPOSED`: 영향 분석 후보가 생성됐지만 개발자가 채택하지 않음
- `COMPLETED`: 필요한 영향 판정과 후속 검토 생성까지 끝남
- `FAILED`: 현재 단계가 종료 오류로 끝났으며 재실행이 필요함

실패 후 재실행은 같은 `ChangeSet`을 이어서 사용한다.

## 먼저 페이지 단위 물리적 변경 범위를 계산한다

변경 감지는 의미 분석 전에 Snapshot 구성 차이를 결정론적으로 계산한다. 이 결과는 저장 가능한 도메인 이력보다 Source Diff 입력을 만드는 실행 자료다.

각 페이지는 `notion_page_id`를 기준으로 매칭한다. 물리적 변경 유형은 다음과 같다:

- `PAGE_ADDED`: 대상 Snapshot에만 존재함
- `PAGE_REMOVED`: 기준 Snapshot에만 존재함
- `CONTENT_CHANGED`: 양쪽에 존재하고 `content_hash`가 다름
- `PARENT_CHANGED`: 양쪽에 존재하고 부모 page가 다름
- `ROLE_CHANGED`: 양쪽에 존재하고 `ROOT` 또는 `COMPOSED_CHILD` 역할이 다름

하나의 page는 여러 유형을 동시에 가질 수 있다. 예를 들어 내용과 부모가 함께 바뀌면 `CONTENT_CHANGED`와 `PARENT_CHANGED`를 모두 기록한다.

## 변경 범위는 원문 근거를 직접 참조한다

Source Diff 입력용 각 page change는 다음 값을 가진다:

- `notion_page_id`
- `change_types`
- `baseline_source_page_snapshot_id`, 추가된 page에서는 빈 값
- `target_source_page_snapshot_id`, 제거된 page에서는 빈 값
- `baseline_parent_notion_page_id`
- `target_parent_notion_page_id`
- `baseline_content_hash`
- `target_content_hash`

본문 전체를 중복 저장하지 않는다. 분석 실행 시 Snapshot의 `content_ref`에서 필요한 원문을 읽는다.

## aggregate hash 차이는 반드시 설명 가능해야 한다

새 `PlanningDocumentSnapshot`의 `aggregate_hash`가 직전 Snapshot과 다르면 page change가 하나 이상 나와야 한다. page change가 비어 있으면 Snapshot 생성 또는 변경 감지 구현 오류로 처리한다.

반대로 page change가 하나 이상인데 aggregate hash가 같으면 hash 구현 오류로 처리한다. 두 경우 모두 `CHANGE_DETECTION_INCONSISTENT` 오류를 남기고 Source Diff를 시작하지 않는다.

## ChangeItem은 Source Diff 채택 시 생성한다

물리적 page change 자체는 `ChangeItem`이 아니다. `ChangeItem`은 기획자가 바꾼 의미를 `POLICY_CHANGED`, `REQUIREMENT_ADDED`, `REQUIREMENT_REMOVED`, `CONDITION_CHANGED`, `WORDING_ONLY`, `IRRELEVANT` 중 하나로 분류한 결과다.

Source Diff 분석기가 후보를 제안하고 개발자가 검증해 채택할 때 `ChangeItem`을 확정한다. 하나의 page change에서 여러 `ChangeItem`이 나올 수 있고, 여러 page change가 하나의 의미 변경을 공동으로 뒷받침할 수도 있다.

## 변경 감지는 후속 실행을 한 번만 예약한다

새 `ChangeSet`을 만들면 Source Diff 실행을 한 번 예약한다. 같은 `ChangeSet`이 이미 진행 중이면 수집기가 추가 작업을 중복 예약하지 않는다.

새로운 더 최신 Snapshot이 들어와도 기존 `ChangeSet`을 삭제하지 않는다. 각 Snapshot 쌍의 변경 이력을 순서대로 유지하고 최신 Snapshot까지 분석이 따라잡도록 작업을 이어간다.

## 실행 계약은 다음 시나리오를 만족해야 한다

구현은 최소한 다음 사례를 검증해야 한다:

- 최초 Snapshot은 `ChangeSet` 없이 신규 검토 대상으로 전달된다
- 두 번째 Snapshot은 직전 Snapshot과 하나의 `ChangeSet`을 만든다
- 같은 Snapshot 쌍을 재처리해도 `ChangeSet`은 하나만 존재한다
- page 추가와 제거를 각각 `PAGE_ADDED`, `PAGE_REMOVED`로 찾는다
- 내용과 부모가 동시에 바뀌면 두 물리적 변경 유형을 유지한다
- aggregate hash 차이를 page change로 설명하지 못하면 Source Diff를 시작하지 않는다

## 다음 단계는 의미 변경을 ChangeItem으로 확정하는 일이다

다음 patch는 물리적 page change와 두 Snapshot의 원문을 입력으로 사용해 Source Diff 후보를 생성한다. 개발자가 후보를 검증하고 채택하는 정확한 입출력 계약도 함께 고정한다.
