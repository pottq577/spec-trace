---
meta:
  title: "첫 MVP를 어떤 순서로 구현하고 검증하는가"
  contentType: "How-to"
  category: "Internal planning"
status: "Draft"
---

# 첫 MVP를 어떤 순서로 구현하고 검증하는가

이 문서는 [MVP acceptance](./acceptance.md)를 실제 Python 코드로 구현하는 patch 순서와 각 단계의 검증 gate를 정의한다. 각 patch는 앞 patch가 적용된 상태에서 독립 검증을 통과해야 한다.

## 구현 patch는 다섯 단계로 나눈다

MVP 코드는 다음 patch 순서로 구현한다:

1. `PATCH-13`: Python package, workspace, SQLite migration, content store
2. `PATCH-14`: Notion adapter, canonicalization, source collection, Snapshot, ChangeSet
3. `PATCH-15`: analysis packet, proposal import, review action, ChangeItem, ImpactLink
4. `PATCH-16`: Finding, Decision, OpenQuestion, Blocker, projection, FinalSpec, DevFlow handoff
5. `PATCH-17`: CLI 전체 연결, fake adapter acceptance, recovery test, live smoke command

기능을 뒤 patch로 미루더라도 앞 단계의 공개 contract를 임시 동작으로 속이지 않는다. 아직 지원하지 않는 command는 명확한 validation error를 반환한다.

## PATCH-13은 실행 가능한 저장 기반을 만든다

첫 코드 patch는 다음 파일을 만든다:

- `pyproject.toml`
- `src/spec_trace` package와 `__main__.py`
- workspace configuration
- SQLite connection과 migration runner
- `0001_initial.sql`
- content-addressed artifact store
- repository registration
- ID, time, JSON utility
- storage와 migration unit test

검증 gate는 다음과 같다:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m spec_trace --help
```

## PATCH-14는 Notion 원문을 Snapshot까지 처리한다

두 번째 코드 patch는 다음 책임을 추가한다:

- Notion port와 `ntn api` live adapter
- GET retry와 process 내부 rate limit
- page, block pagination
- child page tree capture
- system review page 제외
- canonical content와 hash
- double tree stability check
- `SourcePageSnapshot`과 `PlanningDocumentSnapshot` 확정
- physical change와 `ChangeSet` 생성
- fake Notion collector test

이 patch가 끝나면 acceptance 시나리오 A와 H의 collection 부분을 통과해야 한다.

## PATCH-15는 외부 agent 결과를 채택 가능한 이력으로 만든다

세 번째 코드 patch는 다음 책임을 추가한다:

- source-diff, impact, review packet export
- proposal JSON validation
- `AnalysisProposal`과 candidate 저장
- `ReviewAction`
- Source Diff 채택과 `ChangeItem`
- Impact 채택과 `ImpactLink`
- stale code baseline 검증
- contract fixture test

이 patch가 끝나면 자동 분석 결과가 개발자 채택 전까지 도메인 상태를 바꾸지 않는다는 invariant를 test로 보장한다.

## PATCH-16은 검토 round trip과 구현 기준을 완성한다

네 번째 코드 patch는 다음 책임을 추가한다:

- `ReviewCycle`과 Finding
- 개발자 Decision
- OpenQuestion과 PlannerAnswer
- Blocker와 BlockedScope
- Notion 개발 검토 projection
- answer slot 수집과 hash
- FinalSpecRevision
- DevFlow export
- implementation receipt import

이 patch가 끝나면 acceptance 시나리오 B부터 F까지 application service 수준에서 실행할 수 있어야 한다.

## PATCH-17은 CLI와 end-to-end acceptance를 닫는다

마지막 MVP patch는 다음 책임을 추가한다:

- 설계 문서에 정의한 CLI command 연결
- `status --json`
- `watch` recovery ordering
- fake Notion end-to-end scenario A부터 H
- 임시 Git repository 기반 ImplementationRef test
- live Notion smoke command
- 사용자 실행 README

최종 자동 gate는 다음 한 명령으로 통과해야 한다:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 테스트는 시간과 외부 서비스 의존성을 주입한다

unit과 acceptance test는 실제 sleep, wall clock, Notion network에 의존하지 않는다. application service는 clock, sleeper, Notion port를 주입받는다.

`ntn` adapter test는 fake runner와 virtual sleeper가 받은 command와 delay를 검증한다. Git test는 test가 생성한 임시 repository와 실제 `git` executable을 사용한다.

## live smoke는 명시적 환경 변수로만 활성화한다

일반 test는 실제 `ntn` subprocess나 Notion을 호출하지 않는다. live smoke는 별도 command와 test page ID를 함께 지정해야 실행된다.

live smoke를 실행하기 전에 `ntn login`으로 인증을 완료한다. 필수 환경 변수는 다음과 같다:

- `SPEC_TRACE_LIVE_DATABASE_ID`
- `SPEC_TRACE_LIVE_PAGE_ID`

`NTN_BIN`과 `NOTION_VERSION`은 기본값을 바꿀 때만 지정한다. spec-trace는 `NOTION_TOKEN`을 읽지 않는다.

live smoke는 시스템이 소유한 테스트 page에서만 수행한다.

## patch마다 migration과 contract compatibility를 확인한다

schema가 바뀌면 새 migration을 추가한다. 기존 migration을 수정하지 않는다.

JSON contract가 바뀌면 `contract_version`을 올리고 이전 version을 거부하거나 명시적 upgrade path를 제공한다. 같은 version에서 field 의미를 바꾸지 않는다.

## 구현 중 범위를 줄일 때 acceptance를 기준으로 판단한다

MVP에서 제외할 수 있는 것은 웹 UI, model API 자동 호출, webhook, multi-user 기능이다. acceptance A부터 H에 필요한 원문 계보, 개발자 채택, 기획자 답변, FinalSpec, DevFlow handoff는 축소하지 않는다.

## MVP 완료 후 다음 단계는 실제 업무 fixture로 검증하는 일이다

PATCH-17 자동 gate가 통과하면 코드 기준 첫 MVP를 완료한다. 다음 실제 적용 단계에서는 회사 Notion의 한 개 기획 문서를 테스트 대상으로 등록해 live smoke와 첫 실제 ReviewCycle을 수행한다.
