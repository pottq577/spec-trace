---
meta:
  title: "기획 변경과 개발 영향을 어떻게 구분해 분석하는가"
  contentType: "Conceptual"
  category: "Internal planning"
status: "Draft"
---

# 기획 변경과 개발 영향을 어떻게 구분해 분석하는가

이 문서는 기획자가 바꾼 내용과 그 변경이 현재 설계·코드에 미치는 영향을 두 단계로 분리하는 이유를 설명한다. 상세 객체와 판정값은 [추적 모델](../design/traceability-model.md)을 따른다.

## 변경 추적은 두 질문을 분리한다

개발 중 기획 변경은 원문 변경 비교와 영향 분석을 순서대로 수행한다. 두 비교는 서로 다른 질문에 답한다.

### 원문 변경 비교

원문 변경 비교(Source Diff)는 “기획자가 실제로 무엇을 바꿨는가”를 확인한다.

비교 대상은 다음과 같다:

- 이전 `SourceSnapshot`
- 신규 `SourceSnapshot`

물리적 변경은 `content_hash`로 확인한다. 내용이 달라졌다면 의미 분석을 수행하고 `ChangeItem`으로 분류한다.

의미 분류는 다음 값을 사용한다:

- `POLICY_CHANGED`
- `REQUIREMENT_ADDED`
- `REQUIREMENT_REMOVED`
- `CONDITION_CHANGED`
- `WORDING_ONLY`
- `IRRELEVANT`

`WORDING_ONLY`는 기존 Decision을 자동으로 다시 열지 않는다.

### 영향 분석

영향 분석(Impact Analysis)은 “이번 의미 변경이 현재 설계와 개발에 무엇을 바꾸는가”를 확인한다.

신규 Snapshot과 다음 대상을 대조한다:

- 현재 `FinalSpecRevision`
- 기존 Finding과 Decision
- 현재 코드베이스
- 선행 작업
- 관련 설계서
- 실제 구현을 나타내는 `ImplementationRef`

각 `ChangeItem`은 `ImpactLink`를 통해 영향받은 객체와 연결한다.

영향 판정은 다음 값을 사용한다:

- `UNAFFECTED`
- `REVIEW_REQUIRED`
- `INVALIDATED`
- `IMPLEMENTATION_CHANGE_REQUIRED`

이 구분을 통해 기획자가 새로 수정한 내용과 기존 개발 결정 때문에 이미 달라진 내용을 섞지 않는다.

## 자동 분석과 확정 판정을 구분한다

AI는 의미 변경과 영향 범위를 제안할 수 있다. 개발자는 원문, 현재 코드, 기존 Decision을 확인하고 최종 판정을 채택한다.

자동 분석만으로 기존 Decision을 무효화하거나 Blocker를 만들지 않는다. 확정된 영향 판정이 후속 재검토와 상태 전이의 기준이 된다.
