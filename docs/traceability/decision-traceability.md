---
meta:
  title: "최종설계의 결정 근거를 어떻게 추적하는가"
  contentType: "Conceptual"
  category: "Internal planning"
status: "Draft"
---

# 최종설계의 결정 근거를 어떻게 추적하는가

이 문서는 최종설계의 중요한 정책이 어떤 원문과 검토, 결정에서 나왔는지 설명한다. 객체와 링크의 정확한 구조는 [추적 모델](../design/traceability-model.md)을 따른다.

## 최종설계에서 근거까지 역추적한다

최종설계서의 중요한 정책은 기획 원문부터 채택 결과까지 역추적할 수 있어야 한다.

추적 흐름은 다음과 같다:

1. `FinalSpecRevision`의 정책
2. 개발자 또는 기획자 `Decision`
3. `Finding`
4. `EvidenceRef`
5. `SourceSnapshot`, 관련 설계서, 기존 코드
6. 선택지와 트레이드오프
7. 채택 결과

이 추적 관계를 통해 몇 달 뒤에도 현재 코드가 최초 기획 원문과 다른 이유를 확인할 수 있다.

## 실제 구현에서도 결정까지 역추적한다

최종설계가 코드에 반영되면 `ImplementationRef`를 연결한다.

구현에서 역으로 조회하면 다음 관계를 확인할 수 있어야 한다:

```text
commit / path
→ ImplementationRef
→ FinalSpecRevision
→ Decision
→ Finding
→ SourceSnapshot / CodeEvidence
```

코드 근거와 구현 결과는 모두 commit SHA를 포함해 기준점을 고정한다. 브랜치명이나 최신 파일 위치만으로 과거 근거를 식별하지 않는다.

## 변경된 결정도 과거 이력을 유지한다

기획 변경으로 기존 결정이 바뀌면 이전 Decision을 삭제하지 않는다.

- 기존 근거가 깨지면 기존 Decision을 `INVALIDATED`로 표시한다
- 대체 결정을 확정하면 새 Decision에서 `supersedes_decision_id`를 연결한다
- 새 Decision을 반영한 `FinalSpecRevision`을 생성한다
- 과거 Revision과 구현은 당시 유효했던 Decision에 계속 연결한다

이 방식으로 현재 기준과 과거 구현 기준을 동시에 설명할 수 있다.
