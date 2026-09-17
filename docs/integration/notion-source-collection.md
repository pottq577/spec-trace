---
meta:
  title: "Notion 원문을 어떻게 일관된 Snapshot으로 확정하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# Notion 원문을 어떻게 일관된 Snapshot으로 확정하는가

이 문서는 하나의 `PlanningDocument`를 읽어 안정적인 `PlanningDocumentSnapshot`으로 확정하는 실행 계약을 정의한다.
[문서 계획](../00_INDEX.md#문서-계획), [Notion 원문 계약](./notion-source-contract.md), [Notion 동기화 규칙](./notion-sync-rules.md)을 실제 수집 순서로 연결한다.

## 수집기는 한 번에 하나의 기획 문서를 처리한다

원문 수집기는 `PlanningDocument` 하나를 하나의 실행 단위로 처리한다. 대상 선택과 전체 스케줄링은 상위 동기화 오케스트레이터가 담당한다.

수집 실행은 다음 입력을 사용한다:

- `planning_document_id`
- `notion_database_id`
- `root_notion_page_id`
- 마지막으로 확정한 `PlanningDocumentSnapshot`, 없는 경우 빈 값
- 데이터베이스별 source property allowlist
- 내부에 저장한 시스템 소유 page와 block 식별자

수집기는 `ChangeSet`, Source Diff, Impact Analysis, `ReviewCycle`을 만들지 않는다. 이번 실행의 책임은 원문 상태를 일관되게 읽고 Snapshot 생성 여부를 확정하는 데서 끝난다.

## 기본 수집 주기는 5분 폴링으로 고정한다

최소 기능 제품(Minimum Viable Product, MVP)의 기본 감지 방식은 5분 간격 폴링이다. 이 값은 운영 설정이며 도메인 불변 규칙은 아니다.

상위 오케스트레이터는 다음 조건에서 수집을 요청한다:

- `INITIAL`: 새 `PlanningDocument`를 등록한 직후
- `POLL`: 모니터링 등록 상태인 문서를 source 상태와 관계없이 5분마다 확인할 때
- `REFRESH`: 개발자가 명시적으로 다시 확인할 때
- `RECONCILE`: 프로세스 재시작이나 동기화 복구 과정에서 현재 원문을 다시 맞출 때
- `RETRY`: 이전 실행이 안정성을 증명하지 못했거나 재시도 가능한 오류로 끝났을 때

같은 `planning_document_id`에는 동시에 하나의 수집 실행만 허용한다. 실행 중 새 트리거가 들어오면 중복 실행 대신 현재 실행 종료 후 한 번의 후속 실행으로 합친다.

## 수집은 두 번의 트리 확인 사이에서 원문을 읽는다

수집기는 시작 트리와 종료 트리를 비교해 수집 중 변경이 없었음을 확인한다. 전체 실행 순서는 다음과 같다:

1. ROOT 상태를 검증한다
2. 시작 시점의 source page 트리를 재귀 탐색한다
3. 발견한 모든 source page의 원문을 수집한다
4. 페이지별 canonical content와 `content_hash`를 계산한다
5. 종료 시점의 source page 트리를 다시 재귀 탐색한다
6. 시작 트리와 종료 트리의 안정성을 검증한다
7. 안정적인 후보로 `aggregate_hash`를 계산한다
8. 마지막 Snapshot과 비교해 새 Snapshot 생성 여부를 확정한다

각 트리 탐색은 모든 pagination을 끝까지 소비해야 한다. `/페이지`로 만든 `COMPOSED_CHILD`는 깊이 제한 없이 탐색하고 `SourceReference` 대상은 따라가지 않는다.

## ROOT 검증이 기획 문서의 접근 가능 상태를 결정한다

수집 시작 시 ROOT는 등록 당시의 `notion_database_id`와 `root_notion_page_id`를 기준으로 검증한다. ROOT가 정상이라면 원문 트리 수집을 계속한다.

다음 상태는 `SOURCE_UNAVAILABLE`로 처리한다:

- ROOT가 archive된 상태
- ROOT가 등록된 모니터링 데이터베이스 밖으로 이동한 상태
- Notion이 ROOT의 부재 또는 접근 권한 상실을 확정적으로 반환한 상태

네트워크 오류, rate limit, 서버 오류처럼 현재 ROOT 상태를 확정할 수 없는 API 실패는 `COLLECTION_FAILED`로 처리한다. 마지막 정상 Snapshot은 모든 경우에 유지한다.
이후 ROOT 검증에 성공하면 source 상태를 다시 사용 가능 상태로 전환한다.

## 시스템 출력은 저장된 식별자로 제외한다

수집기는 제목이나 로컬 파일명으로 시스템 출력을 판별하지 않는다. 내부 output mapping에 저장된 `review_page_id`와 시스템 소유 block 식별자를 기준으로 제외한다.

시스템 소유 page를 제외하면 그 하위 page와 상위 page 안의 해당 child-page 연결 block도 원문에서 제외한다. 기획자가 작성한 일반 하위 page는 제목이 `개발 검토`와 같아도 저장된 시스템 식별자와 다르면 원문 후보로 처리한다.

## 시작 트리는 페이지 집합과 관계를 고정한다

첫 번째 탐색은 수집 후보가 되는 페이지의 구조 상태를 기록한다. 각 page에 다음 값을 보관한다:

- `notion_page_id`
- `parent_notion_page_id`, ROOT는 빈 값
- `role`: `ROOT` 또는 `COMPOSED_CHILD`
- `last_edited_time`

같은 `notion_page_id`가 한 트리에서 서로 다른 부모로 두 번 나타나면 원문 트리를 확정하지 않는다. 이 경우 `COLLECTION_FAILED`와 `INVALID_SOURCE_TREE` 오류를 반환한다.

## 페이지 수집은 완전한 block 트리를 읽는다

시작 트리에 포함된 각 page는 페이지 메타데이터, allowlist 속성, 전체 block 트리를 수집한다. 중첩 block도 pagination을 끝까지 읽어야 한다.

수집 중 다음 정보도 함께 추출한다:

- 다른 Notion page를 가리키는 링크와 멘션
- allowlist 속성에 포함된 Relation 대상
- 현재 page에서 생성되는 `SourceReference` 후보

시작 트리에 있던 page가 수집 중 사라지거나 이동한 정황이 확인되면 후보를 폐기한다. 현재 상태를 판별할 수 없는 API 실패는 `COLLECTION_FAILED`로 종료한다.

## canonical content는 의미 정보만 안정적으로 직렬화한다

페이지 원문은 [Notion 원문 계약](./notion-source-contract.md)의 포함·제외 규칙을 적용한 canonical object로 변환한다. 동일한 의미 입력은 실행 시점과 API 응답 순서에 관계없이 같은 byte sequence를 만들어야 한다.

canonicalization은 다음 규칙을 사용한다:

- 문자열은 Unicode 정규화 형식 C(Normalization Form C, NFC)로 정규화한다
- block 배열은 Notion의 의미 있는 표시 순서를 유지한다
- 순서가 의미 없는 property map과 Relation 집합은 안정적인 key 또는 target page ID 순서로 정렬한다
- Notion page ID는 하이픈을 제거한 소문자 32자리 형태로 정규화한다
- block ID, `last_edited_time`, 작성자, 마지막 편집자, 요구사항 의미를 바꾸지 않는 표현용 annotation은 제외한다
- 링크 URL, 멘션 대상, 체크 상태, 코드 언어처럼 의미를 바꾸는 값은 유지한다

canonical object는 JSON Canonicalization Scheme(JCS, RFC 8785)으로 직렬화한다. UTF-8 byte sequence에 SHA-256를 적용하고 소문자 16진수 문자열을 `SourcePageSnapshot.content_hash`로 사용한다.
[Notion 원문 계약](./notion-source-contract.md)의 `page_content_hash`는 이 값을 뜻한다.

## 종료 트리가 시작 트리와 같아야 후보를 확정한다

두 번째 탐색은 첫 번째 탐색과 같은 규칙으로 page 구조와 `last_edited_time`을 다시 수집한다. 다음 조건을 모두 만족해야 원문이 안정적이다:

1. ROOT가 계속 접근 가능하다
2. 포함된 `notion_page_id` 집합이 같다
3. 각 page의 부모와 `role`이 같다
4. 각 page의 `last_edited_time`이 같다

ROOT가 archive, 이동, 확정적 부재 또는 권한 상실 상태가 되면 `SOURCE_UNAVAILABLE`을 우선 반환한다.
그 외 조건이 하나라도 다르면 이번 수집에서 만든 모든 Snapshot 후보를 폐기하고 `SOURCE_UNSTABLE`을 반환한다. 후속 실행이 안정적인 새 상태를 다시 수집한다.

## aggregate hash는 페이지 내용과 포함 관계를 함께 비교한다

안정성 검증을 통과한 뒤 각 page를 다음 tuple로 변환한다:

```text
(notion_page_id, parent_notion_page_id, role, content_hash)
```

tuple은 `notion_page_id` 기준으로 정렬한다. 정렬된 목록을 페이지 canonicalization과 같은 방식으로 직렬화하고 SHA-256을 적용해 `aggregate_hash`를 계산한다.

같은 page 내용이 유지된 채 부모만 바뀌면 기존 `SourcePageSnapshot`을 재사용하고 새 `PlanningDocumentSnapshot`을 만든다. 형제 page의 표시 순서만 바뀌면 `aggregate_hash`는 유지된다. 안정적인 수집에서 추출한 `SourceReference` 집합은 해당 `PlanningDocumentSnapshot`에 함께 고정한다.

## Snapshot 공개는 기획 문서 단위로 원자적이어야 한다

확정 단계는 마지막 `PlanningDocumentSnapshot`의 `aggregate_hash`와 이번 후보를 비교한다. 결과는 다음 규칙을 따른다:

- 이전 Snapshot이 없으면 새 `PlanningDocumentSnapshot`을 만든다
- `aggregate_hash`가 다르면 새 `PlanningDocumentSnapshot`을 만든다
- `aggregate_hash`가 같으면 새 Snapshot을 만들지 않는다
- 같은 `source_page_id + content_hash`의 `SourcePageSnapshot`이 이미 있으면 재사용한다

저장 구현은 새 `PlanningDocumentSnapshot`과 그 참조가 모두 준비되기 전까지 현재 Snapshot으로 공개하면 안 된다. 저장 중 실패하면 마지막 정상 Snapshot을 유지하고 다음 실행에서 멱등성 key로 재사용하거나 다시 생성한다.

## 수집 결과는 다섯 상태 중 하나다

상위 오케스트레이터는 수집 결과 상태로 후속 동작을 결정한다:

| 상태                 | 조건                                                                  | 확정 상태 변경                                            |
| -------------------- | --------------------------------------------------------------------- | --------------------------------------------------------- |
| `UNCHANGED`          | 안정적인 원문의 `aggregate_hash`가 마지막 Snapshot과 같음             | 없음                                                      |
| `SNAPSHOT_CREATED`   | 최초 수집이거나 안정적인 `aggregate_hash`가 달라짐                    | 새 `PlanningDocumentSnapshot` 확정                        |
| `SOURCE_UNSTABLE`    | 시작·종료 트리 또는 `last_edited_time`이 다름                         | 없음                                                      |
| `SOURCE_UNAVAILABLE` | ROOT archive, 이동, 확정적 부재 또는 권한 상실                        | `PlanningDocument.source_status` 갱신, 과거 Snapshot 유지 |
| `COLLECTION_FAILED`  | API, pagination, canonicalization, 저장 등으로 일관성을 증명하지 못함 | 없음                                                      |

결과에는 최소한 `sync_run_id`, `planning_document_id`, 상태, 기준 Snapshot ID, 생성된 Snapshot ID, `started_at`, `completed_at`을 포함한다. 실패 결과는 `failure_code`와 `retryable`도 포함한다.

## 수집기는 실패 상태에서 기존 이력을 수정하지 않는다

`SOURCE_UNSTABLE`과 `COLLECTION_FAILED`는 새 Snapshot을 만들지 않는다. 재시도 횟수, backoff, rate limit 대응은 별도 오류·재시도 계약에서 정의한다.

`SOURCE_UNAVAILABLE`도 과거 Snapshot을 삭제하거나 수정하지 않는다. ROOT가 다시 정상 상태로 돌아오면 일반 수집 절차로 새 Snapshot 필요 여부를 다시 판정한다.

## 실행 계약은 다음 시나리오를 만족해야 한다

구현은 최소한 다음 사례를 검증해야 한다:

- 최초 안정 수집은 `SNAPSHOT_CREATED`를 반환한다
- `last_edited_time`만 달라지고 canonical content가 같으면 안정적인 다음 실행에서 `UNCHANGED`를 반환한다
- 수집 도중 하위 page가 추가되거나 제거되면 `SOURCE_UNSTABLE`을 반환한다
- 하위 page가 같은 문서 안에서 부모를 바꾸면 안정적인 다음 실행에서 새 `PlanningDocumentSnapshot`을 만든다
- 시스템 `개발 검토` page만 바뀌면 원문 Snapshot은 바뀌지 않는다
- ROOT가 archive되면 `SOURCE_UNAVAILABLE`을 반환하고 과거 Snapshot을 유지한다
- 같은 문서에 여러 트리거가 겹쳐도 활성 수집은 하나만 실행한다

## 다음 patch는 변경 감지와 ChangeSet 생성을 확정한다

이 문서에서 원문 수집과 Snapshot 확정 경계를 고정했으므로 다음 patch는 두 `PlanningDocumentSnapshot` 사이의 변경 감지와 `ChangeSet` 생성 조건을 정의한다. Source Diff의 의미 분류와 Impact Analysis는 그 다음 실행 계약에서 다룬다.
