# spec-trace 1차 최소 기능 제품(MVP)을 어떻게 실행하고 검증하나

이 문서는 실제 Notion 기획서 하나를 등록해 spec-trace 1차 MVP를 실행하고 검증하는 절차를 설명한다.
이번 단계에서는 원문 수집, Snapshot 생성, 변경 감지, 검토 이력, Notion `개발 검토` 반영(projection), 복구 동작까지 확인한다.

## 1차 MVP에서 확인하는 기능

이번 단계에서는 다음 동작을 확인한다:

- Notion ROOT 페이지와 하위 페이지를 하나의 불변 Snapshot으로 수집
- 기획서가 수정되면 새 Snapshot과 `ChangeSet` 생성
- 분석 packet을 파일로 내보내고 외부 에이전트 결과를 다시 import
- 개발자 Decision, 기획자 OpenQuestion, Blocker, PlannerAnswer 기록
- 현재 검토 상태를 Notion ROOT 아래 `개발 검토` 페이지에 반영
- projection 실패가 있으면 다음 수집보다 먼저 reconcile
- 모든 blocking Finding이 해결되면 `FinalSpecRevision` 생성

## 개발 환경 설치와 자동 테스트

Python 3.12 이상을 사용한다. 저장소 루트에서 다음 명령을 실행한다:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

`pip install -e .`는 현재 저장소를 코드 수정이 즉시 반영되는 editable 방식으로 설치한다.
이 과정에서 `src/spec_trace.egg-info/`가 생성될 수 있으며 Git에는 포함하지 않는다.

자동 테스트는 fake Notion adapter를 사용하므로 `ntn` 설치나 로그인 없이 실행할 수 있다.

모든 테스트가 `OK`로 끝나면 코드 기준 첫 검증 조건을 통과한 상태다.

## 실제 Notion 연동을 준비한다

실제 Notion 연동은 개인 액세스 토큰(PAT)을 직접 읽지 않는다. spec-trace는 Notion CLI `ntn`의 로그인 세션을 사용한다.

먼저 `ntn`을 설치하고 사용할 워크스페이스에 로그인한다:

```bash
curl -fsSL https://ntn.dev | bash
ntn login
ntn doctor
```

쓰기 smoke test는 ROOT 아래에 `개발 검토` 페이지를 생성한다. 운영 기획서 대신 테스트용 데이터베이스 행 페이지를 사용한다.

live smoke 대상만 환경 변수로 지정한다:

```bash
export SPEC_TRACE_LIVE_DATABASE_ID=your_database_id_here
export SPEC_TRACE_LIVE_PAGE_ID=your_test_page_id_here
```

다른 `ntn` 실행 파일이나 API 버전을 사용해야 할 때만 `NTN_BIN`과 `NOTION_VERSION`을 지정한다. spec-trace는 `NOTION_TOKEN`을 읽거나 저장하지 않는다.

## Notion 메뉴 트리를 갱신한다

`bin/notion-tree.sh`는 작업 데이터 소스의 `메뉴명`과 `상위 항목` 관계를 읽어 전체 메뉴 트리를 갱신한다:

```bash
./bin/notion-tree.sh
```

다른 데이터 소스를 읽으려면 첫 번째 인자나 `SPEC_TRACE_NOTION_DATA_SOURCE_ID`를 사용한다:

```bash
SPEC_TRACE_NOTION_DATA_SOURCE_ID=your_data_source_id_here ./bin/notion-tree.sh
```

이 트리는 메뉴 탐색용 스냅샷이다. `spec-trace collect`는 등록한 ROOT 페이지 본문을 읽고, 페이지 내부의 `child_page`를 깊이 제한 없이 재귀 수집한다.

## Notion live smoke를 실행한다

먼저 `ntn` 로그인 세션, 읽기 권한, Notion API 호환성을 확인한다:

```bash
spec-trace live-smoke
```

성공하면 페이지와 database 정보를 읽을 수 있다는 뜻이다.

다음으로 별도 로컬 workspace에서 수집과 projection 쓰기를 확인한다:

```bash
spec-trace \
  --workspace /tmp/spec-trace-smoke \
  live-smoke \
  --allow-write
```

쓰기 smoke가 성공하면 다음 동작을 확인한 상태다:

- 실제 Notion 원문 수집
- ROOT 아래 `개발 검토` 페이지 생성
- 같은 projection을 다시 실행해도 중복 생성하지 않음
- 시스템이 만든 `개발 검토` 페이지가 새 원문 Snapshot으로 감지되지 않음

## 실제 기획서 하나를 등록한다

프로젝트에서 사용할 workspace를 초기화한다:

```bash
spec-trace init
```

그다음 테스트할 Notion 기획서 ROOT를 등록한다:

```bash
spec-trace document register \
  --database-id your_database_id_here \
  --page-id your_page_id_here
```

출력된 `planning_document_id`를 기록한다. 이후 대부분의 명령에서 이 ID를 사용한다.

## 첫 Snapshot을 만든다

등록한 문서를 처음 수집한다:

```bash
spec-trace collect \
  --document your_planning_document_id_here
```

처음 수집이면 결과가 `SNAPSHOT_CREATED`여야 한다. 최초 Snapshot에는 이전 버전이 없으므로 `ChangeSet`을 만들지 않는다.

현재 상태를 확인한다:

```bash
spec-trace status \
  --document your_planning_document_id_here
```

현재 Snapshot ID, pending `ChangeSet`, 검토 상태, Blocker, FinalSpec 상태를 확인할 수 있다.

같은 원문을 다시 수집한다:

```bash
spec-trace collect \
  --document your_planning_document_id_here
```

기획서가 바뀌지 않았다면 결과가 `UNCHANGED`여야 한다.

