BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO public.entities_surveillanceprogram (
    id, name, code, legal_basis, document_number,
    document_year, status, notes, created_at, updated_at
)
VALUES (
    gen_random_uuid(),
    'Program Surveilans Penyakit Menular',
    'skdr-penyakit-menular',
    'Permenkes Nomor 45 Tahun 2014 dan Permenkes Nomor 1501 Tahun 2010',
    '45/2014; 1501/2010',
    2014,
    'active',
    'Baseline development MedIntel untuk penyaringan artikel OSINT penyakit menular.',
    NOW(),
    NOW()
)
ON CONFLICT (code)
DO UPDATE SET
    name = EXCLUDED.name,
    legal_basis = EXCLUDED.legal_basis,
    document_number = EXCLUDED.document_number,
    document_year = EXCLUDED.document_year,
    status = 'active',
    notes = EXCLUDED.notes,
    updated_at = NOW();

INSERT INTO public.entities_disease (
    id, name, canonical_name, code, category, description,
    is_priority, is_active, created_at, updated_at
)
VALUES
(
    gen_random_uuid(), 'Tuberkulosis', 'Tuberculosis',
    'tuberkulosis', 'menular_langsung', '',
    TRUE, TRUE, NOW(), NOW()
),
(
    gen_random_uuid(), 'Malaria', 'Malaria',
    'malaria', 'tular_vektor', '',
    TRUE, TRUE, NOW(), NOW()
),
(
    gen_random_uuid(), 'HIV/AIDS',
    'Human Immunodeficiency Virus / Acquired Immunodeficiency Syndrome',
    'hiv-aids', 'menular_langsung', '',
    TRUE, TRUE, NOW(), NOW()
)
ON CONFLICT (code)
DO UPDATE SET
    name = EXCLUDED.name,
    canonical_name = EXCLUDED.canonical_name,
    category = EXCLUDED.category,
    is_priority = TRUE,
    is_active = TRUE,
    updated_at = NOW();

INSERT INTO public.entities_diseasealias (
    disease_id, alias, language, is_active, created_at
)
SELECT d.id, v.alias, 'id', TRUE, NOW()
FROM public.entities_disease d
JOIN (
    VALUES
        ('tuberkulosis', 'Tuberkulosis'),
        ('tuberkulosis', 'TBC'),
        ('tuberkulosis', 'TB'),
        ('tuberkulosis', 'Tuberculosis'),
        ('malaria', 'Malaria'),
        ('hiv-aids', 'HIV'),
        ('hiv-aids', 'AIDS'),
        ('hiv-aids', 'HIV/AIDS')
) AS v(code, alias)
ON d.code = v.code
ON CONFLICT (disease_id, alias)
DO UPDATE SET
    language = EXCLUDED.language,
    is_active = TRUE;

INSERT INTO public.entities_surveillancedisease (
    id, program_id, disease_id, official_name, category,
    is_active, notes, created_at, updated_at
)
SELECT
    gen_random_uuid(),
    p.id,
    d.id,
    d.name,
    d.category,
    TRUE,
    'Baseline development MedIntel.',
    NOW(),
    NOW()
FROM public.entities_surveillanceprogram p
JOIN public.entities_disease d
    ON d.code IN ('tuberkulosis', 'malaria', 'hiv-aids')
WHERE p.code = 'skdr-penyakit-menular'
ON CONFLICT (program_id, disease_id)
DO UPDATE SET
    official_name = EXCLUDED.official_name,
    category = EXCLUDED.category,
    is_active = TRUE,
    notes = EXCLUDED.notes,
    updated_at = NOW();

COMMIT;
