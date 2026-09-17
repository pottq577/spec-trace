---
meta:
  title: "최종설계의 결정 근거를 어떻게 추적하는가"
  contentType: "Conceptual"
  category: "Internal planning"
status: "Draft"
---

# 최종설계의 결정 근거를 어떻게 추적하는가

이 문서는 최종설계의 중요한 정책이 어떤 Notion 원문, 검토, Decision에서 나왔는지 설명한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 역추적 범위를 고정하며, 객체와 링크의 정확한 구조는 [추적 모델](../design/traceability-model.md)을 따른다.

## 최종설계에서 원문 근거까지 역추적한다

FinalSpecRevision의 중요한 정책은 다음 순서로 역추적할 수 있어야 한다:

1. FinalSpecRevision의 정책
2. 개발자 또는 기획자 Decision
3. Finding
4. EvidenceRef
5. PlanningDocumentSnapshot과 SourcePageSnapshot 원문 위치
6. 관련 기획, DerivedArtifact, 기존 코드 근거
7. 선택지와 트레이드오프
8. 채택 결과

이 관계를 통해 현재 구현이 최초 기획 원문과 다른 이유를 이후에도 확인할 수 있다.

## 실제 구현에서도 Decision까지 역추적한다

최종설계가 코드에 반영되면 ImplementationRef를 연결한다.

구현에서 역으로 조회하면 다음 관계를 확인할 수 있어야 한다:

1. commit / path
2. ImplementationRef
3. FinalSpecRevision
4. Decision
5. Finding
6. PlanningDocumentSnapshot
7. SourcePageSnapshot / CodeEvidenceRef

코드 근거와 구현 결과는 모두 commit SHA를 포함해 기준점을 고정한다. 브랜치명이나 최신 파일 위치만으로 과거 근거를 식별하지 않는다.

## 변경된 Decision도 과거 이력을 유지한다

기획 변경으로 기존 Decision이 바뀌면 이전 Decision을 삭제하지 않는다.

- 기존 근거가 깨지면 이전 Decision을 `INVALIDATED`로 표시한다
- 대체 결정을 확정하면 새 Decision에서 `supersedes_decision_id`를 연결한다
- 새 Decision을 반영한 FinalSpecRevision을 생성한다
- 과거 Revision과 구현은 당시 유효했던 Decision에 계속 연결한다

이 방식으로 현재 기준과 과거 구현 기준을 동시에 설명할 수 있다.
