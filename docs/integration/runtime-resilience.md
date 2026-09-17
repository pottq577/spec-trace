---
meta:
  title: "연동 실패와 rate limit을 어떻게 복구하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 연동 실패와 rate limit을 어떻게 복구하는가

이 문서는 Notion 호출, 원문 수집, 분석 import, 저장, Notion projection에서 발생하는 오류를 분류하고 재시도 책임을 고정한다. 모든 재실행은 마지막 확정 이력을 유지한 상태에서 시작한다.

## 재시도는 가장 낮은 공통 계층에서 한 번만 담당한다

같은 실패에 HTTP client, collector, scheduler가 각각 retry loop를 만들면 요청 폭주와 중복 쓰기가 생길 수 있다. 각 실패는 하나의 계층만 재시도를 소유한다.

책임은 다음과 같이 나눈다:

- HTTP 전송 실패와 Notion 일시 오류: Notion HTTP client
- 수집 중 원문 변경: source collection job
- 저장 충돌과 트랜잭션 실패: storage adapter
- 구조화 분석 결과 검증 실패: 재시도하지 않고 새 proposal 요청
- Notion projection 부분 실패: projection reconcile job

## Notion 요청은 중앙 queue를 통과한다

MVP는 하나의 Notion connection에 대해 모든 요청을 중앙 queue로 직렬화한다. 기본 송신 속도는 초당 2건으로 제한해 Notion의 connection 평균 제한보다 여유를 둔다.

서로 다른 collector나 projection 작업이 독립적인 rate limiter를 가지면 안 된다. `Retry-After`를 받으면 중앙 queue 전체가 해당 connection의 다음 요청을 지연한다.

## HTTP 재시도는 상태 코드와 멱등성을 기준으로 한다

Notion HTTP client는 한 요청에 최대 6회 시도한다. 다음 응답을 자동 재시도한다:

- `429`: rate limited
- `529`: service overload
- `500`, `502`, `503`, `504`: `GET`과 `DELETE`처럼 멱등성이 보장된 요청

`Retry-After`가 있으면 해당 초를 우선 사용한다. 없으면 `1s`, `2s`, `4s`, `8s`, `16s`, `30s` 상한의 exponential backoff를 사용하고 최대 `250 ms` jitter를 더한다.

Notion page 또는 block 생성·수정 요청은 내부 idempotency mapping이나 reconcile 근거가 확보된 경우에만 같은 payload를 자동 재시도한다. 중복 생성 가능성이 있으면 write job으로 제어를 넘긴다.

## 인증과 요청 오류는 자동 반복하지 않는다

다음 오류는 기본적으로 재시도하지 않는다:

- `401`: integration token 또는 인증 설정 오류
- `403`: connection capability 또는 접근 권한 오류
- `400`: validation, payload size, 지원하지 않는 요청
- 지속적인 `404`: 대상 부재 또는 접근 범위 변경 가능성

`404`는 Notion에서 대상 부재와 접근 권한 상실을 동일하게 표현할 수 있으므로 원인을 추측하지 않는다. collector는 ROOT 직접 조회와 connection 상태를 함께 확인해 `SOURCE_UNAVAILABLE` 또는 연동 설정 오류로 분류한다.

## 오류는 실행 책임 기준으로 분류한다

시스템 오류 분류는 다음 값을 사용한다:

- `TRANSIENT_REMOTE`: 일시적인 Notion 또는 네트워크 오류
- `RATE_LIMITED`: `429` 또는 `529`로 요청 속도 조절이 필요함
- `AUTHENTICATION_FAILED`: token이 유효하지 않음
- `AUTHORIZATION_FAILED`: connection capability 또는 대상 접근 권한이 부족함
- `INVALID_REMOTE_REQUEST`: API version, payload, size, validation 오류
- `SOURCE_UNSTABLE`: 수집 중 원문이 변경됨
- `SOURCE_UNAVAILABLE`: ROOT archive, 이동, 확정적 부재
- `INVALID_ANALYSIS_PAYLOAD`: analysis contract 검증 실패
- `STALE_ANALYSIS`: Snapshot 또는 code baseline freshness 실패
- `PERSISTENCE_FAILED`: 저장 트랜잭션을 완료하지 못함
- `PROJECTION_FAILED`: Notion 개발 검토 projection을 완료하지 못함
- `INTERNAL_INVARIANT_VIOLATION`: hash, 계보, 상태 전이 불변 규칙 위반

각 실패는 `retryable`, `failure_code`, `operation`, `subject_ref`, `occurred_at`을 기록한다.

## SOURCE_UNSTABLE은 짧은 job retry를 허용한다

