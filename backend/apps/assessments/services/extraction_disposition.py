"""Read-only routing of extraction outcomes in MEDINTEL.

A completed extraction attempt is not necessarily complete evidence. A missing
numeric value never means zero, and no missing value is synthesized here.
"""

CONTEXT_TYPES = frozenset({'risk_indicator', 'prevention_response'})


def _confirmed_category(article):
    review = (getattr(article, 'raw_metadata', None) or {}).get('information_type_review')
    if not isinstance(review, dict):
        return None
    category = review.get('category')
    return category if category in {'case_event', 'risk_indicator', 'prevention_response', 'uncertain'} else None


def extraction_disposition(article, *, complete, state, context_candidate=False):
    """Return an operational indication; not a decision on article validity.

    Returns dict with code/label/needs_human_review. The caller may display the
    result without changing ArticleFact, article status, validation or maps.
    """
    if state == 'rejected':
        return {'code': 'excluded', 'label': 'Dikecualikan dari ekstraksi', 'needs_human_review': False}
    if state == 'running':
        return {'code': 'running', 'label': 'Ekstraksi sedang berjalan', 'needs_human_review': False}
    metadata = getattr(article, 'raw_metadata', None) or {}
    result = metadata.get('extraction_review_result') or {}
    if state == 'failed' or (isinstance(result, dict) and result.get('ai_failed')):
        return {'code': 'technical_issue', 'label': 'Perlu pemeriksaan kendala teknis AI/ekstraksi', 'needs_human_review': True}

    category = _confirmed_category(article)
    has_disease = bool(getattr(article, 'extraction_has_disease', False))
    one_location = bool(getattr(article, 'extraction_has_location', False) and
                        getattr(article, 'extraction_primary_count', 0) == 1)

    if category in CONTEXT_TYPES:
        if has_disease and one_location:
            return {'code': 'context_ready', 'label': 'Konteks terkonfirmasi — data dasar tersedia', 'needs_human_review': False}
        return {'code': 'context_missing', 'label': 'Konteks terkonfirmasi — penyakit/lokasi belum memadai', 'needs_human_review': True}
    if complete:
        return {'code': 'case_evidence_ready', 'label': 'Bukti kejadian lengkap', 'needs_human_review': False}
    if category == 'uncertain' or (category is None and context_candidate):
        return {'code': 'classification_review', 'label': 'Perlu pemeriksaan kategori oleh analis', 'needs_human_review': True}
    if state == 'attempted' and isinstance(result, dict) and result.get('changed') is False:
        return {'code': 'no_new_evidence', 'label': 'Belum ditemukan bukti tambahan; periksa sumber', 'needs_human_review': True}
    return {'code': 'needs_evidence', 'label': 'Bukti kejadian belum lengkap', 'needs_human_review': True}
