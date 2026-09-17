---
meta:
  title: "Notion 원문을 어떤 단위로 수집하고 버전으로 판단하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# Notion 원문을 어떤 단위로 수집하고 버전으로 판단하는가

이 문서는 Notion 데이터베이스의 기획 페이지를 내부 `PlanningDocument`와 Snapshot으로 변환하는 입력 계약을 정의한다.
[문서 계획](../00_INDEX.md#문서-계획)에 따라 페이지 식별, 하위 페이지 수집, 원문 보존, 로컬 미러 분류 규칙을 고정한다.

## Notion 데이터베이스 행을 하나의 기획 문서로 본다

`PlanningDocument`는 모니터링 대상으로 등록한 Notion 데이터베이스의 행 페이지 하나를 뜻한다. 페이지 제목, 로컬 파일명, 디렉터리 위치가 바뀌어도 Notion `page_id`가 같으면 같은 `PlanningDocument`다.

시스템은 다음 값을 외부 식별 기준으로 사용한다:

- `notion_database_id`: 기획 문서를 소유한 데이터베이스 식별자
- `notion_page_id`: 데이터베이스 행 페이지 식별자
- `planning_document_id`: 시스템이 발급한 내부 불변 식별자

로컬 Markdown 경로나 번호 접두사는 식별 기준으로 사용하지 않는다.

## 기획 문서는 여러 실제 Notion 페이지로 구성될 수 있다

데이터베이스 행 페이지 본문과 그 안에서 `/페이지`로 만든 하위 페이지는 하나의 논리 기획을 구성한다. 시스템은 실제 Notion 페이지를 `SourcePage`로 관리한다.

`SourcePage`는 다음 역할을 가진다:

- `ROOT`: 데이터베이스 행 페이지 자체
- `COMPOSED_CHILD`: `ROOT` 또는 다른 `COMPOSED_CHILD` 아래에서 `/페이지`로 생성된 하위 페이지

시스템은 `COMPOSED_CHILD`를 깊이 제한 없이 재귀 수집한다. 페이지 안의 링크, 멘션, Relation 속성처럼 기존 다른 페이지를 참조하는 연결은 `SourceReference`로 관리하고 원문 구성에는 포함하지 않는다.

시스템이 생성한 개발 검토 페이지와 시스템 소유 블록은 원문 수집 대상에서 제외한다.

## 외부 페이지 참조는 원문 포함과 구분한다

`SourceReference`는 현재 기획이 다른 Notion 페이지를 참조한다는 관계만 기록한다. 참조 대상의 전체 내용을 현재 `PlanningDocumentSnapshot`에 복사하지 않는다.

참조 대상이 다른 모니터링 대상 `PlanningDocument`라면 해당 문서 변경 시 역참조 관계를 사용해 Impact Analysis 후보를 만든다. 모니터링 대상이 아니면 참조 대상은 검토 시점에 필요한 경우에만 읽는다.

이 규칙은 공용 정책 페이지 하나가 수정될 때 관련 없는 기획까지 새 원문 버전으로 판정하는 문제를 막는다.

## 실제 페이지마다 불변 Snapshot을 만든다

`SourcePageSnapshot`은 특정 시점의 실제 Notion 페이지 내용을 보존한다. 같은 `SourcePage`에서 정규화된 내용 hash가 달라질 때만 새 Snapshot을 만든다.

정규화 내용에는 다음 정보를 포함한다:

- 페이지 제목
- 본문 블록의 순서와 블록 유형
- 텍스트, 링크, 멘션 대상, 체크 상태, 코드 언어처럼 의미에 영향을 주는 값
- 하위 `/페이지` 관계의 대상 `page_id`
- 데이터베이스별로 명시한 source property allowlist의 속성값

다음 값은 hash에서 제외한다:

- `last_edited_time`
- 작성자와 마지막 편집자
- 시스템 생성 개발 검토 페이지와 시스템 소유 블록
- 색상처럼 요구사항 의미를 바꾸지 않는 표현 정보
- allowlist에 없는 데이터베이스 속성

기본 정책은 데이터베이스 제목 속성만 원문 의미에 포함한다. 추가 속성이 요구사항을 표현할 때만 데이터베이스별 allowlist에 등록한다.

## 기획 전체 Snapshot은 페이지 Snapshot 집합으로 만든다

`PlanningDocumentSnapshot`은 한 시점의 기획 전체 원문이다. `ROOT`와 모든 `COMPOSED_CHILD`의 `SourcePageSnapshot`을 묶어서 만든다.

Snapshot은 다음 관계를 고정한다:

- 포함된 `SourcePageSnapshot`
- 각 페이지의 부모 `SourcePage`
- `ROOT` 또는 `COMPOSED_CHILD` 역할
- 해당 시점의 `SourceReference`

`aggregate_hash`는 `(notion_page_id, parent_notion_page_id, role, page_content_hash)` 튜플을 `notion_page_id` 기준으로 정렬해 계산한다. 형제 페이지의 표시 순서만 바뀐 경우 새 기획 버전으로 판정하지 않는다. 페이지 내용, 부모 관계, 포함 여부가 바뀌면 새 버전으로 판정한다.

## 페이지 추가, 이동, 삭제를 원문 변경으로 처리한다

페이지 구조 변화는 다음 규칙을 따른다:

- 새 `/페이지`가 기획 트리 안에 생기면 새 `COMPOSED_CHILD`로 포함한다
- 기존 하위 페이지가 다른 부모 아래로 이동하면 같은 `SourcePage`의 관계 변경으로 처리한다
- 하위 페이지가 기획 트리 밖으로 이동하거나 archive되면 다음 `PlanningDocumentSnapshot`에서 제외한다
- 제외된 페이지 내용은 과거 Snapshot에서 계속 조회할 수 있어야 한다
- `ROOT`가 archive되거나 모니터링 데이터베이스에서 제거되면 `PlanningDocument`를 source unavailable 상태로 전환하고 과거 이력은 보존한다

하위 페이지 삭제는 Source Diff에서 요구사항 삭제 후보가 된다.

## 로컬 Markdown은 Notion 원문의 미러다

`docs/PRD_Notion` 같은 로컬 디렉터리는 개발 작업공간이다. 같은 디렉터리에 Notion 원문 미러와 개발자가 만든 분석 문서가 함께 존재할 수 있다.

시스템은 파일명으로 원문 여부를 추측하지 않는다. Notion에서 생성한 로컬 미러는 mirror root 아래의 `.spec-trace/source-manifest.json`에 다음 매핑을 기록한다:

```json
{
  "planning_document_id": "planning_document_id_here",
  "sources": [
    {
      "notion_page_id": "12345678901234567890123456789012",
      "role": "ROOT",
      "local_path": "근무유형별근무기준등록.md"
    }
  ]
}
```

manifest에 Source로 등록되지 않은 로컬 Markdown은 자동으로 기획 원문으로 취급하지 않는다. 개발자가 작성한 분석서, 코드 대조 결과, 확정사항 정리 문서는 `DerivedArtifact`로 분류한다.

로컬 경로가 바뀌어도 Notion `page_id`와 내부 ID가 같으면 원문 이력은 유지한다.

## 원문 수집 완료 조건

하나의 `PlanningDocumentSnapshot`은 다음 조건을 모두 만족해야 확정한다:

1. `ROOT`와 모든 `COMPOSED_CHILD` 목록을 수집한다
2. 각 페이지의 내용을 읽고 `SourcePageSnapshot` 후보를 만든다
3. 수집 시작 시점과 종료 시점의 `last_edited_time`을 비교한다
4. 수집 중 하나라도 변경된 페이지가 있으면 이번 후보를 폐기한다
5. 모든 페이지가 안정적이면 `PlanningDocumentSnapshot`과 `aggregate_hash`를 확정한다

이 규칙은 여러 Notion 페이지를 읽는 도중 기획자가 수정했을 때 서로 다른 시점의 내용을 하나의 버전으로 섞지 않게 한다.
