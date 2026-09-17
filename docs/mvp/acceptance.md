---
meta:
  title: "첫 MVP가 실제 업무 흐름을 끝까지 처리하는가"
  contentType: "Reference"
  category: "Internal planning"
status: "Draft"
---

# 첫 MVP가 실제 업무 흐름을 끝까지 처리하는가

이 문서는 spec-trace 첫 MVP의 end-to-end acceptance scenario와 완료 gate를 정의한다. 구현은 신규 기획 검토, 기획자 답변, DevFlow handoff, 개발 중 기획 변경을 하나의 추적 계보로 끝까지 처리해야 한다.

## acceptance는 한 개의 대표 기획 문서로 검증한다

대표 fixture는 ROOT page 하나와 COMPOSED_CHILD 두 개를 가진다. 하나의 child page는 정책 설명, 다른 child page는 상세 입력 규칙을 담는다.

초기 상태에는 다음 내용이 포함된다:

- 개발자가 기존 정책으로 결정할 수 있는 중복 규칙 1건
- 기획자 결정이 필요한 누락 규칙 1건
- 누락 규칙 때문에 일부 구현만 막히는 Blocker 1건
- BlockedScope 밖에서 진행 가능한 작업 1건

## 시나리오 A는 workspace와 원문 계보를 만든다

개발자는 새 workspace를 초기화하고 code repository와 Notion ROOT를 등록한다.

통과 조건은 다음과 같다:

1. `spec-trace init`을 두 번 실행해도 기존 상태가 유지된다
2. 같은 repository를 중복 등록하지 않는다
3. 같은 Notion ROOT를 중복 등록하지 않는다
4. 최초 `collect`가 `SNAPSHOT_CREATED`를 반환한다
5. ROOT와 두 COMPOSED_CHILD가 같은 PlanningDocumentSnapshot에 포함된다
6. 같은 원문을 다시 수집하면 `UNCHANGED`를 반환한다
7. 최초 Snapshot에는 `ChangeSet`을 만들지 않는다

## 시나리오 B는 신규 기획 검토를 개발자 채택까지 진행한다

개발자는 `REVIEW` analysis packet을 export하고 fixture agent response를 import한다. response는 개발자 결정 후보, 기획자 OpenQuestion 후보, Blocker scope 후보를 포함한다.

통과 조건은 다음과 같다:

1. import 직후 Finding과 Decision은 생성되지 않는다
2. proposal candidate를 채택하면 Finding이 생성된다
3. `decision_owner=DEVELOPER` Finding은 개발자가 evidence와 함께 Decision을 채택한다
4. `decision_owner=PLANNER` Finding은 OpenQuestion으로 publish한다
5. blocking Finding은 하나 이상의 BlockedScope를 가진 Blocker로 확정한다
6. ReviewAction과 원본 proposal payload를 역추적할 수 있다

## 시나리오 C는 기획자용 Notion projection을 만든다

내부 검토 상태를 확정한 뒤 `sync`를 실행한다. fake Notion adapter와 live smoke 모두 같은 projection contract를 사용한다.

통과 조건은 다음과 같다:

1. ROOT 아래 시스템 소유 `개발 검토` page가 하나만 존재한다
2. 개발자 Decision은 확인용으로 표시되고 answer slot을 만들지 않는다
3. OpenQuestion은 선택지, tradeoff, 개발 권장안, answer slot을 가진다
4. Blocker는 막힌 범위와 현재 진행 가능한 범위를 함께 보여준다
5. `sync`를 반복해도 review page와 question section을 중복 생성하지 않는다
6. 시스템 projection 변경만으로 새 PlanningDocumentSnapshot이 생기지 않는다

## 시나리오 D는 PlannerAnswer를 재검증해 제품 Decision으로 만든다

fixture planner는 answer slot에 답변을 작성한다. 다음 `sync`가 답변을 수집한다.

통과 조건은 다음과 같다:

1. 비어 있지 않은 새 answer만 PlannerAnswer로 생성한다
2. 같은 답변을 다시 읽어도 중복 PlannerAnswer를 만들지 않는다
3. 답변을 수정하면 새 PlannerAnswer가 이전 답변을 supersede한다
4. PlannerAnswer 수집만으로 Decision을 만들지 않는다
5. `answer verify` 성공 후 `Decision(owner=PLANNER)`을 만든다
6. Blocker resume condition을 만족하면 Blocker를 resolve할 수 있다
7. resolved scope의 `resume_work`가 후속 실행 대상으로 돌아온다

## 시나리오 E는 FinalSpecRevision과 DevFlow handoff를 만든다

모든 구현 필수 Finding을 해결한 뒤 FinalSpec을 확정한다.

통과 조건은 다음과 같다:

