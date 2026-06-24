"""build_service_badge_reference — 참고자료 서비스 뱃지 카탈로그 파생 (SERVICE_CATALOG 단일 진실, E7).

검증: 카탈로그 카테고리 전수 1:1 행, category·badge_class·ports_label 파생 정합, 한국어 라벨/설명 채움.
기대값은 모두 SERVICE_CATALOG 에서 재파생 — 하드코딩 0 ([2.4]).
"""

from assessment_engine.service_classifier import BADGE_CLASS_BY_CATEGORY, SERVICE_CATALOG
from assessment_engine.web.services.mappers.shared import build_service_badge_reference


def test_returns_catalog_rows_in_order():
    """카탈로그 카테고리(정의 순) 1:1 — 미식별(unknown) 행 미포함."""
    refs = build_service_badge_reference()
    assert len(refs) == len(SERVICE_CATALOG)
    assert [r.category for r in refs] == [d.key for d in SERVICE_CATALOG]


def test_badge_class_matches_catalog():
    """각 행 badge_class 가 BADGE_CLASS_BY_CATEGORY 파생과 일치."""
    for ref in build_service_badge_reference():
        assert ref.badge_class == BADGE_CLASS_BY_CATEGORY[ref.category]


def test_all_rows_have_label_and_desc():
    """모든 행이 한국어 라벨·설명을 가진다 (빈 값 0)."""
    for ref in build_service_badge_reference():
        assert ref.label_ko, f"{ref.category} label_ko 비어있음"
        assert ref.desc_ko, f"{ref.category} desc_ko 비어있음"


def test_catalog_labels_match_source():
    """카탈로그 행 label_ko/desc_ko 는 SERVICE_CATALOG(CategoryDef) 단일 진실에서 그대로 온다."""
    refs = {r.category: r for r in build_service_badge_reference()}
    for d in SERVICE_CATALOG:
        assert refs[d.key].label_ko == d.label_ko
        assert refs[d.key].desc_ko == d.desc_ko
