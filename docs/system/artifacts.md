---
meta:
  title: "기획부터 실제 구현까지 어떤 문서와 산출물을 관리하는가"
  contentType: "Conceptual"
  category: "Internal planning"
status: "Draft"
---

# 기획부터 실제 구현까지 어떤 문서와 산출물을 관리하는가

이 문서는 Notion 원문, 로컬 개발 산출물, 기획자용 개발 검토, 최종설계, 구현 참조가 서로 어떤 역할을 가지는지 설명한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 원문과 파생 산출물의 책임을 분리한다.

## Notion 기획 원문은 Source 계층에서 보존한다

기획자가 Notion 데이터베이스에 작성한 행 페이지와 `/페이지` 하위 페이지가 원문이다. 개발 검토 때문에 원문 내용을 수정하지 않는다.

시스템은 원문을 다음 단위로 보존한다:

- `PlanningDocument`: 데이터베이스 행 페이지를 기준으로 한 논리 기획
- `SourcePage`: ROOT와 COMPOSED_CHILD 실제 Notion 페이지
- `SourcePageSnapshot`: 실제 페이지의 불변 사본
- `PlanningDocumentSnapshot`: 해당 시점의 기획 전체 불변 버전
- `SourceReference`: 다른 기존 페이지를 참조하는 관계

세부 수집 규칙은 [Notion 원문 계약](../integration/notion-source-contract.md)을 따른다.

## 로컬 Markdown에는 원문 미러와 개발 산출물이 함께 있을 수 있다

`docs/PRD_Notion` 같은 로컬 작업공간은 Notion 원문 미러와 개발자가 만든 문서를 함께 보관할 수 있다. 시스템은 파일명으로 둘을 구분하지 않는다.

Notion 원문 미러는 `.spec-trace/source-manifest.json`에 등록된 SourcePage mapping으로 식별한다. 그 밖의 코드 대조 분석서, 요구사항 정리, 확정사항 정리 같은 문서는 `DerivedArtifact`로 관리한다.

DerivedArtifact 변경은 기획 원문 버전을 올리지 않는다.

## 검토 명세서는 개발자의 상세 작업 산출물이다

검토 명세서는 개발자가 AI와 함께 사용하는 상세 검토 문서다. 로컬 Markdown을 기본 작업 형태로 사용할 수 있다.

검토 명세서는 다음 정보를 포함한다:

- 검토 기준과 코드 기준점
- Finding과 EvidenceRef
- 개발자 Decision
- OpenQuestion
- Blocker와 BlockedScope
- PlannerAnswer
- 재검증 결과
- 최종 반영 결과

검토 명세서 자체는 기획자에게 노출할 원문이 아니다. 필요한 결과만 기획자용 개발 검토에 투영한다.

## 기획자용 개발 검토는 Notion의 시스템 출력이다

각 PlanningDocument의 ROOT 아래에는 시스템이 관리하는 `개발 검토` 페이지 하나를 둔다. 이 페이지는 원문 수집 대상에서 제외한다.

기획자용 개발 검토는 다음 정보를 보여준다:

- 현재 검토 상태
- 이번 검토에서 개발자가 결정한 사항
- 기획자가 답변할 OpenQuestion
- Blocker와 막힌 업무 범위
- 현재 계속 진행할 수 있는 업무

기획자는 OpenQuestion의 answer slot에서만 답변한다. 출력 구조와 답변 소유권은 [Notion 검토 결과 계약](../integration/notion-review-contract.md)을 따른다.

## 최종설계서는 실제 구현 기준이다

`FinalSpecRevision`은 기획 원문, 개발자 Decision, 기획자 Decision, 기존 정책, 코드베이스 제약을 반영한 실제 구현 기준이다.

새 기획 변경이 구현 기준을 바꾸면 기존 Revision을 덮어쓰지 않고 새 Revision을 만든다. 각 Revision은 반영한 PlanningDocumentSnapshot과 Decision을 역추적할 수 있어야 한다.

## ImplementationRef는 최종설계와 실제 코드를 연결한다

개발이 완료되면 `ImplementationRef`가 FinalSpecRevision과 실제 commit을 연결한다. 이 관계를 사용하면 코드에서 기획 원문과 결정 근거까지 역추적할 수 있다.

DevFlow PLAN/WORK, pull request (PR) 번호, 브랜치명은 보조 참조로 사용할 수 있다. 장기 기준점은 commit SHA다.
