---
meta:
  title: "기획 검토·변경 추적 시스템 문서는 어떻게 구성되는가"
  contentType: "Landing"
  category: "Internal planning"
status: "Draft"
---

# 기획 검토·변경 추적 시스템 문서는 어떻게 구성되는가

이 문서는 기획 검토·변경 추적 시스템의 전체 설계 문서를 연결하는 인덱스다. 시스템 정의부터 검토, Notion 입출력, 변경 추적, 후속 상세 설계까지 책임 단위로 문서를 분리한다.

## 문서 계획

- **대상**: 기획 검토와 구현 기준 확정을 담당하는 개발자
- **목표**: 필요한 설계 문서를 목적별로 찾아 읽고 전체 업무 흐름과 추적 관계를 설명할 수 있다
- **범위**: 시스템 정의, 검토 모델, 업무 흐름, 도메인·상태 모델, Notion 입출력, 원문 수집, 변경 분석, MVP 아키텍처, 결정 근거, 후속 설계 범위
- **현재 경계**: 개발자 CLI와 분석·검토 등록 인터페이스까지 확정
- **읽는 순서**: 시스템 정의 → 검토 모델 → 업무 흐름 → 내부 상세 모델 → Notion 입출력 → 원문 수집 실행 → 추적 구조 → 후속 상세 설계

## 시스템 정의

시스템 정의 문서는 해결하려는 문제, 책임 경계, 주요 산출물을 설명한다.

- [시스템 정의와 역할](./system/definition.md): 해결하려는 문제, 핵심 원칙, 기획자·개발자·인공지능(AI)·시스템의 책임
- [문서와 산출물](./system/artifacts.md): Notion 원문, 로컬 파생 산출물, 기획자용 개발 검토, 최종설계, 실제 구현 참조의 역할

## 검토와 의사결정

검토 문서는 발견한 문제를 어떤 축으로 분류하고 누가 결정하는지 설명한다.

- [검토와 의사결정 모델](./review/review-model.md): Finding 유형, 결정 주체, OpenQuestion, Blocker와 BlockedScope
- [기획자용 Notion 결과](./review/notion-output.md): 기획자에게 보여줄 현재 상태, 개발 결정, 확인 필요 항목, Blocker의 정보 구조

## 업무 흐름

업무 흐름 문서는 신규 검토부터 개발 중 기획 변경까지 실제 처리 순서를 설명한다.

- [전체 업무 흐름과 상태](./workflow/overview.md): 공통 흐름, 반복 규칙, 업무 상태
- [신규 설계서 검토](./workflow/new-design-review.md): 신규 PlanningDocumentSnapshot을 검토하고 첫 FinalSpecRevision을 확정하는 흐름
- [개발 중 기획 변경 대응](./workflow/change-during-development.md): 신규 PlanningDocumentSnapshot과 현재 구현 기준의 영향을 다시 검토하는 흐름

## 내부 상세 모델

내부 상세 모델은 원문, 검토, 결정, 구현을 연결하는 객체와 상태를 정의한다.

- [도메인 모델](./design/domain-model.md): PlanningDocument, SourcePage, Snapshot, Finding, Decision, DerivedArtifact 등 핵심 객체
- [상태 모델](./design/state-model.md): ReviewCycle, Finding, OpenQuestion, Decision, Blocker의 상태와 전이
- [추적 모델](./design/traceability-model.md): PlanningDocumentSnapshot 변경, 영향 분석, 최종설계, 실제 구현의 연결 관계

## Notion 입출력과 원문 수집

Notion 연동 문서는 회사 기획서 구조를 내부 모델과 연결하고 안정적인 원문 Snapshot을 만드는 규칙을 정의한다.

- [Notion 원문 계약](./integration/notion-source-contract.md): 데이터베이스 행 페이지, `/페이지` 하위 페이지, SourceReference, Snapshot, 로컬 미러 분류
- [Notion 원문 수집 실행](./integration/notion-source-collection.md): 수집 주기, 이중 트리 검증, canonicalization, hash, Snapshot 확정 결과
- [Notion 검토 결과 계약](./integration/notion-review-contract.md): ROOT 아래 `개발 검토` 페이지, answer slot, 시스템·기획자 소유권
- [Notion 동기화 규칙](./integration/notion-sync-rules.md): idempotency, 부분 실패, 삭제와 이동, reconcile, 자기 변경 루프 방지
- [연동 실패와 재시도](./integration/runtime-resilience.md): 중앙 rate limit, HTTP retry, 오류 분류, 재시작 복구

## 변경 분석 실행

변경 분석 실행 문서는 새 Snapshot에서 변경 검토를 시작하는 처리 계약을 정의한다.

- [Snapshot 변경 감지](./analysis/change-detection.md): `ChangeSet` 생성 조건, 물리적 page change, 멱등성 규칙
- [Source Diff 실행](./analysis/source-diff.md): 의미 변경 후보 schema, 원문 근거, coverage, `ChangeItem` 채택 규칙
- [Impact Analysis 실행](./analysis/impact-analysis.md): 현재 개발 기준, scope manifest, 영향 후보, 코드 freshness 규칙
- [분석 후보 채택](./analysis/adoption.md): `AnalysisProposal`, 개발자 `ReviewAction`, `ChangeItem`·`ImpactLink` 확정 규칙

## 변경과 결정 추적

추적 개념 문서는 변경 분석과 결정 근거 추적의 목적을 설명한다.

- [기획 변경 비교 기준](./traceability/change-analysis.md): Source Diff와 Impact Analysis의 목적과 비교 대상
- [최종설계서와 결정 근거 추적](./traceability/decision-traceability.md): Notion 원문부터 실제 구현까지의 역추적 목적

## MVP 구현 구조

MVP 구현 구조 문서는 확정된 처리 계약을 실행 가능한 애플리케이션 구조로 내린다.

- [MVP 애플리케이션 아키텍처](./architecture/mvp-architecture.md): Python CLI, SQLite, Notion·Git adapter, external agent contract, 실행 범위
- [MVP 저장 schema](./storage/mvp-schema.md): SQLite table, content-addressed artifact, unique constraint, transaction, 복구 queue
- [개발자 CLI](./interface/developer-cli.md): workspace, collect, analysis import, proposal review, Decision, OpenQuestion, Blocker, FinalSpec command

## 후속 상세 설계

후속 설계 문서는 현재 확정한 경계와 다음 설계 순서를 관리한다.

- [후속 상세 설계 범위](./design/implementation-boundaries.md): 확정된 내부 모델과 Notion 연동 계약, 다음 실행 설계 순서

현재 다음 설계 단계는 FinalSpecRevision과 DevFlow 연동 계약이다.
