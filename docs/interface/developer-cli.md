---
meta:
  title: "개발자는 MVP에서 검토와 결정을 어떻게 등록하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 개발자는 MVP에서 검토와 결정을 어떻게 등록하는가

이 문서는 첫 MVP의 CLI command, machine-readable 출력, 외부 agent review import, 개발자 채택 흐름을 정의한다. CLI는 application service를 호출하며 SQLite row를 직접 수정하지 않는다.

## 모든 command는 workspace를 기준으로 실행한다

기본 workspace는 현재 디렉터리다. `--workspace path`를 지정하면 해당 경로의 `.spec-trace` 상태를 사용한다.

공통 option은 다음과 같다:

- `--workspace path`: workspace root
- `--json`: 사람이 읽는 출력 대신 JSON envelope 출력
- `--no-color`: 터미널 색상 비활성화

`NOTION_TOKEN`처럼 secret 값은 command argument로 받지 않는다.

## workspace init이 실행 환경을 만든다

초기 명령은 다음 형식을 사용한다:

```bash
spec-trace init
```

`init`은 `.spec-trace` 디렉터리, SQLite database, content store, analysis request·response 경로를 만든다. 이미 초기화된 workspace에서는 migration만 확인하고 기존 데이터를 유지한다.

## repository add가 코드 근거 저장소를 등록한다

분석 대상 Git repository는 다음 명령으로 등록한다:

```bash
spec-trace repo add --name peoplo --path ../peoplo-backend
```

등록 시 CLI는 Git repository 여부와 현재 commit 조회 가능 여부를 검증한다. 경로는 workspace 기준 상대 경로로 저장할 수 있으며 실제 EvidenceRef는 commit SHA를 함께 사용한다.

조회 명령은 다음 형식을 사용한다:

```bash
spec-trace repo list
```

## document register가 Notion ROOT를 등록한다

모니터링 시작점은 다음 명령으로 등록한다:

```bash
spec-trace document register \
  --database-id notion_database_id_here \
  --page-id notion_page_id_here
```

등록 시 ROOT page를 한 번 조회해 접근 권한, archive 상태, database parent를 검증한다. title은 Notion에서 읽으며 local 입력값을 정식 identity로 사용하지 않는다.

같은 database와 ROOT page를 다시 등록하면 기존 `PlanningDocument`를 반환한다.

## collect와 watch가 원문 수집을 실행한다

특정 문서를 즉시 수집하려면 다음 명령을 사용한다:

```bash
spec-trace collect --document planning_document_id_here
```

모든 등록 문서를 한 번 확인하려면 `--all`을 사용한다. 장기 polling은 다음 명령으로 실행한다:

```bash
spec-trace watch
```

`watch` 기본 interval은 `300s`다. process는 미완료 `pending_operations`와 projection reconcile을 먼저 처리하고 정기 collection을 실행한다.

## status가 현재 lifecycle을 한 화면에 보여준다

상태 조회는 다음 명령을 사용한다:

```bash
spec-trace status --document planning_document_id_here
```

기본 출력은 다음 항목을 순서대로 보여준다:

- current source status와 Snapshot
- pending ChangeSet과 분석 단계
- current ReviewCycle
- unresolved Finding과 OpenQuestion
- active Blocker와 BlockedScope
- current FinalSpecRevision
- pending projection과 recovery operation

`--json`은 같은 정보를 stable field 이름으로 반환한다.

## analysis export가 외부 agent 입력 packet을 만든다

Source Diff packet은 다음 명령으로 만든다:

```bash
spec-trace analysis export \
  --type source-diff \
  --change-set change_set_id_here
```

Impact packet은 `--type impact`, 신규·재검토 Finding 분석 context는 `--type review`를 사용한다. 결과 파일은 기본적으로 `.spec-trace/analysis/requests` 아래에 생성한다.

packet은 contract version, immutable Snapshot, code baseline, evidence source를 포함한다. prompt 문구 자체는 contract가 아니며 packet을 소비하는 agent별 wrapper가 추가할 수 있다.

## analysis import가 구조화 proposal을 검증한다

외부 agent 결과는 다음 명령으로 가져온다:

```bash
spec-trace analysis import .spec-trace/analysis/responses/result.json
```

import는 JSON schema, contract version, subject reference, Snapshot, evidence locator, code baseline을 검증한다. 성공하면 `AnalysisProposal`을 만들고 도메인 상태는 아직 바꾸지 않는다.

같은 subject와 payload hash를 다시 import하면 기존 proposal을 반환한다.

## REVIEW proposal도 같은 채택 계약을 사용한다

`AnalysisProposal.analysis_type`은 MVP에서 다음 값을 지원한다:

- `SOURCE_DIFF`
- `IMPACT`
- `REVIEW`

`REVIEW` candidate는 다음 필드를 제안할 수 있다:

- `finding_type`
- `decision_owner`
- `blocking`
- `summary`
- `evidence_refs`
- `options`
- `tradeoffs`
- `developer_recommendation`
- `blocked_scope_candidates`

개발자가 candidate를 채택해야 `Finding`이 생성된다. 제품 결정 후보는 채택 시 `OpenQuestion` 초안을 만들 수 있고, blocking candidate는 Blocker scope 검토 대상으로 연결된다.

## proposal show가 candidate와 근거를 보여준다

검토할 proposal은 다음 명령으로 연다:

```bash
spec-trace proposal show analysis_proposal_id_here
```

