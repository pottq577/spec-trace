---
meta:
  title: "원문 변경을 어떤 의미 단위로 분류하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 원문 변경을 어떤 의미 단위로 분류하는가

이 문서는 `ChangeSet`의 물리적 page change를 의미 단위 Source Diff 후보로 변환하는 입출력 계약을 정의한다. 후보는 개발자가 검증해 채택한 뒤 불변 `ChangeItem`이 된다.

## Source Diff는 원문 변화만 설명한다

Source Diff의 질문은 “기획자가 이전 원문에서 무엇을 바꿨는가”다. 현재 코드, 기존 Decision, `FinalSpecRevision`의 영향은 이 단계에서 판정하지 않는다.

입력은 같은 `PlanningDocument`의 연속된 두 `PlanningDocumentSnapshot`과 [Snapshot 변경 감지](./change-detection.md)가 만든 물리적 page change다.

## 분석 요청은 불변 기준점을 가진다

하나의 Source Diff 요청은 다음 값을 고정한다:

- `source_diff_request_id`
- `change_set_id`
- `baseline_planning_snapshot_id`
- `target_planning_snapshot_id`
- `physical_changes`
- 분석에 사용할 원문 content reference
- `analysis_contract_version`

분석 중 더 최신 Snapshot이 생겨도 현재 요청의 기준점을 바꾸지 않는다. 최신 변경은 다음 `ChangeSet`에서 별도로 분석한다.

## 분석기는 변경된 원문 범위만 읽는다

기본 입력은 물리적 변경이 발생한 SourcePage의 기준·대상 원문이다. 의미를 판단하는 데 필요한 직접 부모와 인접 문맥은 함께 제공할 수 있다.

분석기가 관련 문서, 코드, 기존 Decision을 임의로 포함해 Source Diff 의미를 바꾸면 안 된다. 관련 정보는 Impact Analysis 단계에서 사용한다.

## SourceLocator는 Snapshot 안의 원문 위치를 가리킨다

각 후보는 이전 원문과 새 원문의 근거 위치를 `SourceLocator`로 연결한다. locator는 다음 값을 사용한다:

- `source_page_snapshot_id`
- `block_path`: canonical block tree의 0-based child index 배열
- `field`: block 안의 `rich_text`, `checked`, `language` 같은 의미 필드, 필요 없으면 빈 값
- `quote_hash`: 해당 위치의 canonical value hash

Notion block ID는 장기 근거 위치로 사용하지 않는다. page Snapshot과 canonical path를 함께 사용해 당시 원문을 다시 찾는다.

## 후보는 하나의 의미 변경을 설명한다

`SourceDiffCandidate`는 다음 필드를 가진다:

- `candidate_id`: 현재 분석 실행 안에서 사용하는 임시 식별자
- `classification`
- `summary`
- `rationale`: 분류 근거를 설명하는 짧은 판단 요약
- `baseline_evidence`: 0개 이상의 `SourceLocator`
- `target_evidence`: 0개 이상의 `SourceLocator`
- `physical_change_refs`: 근거가 된 page change 식별자
- `supersedes_candidate_ids`: 분석기 재실행에서 이전 후보를 대체할 때 사용

`candidate_id`는 도메인 식별자가 아니다. 개발자가 채택할 때 시스템이 `change_item_id`를 발급한다.

## 의미 분류는 여섯 값으로 고정한다

후보는 다음 값 중 하나를 사용한다:

- `POLICY_CHANGED`: 기존 정책의 규칙이나 결과가 달라짐
- `REQUIREMENT_ADDED`: 이전에 없던 요구사항이 추가됨
- `REQUIREMENT_REMOVED`: 기존 요구사항이 삭제됨
- `CONDITION_CHANGED`: 조건, 범위, 예외, 적용 대상이 달라짐
- `WORDING_ONLY`: 표현만 달라지고 요구사항 의미는 같음
- `IRRELEVANT`: 제품·개발 검토 범위에 영향을 주는 요구사항 의미가 아님

분류가 애매하면 여러 후보를 만들지 않고 `POLICY_CHANGED`나 `CONDITION_CHANGED`로 추측하지 않는다. 분석 결과 자체를 `NEEDS_CLARIFICATION` 실행 상태로 반환하고 개발자가 원문을 확인한다.

## 추가와 삭제는 한쪽 근거만 가질 수 있다

