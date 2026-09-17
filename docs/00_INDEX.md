---
meta:
  title: "기획 검토·변경 추적 시스템 문서는 어떻게 구성되는가"
  contentType: "Landing"
  category: "Internal planning"
status: "Draft"
---

# 기획 검토·변경 추적 시스템 문서는 어떻게 구성되는가

이 문서는 기획 검토·변경 추적 시스템의 전체 설계 문서를 연결하는 인덱스다. 시스템 정의부터 검토와 의사결정, 업무 흐름, 내부 도메인 모델, 변경 추적, 후속 상세 설계 범위까지 책임 단위로 문서를 분리한다.

## 문서 계획

- **대상**: 기획 검토와 구현 기준 확정을 담당하는 개발자
- **목표**: 필요한 설계 문서를 목적별로 찾아 읽고 전체 업무 흐름과 추적 관계를 설명할 수 있다
- **범위**: 시스템 정의, 검토 모델, 업무 흐름, 도메인 모델, 상태 모델, 변경 추적, 결정 근거, 후속 설계 범위
- **현재 경계**: Notion I/O 계약 직전의 내부 모델까지 확정
- **읽는 순서**: 시스템 정의 → 검토 모델 → 업무 흐름 → 도메인·상태 모델 → 추적 구조 → 후속 상세 설계

## 시스템 정의

시스템 정의 문서는 해결하려는 문제, 책임 경계, 주요 산출물을 설명한다.

- [시스템 정의와 역할](./system/definition.md): 해결하려는 문제, 핵심 원칙, 기획자·개발자·AI·시스템의 책임
- [문서와 산출물](./system/artifacts.md): 기획 원문, 검토 명세서, 기획자용 개발 검토, 최종설계서의 역할

## 검토와 의사결정

검토 문서는 발견한 문제를 어떤 축으로 분류하고 누가 결정하는지 설명한다.

- [검토와 의사결정 모델](./review/review-model.md): Finding 유형, 결정 주체, Open Question, Blocker와 `BlockedScope`
- [기획자용 Notion 결과](./review/notion-output.md): 개발 결정과 기획자 확인 항목을 Notion에 보여주는 방식

## 업무 흐름

업무 흐름 문서는 신규 검토부터 개발 중 기획 변경까지 실제 처리 순서를 설명한다.

- [전체 업무 흐름과 상태](./workflow/overview.md): 공통 흐름, 반복 규칙, 업무 상태
- [신규 설계서 검토](./workflow/new-design-review.md): 신규 기획 원문을 검토하고 최종설계서를 확정하는 흐름
- [개발 중 기획 변경 대응](./workflow/change-during-development.md): 신규 기획 원문이 등록됐을 때 변경점과 개발 영향을 다시 검토하는 흐름

## 내부 상세 모델

내부 상세 모델은 Notion I/O 계약 전에 고정해야 하는 객체, 상태, 추적 관계를 정의한다.

- [도메인 모델](./design/domain-model.md): 핵심 객체, 식별자, 관계, 불변 규칙
- [상태 모델](./design/state-model.md): `ReviewCycle`, Finding, Open Question, Decision, Blocker의 상태와 전이
- [추적 모델](./design/traceability-model.md): Snapshot 변경, 영향 분석, 최종설계, 실제 구현의 연결 관계

## 변경과 결정 추적

추적 개념 문서는 변경 분석과 결정 근거 추적의 목적을 설명한다.

- [기획 변경 비교 기준](./traceability/change-analysis.md): Source Diff와 Impact Analysis의 목적과 비교 대상
- [최종설계서와 결정 근거 추적](./traceability/decision-traceability.md): 기획 원문부터 실제 구현까지의 역추적 목적

## 후속 상세 설계

후속 설계 문서는 현재 확정한 경계와 다음 설계 순서를 관리한다.

- [후속 상세 설계 범위](./design/implementation-boundaries.md): 현재 확정한 내부 모델, 다음 Notion I/O 계약, 이후 구현 설계 순서

현재 다음 설계 단계는 Notion I/O 계약이다.
