from types import SimpleNamespace
from django.test import SimpleTestCase
from apps.assessments.services.extraction_disposition import extraction_disposition


def article(category=None, disease=True, locations=1, result=None):
    md = {'information_type_review': {'category': category}} if category else {}
    if result is not None:
        md['extraction_review_result'] = result
    return SimpleNamespace(raw_metadata=md, extraction_has_disease=disease,
                           extraction_has_location=bool(locations), extraction_primary_count=locations)


class ExtractionDispositionTests(SimpleTestCase):
    def test_vaccination_without_case_numbers_is_context_ready(self):
        result = extraction_disposition(article('prevention_response'), complete=False, state='attempted')
        self.assertEqual(result['code'], 'context_ready')

    def test_missing_location_keeps_context_under_review(self):
        result = extraction_disposition(article('risk_indicator', locations=0), complete=False, state='attempted')
        self.assertEqual(result['code'], 'context_missing')

    def test_case_without_numeric_evidence_is_not_complete(self):
        result = extraction_disposition(article('case_event'), complete=False, state='unknown')
        self.assertEqual(result['code'], 'needs_evidence')

    def test_no_new_evidence_is_not_equivalent_to_zero_cases(self):
        result = extraction_disposition(article('case_event', result={'changed': False}), complete=False, state='attempted')
        self.assertEqual(result['code'], 'no_new_evidence')

    def test_ai_failure_is_separate_from_missing_source_evidence(self):
        result = extraction_disposition(article('case_event', result={'ai_failed': True}), complete=False, state='attempted')
        self.assertEqual(result['code'], 'technical_issue')

    def test_unconfirmed_title_suggestion_requires_review(self):
        result = extraction_disposition(article(), complete=False, state='unknown', context_candidate=True)
        self.assertEqual(result['code'], 'classification_review')
