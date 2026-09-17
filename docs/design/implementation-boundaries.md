---
meta:
  title: "현재 단계에서 무엇을 확정하고 무엇을 후속 설계로 남기는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 현재 단계에서 무엇을 확정하고 무엇을 후속 설계로 남기는가

이 문서는 현재까지 확정한 업무 모델, Notion 입출력 계약, 원문 수집 실행 계약을 정리하고 다음 상세 설계 경계를 정의한다.
[문서 계획](../00_INDEX.md#문서-계획)의 현재 완료 지점을 판단하는 기준 문서다.

## 내부 도메인과 추적 모델을 확정했다

현재 단계에서 다음 내부 개념과 관계를 확정한다:

- Notion 데이터베이스 행 페이지를 기준으로 한 `PlanningDocument`
- ROOT와 COMPOSED_CHILD `SourcePage`
- 실제 페이지의 `SourcePageSnapshot`
- 기획 전체 버전인 `PlanningDocumentSnapshot`
- 외부 페이지 연결인 `SourceReference`
- 개발자 작성 문서인 `DerivedArtifact`
- `ReviewCycle`과 `Finding`
- `finding_type`, `decision_owner`, `blocking`의 독립 분류
- 개발자 Decision과 기획자 Decision
- `OpenQuestion`과 불변 `PlannerAnswer`
- `Blocker`와 복수 `BlockedScope`
- `FinalSpec`과 불변 `FinalSpecRevision`
- `ChangeSet`, `ChangeItem`, `ImpactLink`
- EvidenceRef와 commit SHA 기반 코드 근거
- ImplementationRef를 통한 최종설계와 실제 구현 연결
- 객체별 상태와 재개, 대체, 무효화 규칙

세부 정의는 [도메인 모델](./domain-model.md), [상태 모델](./state-model.md), [추적 모델](./traceability-model.md)을 따른다.

## Notion 입출력 계약을 확정했다

기획자의 기존 Notion 작성 방식을 유지하면서 내부 모델과 Notion을 연결하는 규칙을 다음 문서에서 확정한다:

- [Notion 원문 계약](../integration/notion-source-contract.md): 데이터베이스 행 식별, 하위 페이지 재귀 수집, Snapshot, 로컬 미러와 DerivedArtifact 분리
- [Notion 검토 결과 계약](../integration/notion-review-contract.md): 개발 검토 페이지, 시스템 소유 영역, answer slot, PlannerAnswer 수집
- [Notion 동기화 규칙](../integration/notion-sync-rules.md): idempotency, 부분 실패, 삭제와 이동, 자기 변경 루프 방지, reconcile

이 계약에 따라 PlanningDocument 하나는 Notion ROOT 페이지 하나와 그 하위 COMPOSED_CHILD 트리를 원문으로 가진다. 다른 기존 페이지 링크는 SourceReference로 처리하고 시스템 출력은 원문에서 제외한다.

## 원문 수집과 Snapshot 확정 실행 계약을 확정했다

[Notion 원문 수집 실행](../integration/notion-source-collection.md)은 원문 계약을 실제 처리 순서로 내린다. 현재 단계에서 다음 실행 결정을 확정한다:

- `PlanningDocument` 하나를 하나의 수집 실행 단위로 사용한다
- MVP는 5분 폴링을 기본 수집 주기로 사용한다
- 같은 기획 문서의 동시 수집을 직렬화한다
- 시작 트리와 종료 트리를 비교해 수집 중 변경 여부를 검증한다
- canonical content를 안정적으로 직렬화해 SHA-256 hash를 계산한다
- `aggregate_hash`가 달라질 때만 새 `PlanningDocumentSnapshot`을 만든다
- `UNCHANGED`, `SNAPSHOT_CREATED`, `SOURCE_UNSTABLE`, `SOURCE_UNAVAILABLE`, `COLLECTION_FAILED` 결과를 사용한다
- 실패한 수집은 마지막 정상 Snapshot을 수정하지 않는다

이 단계는 데이터베이스 테이블, 배포 구조, 작업 큐 구현을 정하지 않는다. 해당 기술 선택은 처리 계약이 끝난 뒤 애플리케이션 아키텍처에서 확정한다.

## Snapshot 간 변경 감지 계약을 확정했다

[Snapshot 변경 감지](../analysis/change-detection.md)는 새 Snapshot에서 `ChangeSet`을 만드는 조건을 고정한다. 직전 Snapshot을 원문 비교 기준으로 사용하고 page 추가, 제거, 내용, 부모, 역할 변경을 결정론적으로 계산한다.

`ChangeItem`은 이 단계에서 만들지 않는다. 물리적 page change와 원문 근거를 Source Diff에 전달하고 개발자가 의미 분석 결과를 채택할 때 확정한다.

## Source Diff 실행 계약을 확정했다

[Source Diff 실행](../analysis/source-diff.md)은 물리적 page change를 의미 단위 후보로 변환한다. 모든 물리적 변경의 coverage와 원문 위치를 검증하고 개발자가 채택한 결과만 불변 `ChangeItem`으로 만든다.

`WORDING_ONLY`와 `IRRELEVANT`는 기본 영향 분석 대상에서 제외한다. Source Diff 자체는 기존 Decision이나 Finding 상태를 변경하지 않는다.

## Impact Analysis 실행 계약을 확정했다

[Impact Analysis 실행](../analysis/impact-analysis.md)은 채택된 `ChangeItem`을 현재 FinalSpecRevision, Decision, 코드, 구현, 관련 기획과 대조한다. 분석 시점의 code baseline을 commit SHA로 고정하고 확인 범위를 scope manifest로 남긴다.

영향 후보는 `UNAFFECTED`, `REVIEW_REQUIRED`, `INVALIDATED`, `IMPLEMENTATION_CHANGE_REQUIRED` 중 하나를 제안한다. 후보 자체는 기존 객체 상태를 수정하지 않는다.

## 분석 후보 채택 계약을 확정했다

[분석 후보 채택](../analysis/adoption.md)은 AI와 외부 agent 결과를 `AnalysisProposal`로 보존하고 개발자 `ReviewAction`을 거친 결과만 도메인 이력으로 확정한다. Source Diff 채택은 `ChangeItem`, Impact 채택은 `ImpactLink`를 만든다.

MVP는 특정 모델 API를 필수로 사용하지 않는다. ChatGPT, Codex, 규칙 기반 분석기의 구조화 결과를 같은 import 계약으로 처리한다.

## 실행 오류와 재시도 계약을 확정했다

[연동 실패와 재시도](../integration/runtime-resilience.md)는 Notion 요청을 중앙 queue로 제한하고 오류별 retry ownership을 고정한다. 외부 write와 내부 저장은 분산 트랜잭션으로 묶지 않고 projection reconcile로 수렴한다.

이 문서까지 원문 수집과 변경 분석 실행 계약을 완료했다.

## 다음 단계는 애플리케이션 아키텍처다

이후 설계는 처리 계약을 구현 가능한 구조로 내린다. 순서는 다음과 같다:

1. 애플리케이션 아키텍처와 실행 경계
2. 저장소와 데이터베이스 스키마
3. 개발자 검토 등록 인터페이스
4. DevFlow 연동 계약
5. MVP end-to-end acceptance scenario
6. 구현 계획

기술 선택은 앞 단계의 계약을 구현하는 데 필요한 시점에 확정한다.

## 제품 완료 기준은 추적성과 업무 연속성을 함께 만족해야 한다

이 시스템은 다음 조건을 만족해야 한다:

- 기획자는 기존 Notion 작성 방식을 유지한다
- 데이터베이스 행 페이지와 `/페이지` 하위 페이지를 하나의 논리 기획으로 추적한다
- 로컬 분석 문서 변경을 기획 변경으로 오인하지 않는다
- 개발자는 신규 또는 변경된 기획을 놓치지 않는다
- 개발자는 의미 변경을 이전 PlanningDocumentSnapshot과 비교할 수 있다
- 개발자는 현재 코드와 정책에 대한 영향을 확인할 수 있다
- 기술적 결정은 근거와 함께 개발자가 확정할 수 있다
- 제품 결정은 충분한 맥락과 선택지를 포함해 기획자에게 전달된다
- Blocker가 있어도 BlockedScope 밖의 작업은 계속할 수 있다
- 기획자는 ROOT 아래 `개발 검토` 페이지에서 자신의 할 일을 파악한다
- 기획자 답변 수정 이력을 PlannerAnswer로 보존한다
- 기획자 답변은 개발자가 재검증한 뒤 Decision으로 채택한다
- FinalSpecRevision은 반영한 원문 Snapshot과 Decision을 역추적할 수 있다
- 실제 구현 commit에서 최종설계와 결정 근거까지 역추적할 수 있다
- 시스템 출력 변경이 원문 변경을 다시 발생시키는 동기화 루프가 없다
- 중복 실행과 부분 실패 후에도 같은 내부 상태로 수렴한다
