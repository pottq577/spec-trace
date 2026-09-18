---
meta:
  title: "Notion 메뉴를 수집 대상으로 어떻게 자동 등록하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# Notion 메뉴를 수집 대상으로 어떻게 자동 등록하는가

이 문서는 메뉴 데이터 소스의 행을 `PlanningDocument` 등록 상태와 동기화하는 계약을 정의한다. [문서 계획](../00_INDEX.md#문서-계획)과 [Notion 원문 계약](./notion-source-contract.md)을 자동 발견 단계에 연결한다.

## source sync는 메뉴 데이터 소스를 등록 기준으로 사용한다

`source sync`는 database ID와 data source ID를 입력받는다. 먼저 database의 `data_sources` 목록에서 대상 data source가 실제로 속해 있는지 확인한다.

검증이 끝나면 data source query의 모든 페이지를 끝까지 읽는다. 페이지가 아닌 결과, archive된 페이지, 휴지통의 페이지는 등록 대상에서 제외한다.

## 메뉴 행 하나는 PlanningDocument 하나에 대응한다

활성 메뉴 행은 다음 값으로 `PlanningDocument`를 생성하거나 갱신한다:

- `notion_database_id`: 메뉴 database ID
- `notion_data_source_id`: query에 사용한 data source ID
- `root_notion_page_id`: 메뉴 행 page ID
- `title`: 행의 title 속성 값
- `menu_parent_notion_page_id`: `상위 항목` relation의 page ID
- `source_status`: `AVAILABLE`

제목 속성 이름은 고정하지 않는다. page properties에서 `type=title`인 속성을 찾아 메뉴명을 읽는다.

## 상위 항목은 단일 부모 관계로 저장한다

기본 부모 속성 이름은 `상위 항목`이다. 다른 데이터 소스에서는 `--parent-property`으로 속성 이름을 바꿀 수 있다.

부모 relation은 0개 또는 1개만 허용한다. relation이 비어 있으면 최상위 메뉴로 보고 `menu_parent_notion_page_id`를 비운다.

## 반복 실행은 현재 메뉴 구조로 수렴한다

같은 data source를 다시 동기화하면 page ID를 기준으로 기존 `PlanningDocument`를 재사용한다. 제목, data source ID, 부모 page ID, source 상태가 달라졌을 때만 갱신 건수에 포함한다.

이전에 같은 data source에서 발견했지만 현재 query 결과에 없는 문서는 `UNAVAILABLE`로 전환한다. 수동 `document register`로 만든 문서는 data source ID가 없으므로 다른 source sync가 임의로 비활성화하지 않는다.

## source sync와 원문 collection은 책임을 분리한다

`source sync`는 메뉴 발견과 등록 상태만 변경한다. Snapshot, `ChangeSet`, source page는 만들지 않는다.

원문 수집은 기존 collector가 담당한다. `collect --all`은 등록된 ROOT 본문을 읽고 실제 `child_page`를 깊이 제한 없이 재귀 수집한다.

```bash
./bin/notion-tree.sh

spec-trace source sync \
  --database-id notion_database_id_here \
  --data-source-id notion_data_source_id_here

spec-trace collect --all
```

`workspace-tree.md`와 `PlanningDocument`는 같은 Notion page ID를 기준으로 같은 메뉴 집합을 가리킨다. 트리 파일은 사람이 구조를 확인하는 스냅샷이고 SQLite 등록 상태는 collector의 실행 기준이다.

## 실패는 기존 등록 상태를 유지한다

database와 data source의 소속이 맞지 않거나 `상위 항목`이 relation이 아니면 sync를 실패시킨다. 같은 행이 두 번 반환되거나 부모 relation이 두 개 이상인 경우에도 입력 구조 오류로 처리한다.

원격 query와 검증을 모두 끝낸 뒤 하나의 SQLite transaction에서 등록 상태를 갱신한다. 검증 중 실패하면 기존 `PlanningDocument` 상태를 수정하지 않는다.