1. unresolved blocking Finding이 있으면 `final-spec create`가 실패한다
2. 해결 후 Revision을 생성하면 current Snapshot과 Decision을 모두 연결한다
3. 같은 content를 임의로 기존 Revision에 덮어쓰지 않는다
4. `devflow export`가 manifest, final spec, decisions, blockers, traceability file을 만든다
5. manifest content hash가 실제 final-spec file과 일치한다
6. handoff 직전 더 최신 pending Snapshot이 있으면 stale로 판정한다

## 시나리오 F는 구현 commit을 ImplementationRef로 연결한다

fixture Git repository에 FinalSpec을 반영한 commit을 만든 뒤 implementation receipt를 import한다.

통과 조건은 다음과 같다:

1. 존재하지 않는 commit SHA receipt는 거부한다
2. 존재하지 않는 path ref는 거부한다
3. 유효한 receipt는 `ImplementationRef`를 만든다
4. ImplementationRef에서 FinalSpecRevision과 Decision을 찾을 수 있다
5. 코드 commit에서 PlanningDocumentSnapshot까지 역추적할 수 있다

## 시나리오 G는 개발 중 기획 변경을 기존 구현과 비교한다

기획자가 child page의 정책 하나를 변경하고 다른 요구사항 하나를 추가한다. 새 안정 Snapshot을 수집한다.

통과 조건은 다음과 같다:

1. 새 `PlanningDocumentSnapshot`과 하나의 `ChangeSet`을 만든다
2. physical change가 변경된 page 범위를 설명한다
3. Source Diff proposal import만으로 `ChangeItem`이 생기지 않는다
4. 개발자 채택 후 의미별 `ChangeItem`을 만든다
5. `WORDING_ONLY` candidate는 기본 영향 분석에서 제외된다
6. Impact packet이 현재 FinalSpecRevision, Decision, code baseline을 포함한다
7. Impact candidate 채택 전 기존 Decision 상태가 바뀌지 않는다
8. `REVIEW_REQUIRED`나 `INVALIDATED` 채택 후 영향받은 Finding만 재검토한다
9. 변경과 무관한 Decision은 유지한다
10. 새 구현 기준이 필요하면 이전 Revision을 보존하고 새 FinalSpecRevision을 만든다

## 시나리오 H는 수집 중 변경과 원격 장애를 복구한다

fixture adapter가 수집과 projection 실패를 의도적으로 발생시킨다.

통과 조건은 다음과 같다:

- 시작·종료 tree가 다르면 Snapshot 후보를 폐기한다
- `SOURCE_UNSTABLE` retry를 소진해도 마지막 정상 Snapshot을 유지한다
- `429`와 `529`는 `Retry-After` 정책을 따른다
- Notion projection 실패가 내부 Decision을 rollback하지 않는다
- process restart 뒤 pending operation과 projection reconcile을 먼저 실행한다
- 같은 recovery operation이 dedupe key 때문에 중복 실행되지 않는다

## 자동 acceptance는 외부 서비스 없이 재현 가능해야 한다

CI와 로컬 test suite는 fake Notion adapter와 임시 Git repository를 사용해 시나리오 A부터 H까지 실행한다. test는 실제 integration token이나 회사 Notion workspace를 요구하지 않는다.

fake adapter는 pagination, nested block, child page, archive, rate limit, write timeout, answer slot 수정 시나리오를 재현한다.

## live smoke는 실제 Notion API 호환성을 확인한다

실제 운영 전에 전용 테스트 page에서 live smoke를 실행한다. 이 검증은 다음 항목만 확인한다:

1. page metadata와 recursive block 조회
2. child page 탐색
3. 개발 검토 page 생성·재사용
4. answer slot 읽기
5. projection reconcile

live smoke는 제품 기획 page를 사용하지 않는다. integration token은 환경 변수로만 전달한다.

## MVP 완료 gate는 네 범주를 모두 통과해야 한다

첫 MVP는 다음 gate를 만족하면 완료로 판정한다:

- **Contract**: JSON schema와 state invariant test 통과
- **Persistence**: migration, idempotency, restart recovery test 통과
- **Workflow**: 시나리오 A부터 H 자동 acceptance 통과
- **Integration**: Notion live smoke를 실행할 수 있는 command와 문서가 존재하며 실제 배포 전 smoke 성공

현재 작업에서 자동 검증 가능한 첫 세 gate를 통과해야 patch series를 완료한다. 실제 Notion credential이 필요한 live smoke 결과는 사용자 환경에서 별도로 기록한다.

## acceptance evidence는 한 명령으로 수집한다

MVP repository는 다음 검증 명령을 제공한다:

```bash
python -m unittest discover -s tests -v
```

추가 dependency 없이 test를 실행할 수 있어야 한다. live smoke는 별도 opt-in command로 분리해 일반 test에서 외부 Notion에 요청하지 않는다.

## 다음 단계는 acceptance를 구현 작업으로 분해하는 일이다

다음 patch는 코드 작성 순서, patch 경계, 각 단계의 검증 명령을 정의한다. 이후 patch부터 실제 Python MVP를 구현한다.