`REQUIREMENT_ADDED`는 `target_evidence`가 필수이고 `baseline_evidence`는 비어 있을 수 있다. `REQUIREMENT_REMOVED`는 반대로 기준 원문 근거가 필수다.

그 밖의 분류는 원칙적으로 양쪽 근거를 가진다. page 전체 추가나 제거처럼 직접 대응 위치가 없는 경우에는 page root를 나타내는 빈 `block_path`를 사용한다.

## 하나의 물리적 변경에서 여러 ChangeItem이 나올 수 있다

기획자가 한 문단에서 두 정책을 함께 수정하면 후보를 의미 단위로 나눈다. 반대로 같은 정책을 여러 page에서 동시에 고쳤다면 하나의 후보가 여러 page change를 참조할 수 있다.

후보를 나누는 기준은 후속 영향과 결정을 독립적으로 추적할 수 있는가다. 하나의 후보가 서로 다른 제품 결정을 묶으면 분리한다.

## 모든 물리적 변경은 분석 범위에서 설명돼야 한다

Source Diff 응답은 각 page change가 어떤 후보로 설명되는지 coverage를 제공한다. 한 page change가 여러 후보에 연결될 수 있다.

응답 완료 조건은 다음과 같다:

1. 모든 물리적 page change가 하나 이상의 후보에 연결된다
2. 모든 후보가 존재하는 `physical_change_refs`만 참조한다
3. 모든 `SourceLocator`가 기준 또는 대상 Snapshot에서 해석된다
4. `quote_hash`가 해당 canonical value와 일치한다
5. 후보 간 의미 범위가 중복되면 중복 이유가 명시된다

조건을 만족하지 못하면 `SOURCE_DIFF_INCOMPLETE`로 처리하고 채택 단계로 넘기지 않는다.

## 분석 응답은 구조화된 후보만 반환한다

Source Diff 응답은 다음 상위 구조를 사용한다:

```json
{
  "change_set_id": "change_set_id_here",
  "analysis_contract_version": "1",
  "status": "PROPOSED",
  "candidates": [],
  "coverage": []
}
```

자유 형식 보고서 전문을 정식 입력으로 사용하지 않는다. 개발자가 읽을 설명은 `summary`와 `rationale`에 한정하고 원문 근거는 `SourceLocator`로 분리한다.

## 개발자 채택이 ChangeItem을 확정한다

개발자는 후보별로 다음 동작 중 하나를 선택한다:

- `ADOPT`: 후보 내용을 그대로 채택
- `EDIT_AND_ADOPT`: 분류, 요약, 근거를 수정해 채택
- `REJECT`: 잘못된 후보로 폐기
- `SPLIT`: 하나의 후보를 둘 이상의 후보로 나눠 다시 검토
- `MERGE`: 여러 후보를 하나의 의미 변경으로 합쳐 다시 검토

채택된 결과만 `ChangeItem`이 된다. `ChangeItem`은 채택 시점의 classification, summary, evidence, 원본 candidate 참조를 불변 이력으로 보존한다.

## WORDING_ONLY와 IRRELEVANT는 영향 분석을 기본 생략한다

`WORDING_ONLY`와 `IRRELEVANT`로 채택한 `ChangeItem`은 기본적으로 Impact Analysis 입력에서 제외한다. 개발자가 영향 가능성을 발견하면 명시적으로 포함할 수 있다.

나머지 네 분류는 Impact Analysis 대상으로 전달한다. Source Diff 단계는 기존 Decision을 재개하거나 무효화하지 않는다.

## 실행 계약은 다음 시나리오를 만족해야 한다

구현은 최소한 다음 사례를 검증해야 한다:

- 한 문단의 두 독립 정책 변경을 두 후보로 분리한다
- 여러 page의 같은 정책 변경을 하나의 후보로 묶을 수 있다
- 추가된 요구사항은 대상 원문 근거만으로 표현할 수 있다
- 삭제된 요구사항은 기준 원문 근거만으로 표현할 수 있다
- 모든 물리적 page change가 coverage에 포함되지 않으면 채택을 막는다
- `WORDING_ONLY` 채택 결과는 기본 Impact Analysis 대상에서 제외한다
- 후보를 수정해 채택해도 원본 후보와 개발자 수정 결과를 함께 추적한다

## 다음 단계는 현재 개발 기준에 대한 영향을 판정하는 일이다

다음 patch는 채택된 `ChangeItem`을 현재 `FinalSpecRevision`, Decision, 코드, 관련 기획과 대조하는 Impact Analysis 입출력 계약을 정의한다.
