---
meta:
  title: "첫 MVP를 어떤 애플리케이션 구조로 구현하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 첫 MVP를 어떤 애플리케이션 구조로 구현하는가

이 문서는 지금까지 확정한 원문 수집, 변경 분석, 검토, Notion projection 계약을 첫 실행 가능한 MVP로 구현하는 애플리케이션 경계와 기술 선택을 정의한다. MVP는 개발자 한 명이 로컬 저장소와 Notion을 연결해 쓰는 내부 도구를 목표로 한다.

## MVP는 local-first modular monolith로 구현한다

첫 버전은 하나의 Python 프로세스와 하나의 SQLite 데이터베이스로 구성한다. 별도 웹 서버, 프론트엔드, message broker, 분산 worker는 두지 않는다.

이 구조는 현재 사용 방식과 맞는다:

- 기획자는 기존 Notion만 사용한다
- 개발자는 로컬 Git 저장소와 CLI를 사용한다
- ChatGPT와 Codex는 기존 방식으로 분석에 사용한다
- 시스템은 수집, 이력, 검증, projection, DevFlow handoff를 담당한다

## 기술 기준은 Python 3.12 이상과 표준 라이브러리다

MVP runtime은 Python 3.12 이상을 사용한다. 핵심 실행 경로는 표준 라이브러리만으로 구현해 설치와 복구 경로를 줄인다.

주요 사용 모듈은 다음과 같다:

- `sqlite3`: 영속 저장
- `json`: contract packet과 projection payload
- `hashlib`: content hash와 payload hash
- `argparse`: CLI
- `subprocess`: `ntn api`와 Git immutable commit 조회
- `pathlib`: workspace와 artifact 경로
- `threading`: process 내부 `ntn` 호출 limiter
- `time`과 `random`: rate limit과 retry

추가 라이브러리가 필요해지면 core contract를 바꾸지 않는 adapter 단위로 도입한다.

## 실행 단위는 CLI command와 watch loop로 나눈다

하나의 executable `spec-trace`가 명령형 작업과 장기 polling을 모두 제공한다.

MVP command surface는 다음 책임을 가진다:

- `init`: workspace와 schema 초기화
- `register`: Notion ROOT를 `PlanningDocument`로 등록
- `collect`: 특정 문서를 즉시 수집
- `watch`: 등록 문서를 5분 주기로 수집하고 미완료 작업을 재개
- `analysis export`: Source Diff 또는 Impact Analysis 입력 packet 생성
- `analysis import`: 외부 agent의 proposal import와 validation
- `review`: proposal candidate 채택, 수정 채택, 거절
- `decision`: 개발자 Decision 확정
- `finalize`: 현재 검토 결과를 `FinalSpecRevision`으로 확정
- `sync`: Notion 개발 검토 projection reconcile
- `devflow export`: 현재 FinalSpecRevision handoff 생성
- `status`: 문서별 Snapshot, ChangeSet, ReviewCycle, Blocker 상태 조회

명령 이름의 세부 option은 개발자 인터페이스 계약에서 확정한다.

## 프로세스 내부는 application service와 adapter를 분리한다

모듈 경계는 다음 구조를 사용한다:

```text
spec_trace/
├── cli.py
├── domain/
├── application/
├── adapters/
│   ├── sqlite/
│   ├── notion/
│   ├── git/
│   └── filesystem/
└── contracts/
```

`domain`은 상태와 불변 규칙을 정의한다. `application`은 use case 순서를 실행하고 transaction 경계를 정한다. `adapters`는 SQLite, Notion, Git, 파일 시스템 접근을 담당한다. `contracts`는 외부 agent와 DevFlow에 전달할 JSON schema와 serializer를 담당한다.

## application service가 workflow를 소유한다

MVP는 다음 application service를 둔다:

- `PlanningDocumentService`: 등록, source status 관리
- `CollectionService`: source tree 수집과 Snapshot 확정
- `ChangeDetectionService`: `ChangeSet`과 physical change 생성
- `AnalysisService`: packet export, proposal import, validation
- `ReviewService`: `ReviewAction`, `ChangeItem`, `ImpactLink`, Finding, Decision 처리
- `FinalSpecService`: FinalSpecRevision 생성
- `ProjectionService`: Notion 개발 검토 출력과 answer 수집
- `DevFlowExportService`: 최종설계 handoff 생성
- `RecoveryService`: 미완료 transition과 reconcile 재개

CLI는 이 service를 호출할 뿐 도메인 상태를 직접 수정하지 않는다.

## Notion adapter는 `ntn api`를 사용한다

Notion adapter는 direct HTTP client를 두지 않는다. 모든 page 조회, database 조회, block 조회, projection write를 `ntn api` subprocess로 실행한다.

기본 Notion API 버전은 `2026-03-11`이며 adapter가 매 호출에 `--notion-version`으로 전달한다. `NOTION_VERSION`으로 교체할 수 있고, API version 변경은 adapter compatibility test를 통과한 뒤 기본값을 갱신한다.

