from assessment_engine.domain.service_classifier import BADGE_CLASS_BY_CATEGORY, SERVICE_CATALOG
from assessment_engine.web.services.mappers.service_reference import build_service_badge_reference


def test_returns_catalog_rows_in_order():
    refs = build_service_badge_reference()
    assert len(refs) == len(SERVICE_CATALOG)
    assert [r.category for r in refs] == [d.key for d in SERVICE_CATALOG]


def test_badge_class_matches_catalog():
    for ref in build_service_badge_reference():
        assert ref.badge_class == BADGE_CLASS_BY_CATEGORY[ref.category]


def test_all_rows_have_label_and_desc():
    for ref in build_service_badge_reference():
        assert ref.label_ko, f"{ref.category} label_ko 비어있음"
        assert ref.desc_ko, f"{ref.category} desc_ko 비어있음"


def test_catalog_labels_match_source():
    refs = {r.category: r for r in build_service_badge_reference()}
    for d in SERVICE_CATALOG:
        assert refs[d.key].label_ko == d.label_ko
        assert refs[d.key].desc_ko == d.desc_ko