원문 수집 중 시작·종료 트리가 달라지면 외부 장애가 아니라 동시 편집 상황이다. collector는 현재 후보를 폐기하고 같은 문서에 최대 3회의 짧은 재수집을 허용한다.

지연은 `5s`, `15s`, `30s`를 사용한다. 세 번 모두 불안정하면 현재 run을 종료하고 다음 5분 polling 또는 명시적 refresh에서 다시 시도한다.

## HTTP retry가 끝난 원격 실패는 scheduler로 되돌린다

HTTP client가 최대 시도를 소진하면 collector가 같은 호출을 다시 반복하지 않는다. 현재 collection run을 `COLLECTION_FAILED`로 종료하고 `retryable=true`인 경우 다음 polling 대상에 남긴다.

인증·권한·요청 형식 오류는 설정을 바꾸기 전까지 반복해도 성공할 가능성이 없으므로 운영 상태에 `attention_required=true`를 표시한다.

## 분석 import 오류는 원본 결과를 보존한다

외부 ChatGPT나 Codex 결과가 schema, locator, coverage 검증에 실패하면 payload를 폐기하지 않는다. `INVALID_ANALYSIS_PAYLOAD` 상태로 보존하고 유효한 proposal로 채택하지 않는다.

개발자는 오류 위치를 확인해 분석을 다시 실행하거나 payload를 수정한 새 import를 등록한다. 실패 payload를 내부에서 임의 보정해 채택하지 않는다.

## 저장은 도메인 확정 단위로 원자성을 가진다

Snapshot 확정, `ChangeItem` 채택, `ImpactLink` 채택, Decision 생성은 각각 하나의 저장 트랜잭션으로 처리한다. 트랜잭션이 실패하면 해당 확정 단위를 외부에 공개하지 않는다.

외부 Notion write와 내부 저장을 하나의 분산 트랜잭션으로 묶지 않는다. 내부 상태를 먼저 확정하고 output mapping을 이용해 projection을 재실행한다.

## Notion projection은 reconcile로 복구한다

개발 검토 page나 question section 일부를 쓴 뒤 실패할 수 있다. 성공한 `review_page_id`, block mapping은 유지하고 다음 reconcile에서 누락된 출력만 복구한다.

새 page 생성 요청의 응답을 받지 못해 성공 여부가 불명확하면 즉시 같은 create 요청을 반복하지 않는다. parent 아래의 시스템 marker와 기존 mapping을 조회해 생성 여부를 확인한 뒤 필요한 경우에만 다시 만든다.

## 내부 불변 규칙 위반은 자동 재시도하지 않는다

`aggregate_hash`와 page change가 모순되거나 존재하지 않는 Decision을 상태 전이하려는 오류는 `INTERNAL_INVARIANT_VIOLATION`으로 처리한다. 같은 입력을 반복해도 해결되지 않으므로 실행을 중단하고 원인을 노출한다.

이 오류는 실패한 실행만 중단한다. 마지막 정상 Snapshot, Decision, FinalSpecRevision은 유지한다.

## 실행 상태는 재시작 후에도 복구 가능해야 한다

프로세스가 중단돼도 다음 실행은 확정된 도메인 상태와 진행 중 run 기록에서 재개한다. 메모리 queue만을 유일한 작업 근거로 사용하지 않는다.

재시작 시 우선순위는 다음과 같다:

1. 완료되지 않은 내부 확정 transition
2. 부분 실패한 Notion projection reconcile
3. retryable collection run
4. 정기 polling

같은 subject에 여러 재시도 요청이 쌓이면 하나의 후속 실행으로 합친다.

## 실행 계약은 다음 시나리오를 만족해야 한다

구현은 최소한 다음 사례를 검증해야 한다:

- 모든 Notion 요청이 하나의 connection queue와 rate limiter를 공유한다
- `429`와 `529`에서 `Retry-After`를 우선 사용한다
- 멱등성이 보장되지 않은 create 요청을 응답 불명 상태에서 바로 반복하지 않는다
- `SOURCE_UNSTABLE`은 세 번의 짧은 retry 뒤 다음 polling으로 넘긴다
- invalid analysis payload를 보존하되 도메인 이력으로 채택하지 않는다
- 내부 저장 성공 후 Notion projection이 실패해도 내부 결정을 되돌리지 않는다
- process restart 뒤 미완료 transition과 projection부터 복구한다

## 다음 단계는 MVP 애플리케이션 구조를 결정하는 일이다

변경 분석 실행 계약이 모두 닫혔다. 다음 patch부터 이 계약을 구현할 애플리케이션 경계, 실행 방식, 기술 선택을 확정한다.