인증은 미리 완료한 `ntn login` 세션을 사용한다. spec-trace는 `NOTION_TOKEN`을 읽거나 저장하지 않는다. 다른 실행 파일이 필요할 때만 `NTN_BIN`을 지정한다.

하나의 command나 watch process는 같은 `NotionCliClient`를 재사용한다. adapter의 shared limiter가 `ntn` 실행 속도를 제한하고, write 실패는 projection reconcile로 복구한다.

## Git adapter는 로컬 저장소를 read-only로 사용한다

MVP는 코드 근거 수집을 위해 Git hosting API를 요구하지 않는다. 등록한 repository path에서 `git rev-parse`, `git show`, `git grep` 같은 read-only 명령을 사용한다.

EvidenceRef는 branch 이름보다 commit SHA를 저장한다. 분석 packet을 만들 때 현재 기준 commit을 한 번 고정하고 해당 commit의 파일 내용만 읽는다.

## 외부 agent 연동은 export와 import로 시작한다

첫 MVP는 모델 provider API를 내장하지 않는다. 개발자는 기존 ChatGPT, Codex, 다른 agent에 analysis packet을 전달하고 구조화 결과를 import한다.

파일 교환 경로는 workspace 아래에 둔다:

```text
.spec-trace/
├── spec-trace.db
├── analysis/
│   ├── requests/
│   └── responses/
├── mirror/
└── exports/
```

이 구조는 AI 사용량과 provider를 시스템 운영에서 분리한다. 이후 자동 호출 adapter를 추가해도 `AnalysisProposal` contract는 유지한다.

## SQLite는 단일 writer를 전제로 사용한다

첫 MVP는 개발자 한 명과 하나의 watch process를 지원한다. SQLite는 WAL mode를 사용하고 application service transaction을 짧게 유지한다.

workspace lock을 사용해 동시에 두 개의 `watch` process가 같은 database를 쓰지 못하게 한다. 단발 CLI command는 lock과 database transaction으로 watch process와 충돌을 조정한다.

다중 사용자, 원격 API, 여러 worker가 필요해지면 storage adapter를 PostgreSQL로 교체한다. 도메인 ID와 contract schema는 이 교체에 영향을 받지 않아야 한다.

## source mirror는 검토 편의를 위한 파생 출력이다

Notion 원문 Snapshot의 정식 이력은 SQLite와 content artifact가 보존한다. `.spec-trace/mirror`의 Markdown은 개발자가 읽기 위한 projection이며 source identity를 결정하지 않는다.

mirror 생성 실패는 확정 Snapshot을 되돌리지 않는다. 다음 collection 또는 reconcile에서 다시 만들 수 있다.

## Notion projection은 내부 상태 이후에 실행한다

Decision, OpenQuestion, Blocker 같은 내부 상태를 먼저 transaction으로 확정한다. `ProjectionService`는 그 상태를 읽어 ROOT 아래 `개발 검토` page를 맞춘다.

Notion write가 실패해도 내부 상태는 유지한다. 다음 `sync` 또는 `watch` 실행이 output mapping을 기준으로 reconcile한다.

## MVP는 한 workspace의 한 개발자 흐름에 집중한다

첫 버전에서 지원하는 범위는 다음과 같다:

- 하나의 local workspace
- 하나의 Notion connection
- 여러 `PlanningDocument`
- 여러 local Git repository 등록
- 개발자 한 명의 검토와 채택
- polling 기반 source 감지
- file 기반 외부 agent contract
- Notion 개발 검토 page와 answer 수집
- DevFlow용 파일 handoff

첫 버전에서 제외하는 범위는 다음과 같다:

- 사용자 계정과 권한 관리
- 웹 UI와 public HTTP API
- Notion webhook
- built-in model provider 호출
- 분산 queue와 worker
- 여러 개발자의 동시 review merge
- cloud hosted multi-tenant 운영

## 실행 환경은 repository와 분리할 수 있다

`spec-trace` application repository 자체와 분석 대상 product repository는 같은 경로일 필요가 없다. workspace 설정에 Notion 문서와 Git repository 경로를 등록한다.

개발자는 현재 작업 중인 product repository를 read-only evidence source로 연결한다. `spec-trace`가 product repository의 branch, commit, working tree를 수정하지 않는다.

## MVP 완료 기준은 실제 round trip이다

애플리케이션 구조는 다음 흐름을 한 프로세스와 한 SQLite database에서 끝까지 실행할 수 있어야 한다:

1. Notion ROOT 등록
2. Snapshot 수집
3. 변경 감지
4. external agent packet export와 proposal import
5. 개발자 채택
6. Finding, Decision, OpenQuestion, Blocker 관리
7. Notion 개발 검토 projection과 PlannerAnswer 수집
8. FinalSpecRevision 확정
9. DevFlow handoff export
10. 새 Notion 변경 후 ChangeSet과 영향 재검토

## 다음 단계는 이 구조를 저장 schema로 내리는 일이다

다음 patch는 SQLite table, unique constraint, immutable history, transaction 경계를 정의한다. 저장 모델은 현재 도메인 객체와 application service의 책임을 그대로 반영한다.