등록한 모든 문서를 한 번에 수집하려면 다음 명령을 사용한다:

```bash
spec-trace collect --all
```

## 기획서 변경 추적을 확인한다

등록한 Notion 테스트 문서에서 문장 하나를 수정한다. 수정한 뒤 다시 수집한다:

```bash
spec-trace collect \
  --document your_planning_document_id_here

spec-trace status \
  --document your_planning_document_id_here
```

다음 두 값을 확인한다:

1. `current_snapshot_id`가 이전 값과 달라졌는지 확인
2. 새 `change_set_id`가 생성됐는지 확인

둘 다 확인되면 물리적 버전 추적이 동작하는 상태다.

변경 내용을 분석 packet으로 내보낸다:

```bash
spec-trace analysis export \
  --type source-diff \
  --change-set your_change_set_id_here
```

생성된 JSON에는 이전 Snapshot, 현재 Snapshot, 변경된 페이지와 물리적 변경 정보가 들어간다.
에이전트는 이 근거를 사용해 의미 단위 `ChangeItem` 후보를 만들 수 있다.

## 첫 기획 검토 packet을 만든다

현재 기획서 전체를 검토하려면 `REVIEW` packet을 만든다:

```bash
spec-trace analysis export \
  --type review \
  --document your_planning_document_id_here
```

결과 파일은 `.spec-trace/analysis/requests/` 아래에 생성된다. 이 JSON을 검토 에이전트에 전달한다.

에이전트는 JSON 안의 `expected_output` 형식에 맞춰 candidate를 작성한다. 결과 JSON을 저장한 뒤 import한다:

```bash
spec-trace analysis import \
  path/to/review-response.json
```

생성된 proposal을 확인한다:

```bash
spec-trace proposal show \
  your_analysis_proposal_id_here
```

candidate의 원문 근거를 확인한 뒤 필요한 항목만 채택한다:

```bash
spec-trace proposal review \
  --proposal your_analysis_proposal_id_here \
  --candidate your_candidate_key_here \
  --action adopt
```

proposal을 import한 것만으로 Finding이나 Decision이 확정되지는 않는다. 개발자가 candidate를 채택해야 실제 검토 이력에 반영된다.

## Decision과 질문을 Notion에 반영한다

채택한 Finding에 따라 `decision`, `question`, `blocker` 명령으로 검토 상태를 확정한다. 이후 Notion projection을 실행한다:

```bash
spec-trace sync \
  --document your_planning_document_id_here
```

ROOT 아래 `개발 검토` 페이지에서 다음 내용을 확인한다:

- 개발자가 확정한 Decision
- 기획자가 답해야 하는 OpenQuestion
- 각 질문의 선택지와 tradeoff
- 개발 권장안
- Blocker와 막힌 범위
- BlockedScope 밖에서 진행 가능한 범위

기획자가 answer slot에 답변을 작성하면 `sync`를 다시 실행한다:

```bash
spec-trace sync \
  --document your_planning_document_id_here
```

새 답변은 `PlannerAnswer`로 저장된다. 답변을 검토한 뒤 `answer verify`로 제품 Decision을 확정한다.

## polling과 복구 순서를 확인한다

한 번의 watch cycle을 실행한다:

```bash
spec-trace watch --once
```

watch는 다음 순서로 동작한다:

1. 실패한 `PROJECT_DOCUMENT` operation 복구
2. Notion projection reconcile
3. 등록된 문서 collection

수집 중 원문이 계속 수정돼 `SOURCE_UNSTABLE`이 발생하면 `5s`, `15s`, `30s` 간격으로 최대 3회 다시 수집한다.

지속 polling을 실행하려면 다음 명령을 사용한다:

```bash
spec-trace watch --interval 300
```

기본 운영 주기는 300초다. 이 명령은 현재 프로세스에서 직접 실행한다.

cron 등록은 이번 `ntn` 마이그레이션 범위에 포함하지 않는다. 실제 업무 페이지에서 수집과 projection을 검증한 뒤 별도 자동화 단계에서 추가한다.

## 첫 FinalSpecRevision을 만든다

검토가 끝난 구현 기준을 Markdown 파일로 작성한다. 모든 Finding과 active Blocker가 해결된 뒤 revision을 생성한다:

```bash
spec-trace final-spec create \
  --document your_planning_document_id_here \
  --content path/to/final-spec.md
```

다시 상태를 확인한다:

```bash
spec-trace status \
  --document your_planning_document_id_here
```

`final_spec_revision`에 새 revision이 표시되면 현재 Snapshot과 Decision을 기준으로 최종 구현 기준을 고정한 상태다.

## 1차 MVP 완료 기준

다음 항목을 모두 확인하면 문서화와 버전 추적 중심의 1차 MVP를 실제 업무에 시험할 수 있다:

- 전체 자동 테스트 통과
- Notion live smoke 읽기 성공
- 테스트 페이지에서 live smoke 쓰기 성공
- 최초 `collect`에서 Snapshot 생성
- 같은 원문 재수집에서 `UNCHANGED`
- Notion 수정 후 새 Snapshot과 `ChangeSet` 생성
- REVIEW packet export와 proposal import 성공
- 채택한 Finding이 Decision 또는 OpenQuestion으로 연결
- `sync`가 `개발 검토` 페이지를 중복 없이 유지
- 기획자 답변을 `PlannerAnswer`로 수집
- 검토 완료 후 `FinalSpecRevision` 생성

여기까지 통과한 뒤 실제 회사 기획서 하나를 대상으로 첫 ReviewCycle을 운영한다. DevFlow 연동은 이 MVP 흐름이 안정적으로 동작하는 것을 확인한 다음 단계에서 진행한다.