기본 출력은 candidate별 summary, 분류, source 또는 code evidence, 현재 freshness, 검토 상태를 보여준다. 긴 원문 전문은 기본 출력에 반복하지 않고 locator를 통해 요청할 때 펼친다.

## proposal review가 개발자 채택 행위를 기록한다

후보를 그대로 채택하려면 다음 명령을 사용한다:

```bash
spec-trace proposal review \
  --proposal analysis_proposal_id_here \
  --candidate candidate_id_here \
  --action adopt
```

수정 채택은 `--action edit-and-adopt --payload file.json`, 거절은 `--action reject --reason text`를 사용한다. split과 merge는 새 candidate payload file을 입력받아 proposal revision을 만든다.

한 번의 batch file로 여러 candidate action을 적용하는 `--actions file.json`도 지원한다. batch는 하나의 database transaction으로 처리하지 않고 candidate별 결과를 반환해 한 항목의 오류가 다른 검토 이력을 숨기지 않게 한다.

## decision adopt가 개발자 결론을 확정한다

`decision_owner=DEVELOPER` Finding의 결론은 다음 명령으로 채택한다:

```bash
spec-trace decision adopt \
  --finding finding_id_here \
  --payload decision.json
```

payload는 `adopted_option`, `rationale`, evidence refs를 포함한다. 최소 하나의 EvidenceRef가 없으면 Decision을 만들지 않는다.

기존 Decision을 대체하면 payload에 `supersedes_decision_id`를 포함한다.

## question publish가 기획자 질문을 Notion 대상으로 확정한다

`decision_owner=PLANNER` Finding의 OpenQuestion은 다음 명령으로 검토 후 활성화한다:

```bash
spec-trace question publish --finding finding_id_here --payload question.json
```

payload는 question, options, tradeoffs, developer recommendation을 포함한다. 성공하면 projection operation을 예약한다.

기획자의 실제 입력은 Notion answer slot에서 수집하므로 CLI에서 답변 원문을 대신 입력하지 않는다.

## answer verify가 PlannerAnswer를 Decision으로 채택한다

새 PlannerAnswer가 수집되면 다음 명령으로 재검증한다:

```bash
spec-trace answer verify \
  --answer planner_answer_id_here \
  --payload verification.json
```

검증 성공 payload는 `Decision(owner=PLANNER)`에 필요한 adopted option, rationale, evidence를 포함한다. 추가 확인이 필요하면 `--reopen --reason text`를 사용해 질문을 다시 연다.

## blocker set이 실제 차단 범위를 확정한다

blocking Finding은 다음 명령으로 Blocker를 확정한다:

```bash
spec-trace blocker set --finding finding_id_here --payload blocker.json
```

payload는 reason, resume condition, 하나 이상의 `BlockedScope`, scope 밖에서 진행 가능한 범위를 포함한다. 빈 scope 목록은 거부한다.

Blocker 해결은 `blocker resolve blocker_id_here --reason text`로 기록하고 `resume_work` 대상 operation을 다시 활성화한다.

## final-spec create가 현재 구현 기준을 고정한다

검토가 FinalSpec 확정 조건을 만족하면 다음 명령을 사용한다:

```bash
spec-trace final-spec create \
  --document planning_document_id_here \
  --content docs/final-spec.md
```

CLI는 current Snapshot, 활성 Decision, unresolved blocking Finding을 검증한다. 조건을 만족하면 content hash를 계산하고 새 `FinalSpecRevision`을 만든다.

기존 Revision이 있으면 자동으로 `previous_revision_id`를 연결한다.

## sync는 Notion projection과 answer 수집을 reconcile한다

즉시 동기화는 다음 명령으로 실행한다:

```bash
spec-trace sync --document planning_document_id_here
```

명령은 먼저 answer slot의 새 입력을 수집하고 내부 state를 저장한다. 이후 현재 내부 상태를 기준으로 개발 검토 projection을 맞춘다.

projection 실패는 nonzero exit code와 recovery operation을 남기며 이미 수집한 PlannerAnswer를 되돌리지 않는다.

## exit code는 자동화 가능한 범위로 고정한다

CLI는 다음 exit code를 사용한다:

- `0`: 성공
- `2`: 입력 또는 contract validation 실패
- `3`: 대상 resource를 찾을 수 없음
- `4`: stale proposal, state conflict, precondition 실패
- `5`: Notion, Git 같은 외부 연동 실패
- `6`: persistence 또는 internal invariant 실패

`--json` 사용 시 stderr에는 machine payload를 중복 출력하지 않는다. stdout envelope의 `ok`, `code`, `result`, `errors`로 결과를 구분한다.

## command는 사람이 수정할 파일 위치를 명확히 반환한다

analysis export, validation failure, split·merge처럼 파일 수정이 필요한 경우 CLI는 생성하거나 다시 제출할 workspace 상대 경로를 출력한다. 긴 JSON을 terminal에서 직접 편집하게 하지 않는다.

이 원칙은 개발자가 Codex나 ChatGPT에 해당 파일을 바로 전달할 수 있게 한다.

## 다음 단계는 FinalSpec을 DevFlow 입력으로 연결하는 일이다

다음 patch는 `FinalSpecRevision`, Decision, BlockedScope를 DevFlow가 소비할 수 있는 handoff packet으로 변환하고 ImplementationRef를 다시 수집하는 계약을 정의한다.
