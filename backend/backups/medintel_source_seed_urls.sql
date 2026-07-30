BEGIN;

INSERT INTO public.sources_sourceseedurl (
    id,
    url,
    seed_type,
    priority,
    is_active,
    notes,
    created_at,
    updated_at,
    source_id
)
SELECT
    gen_random_uuid(),
    s.base_url,
    'listing',
    100,
    s.is_active,
    'Seed URL awal otomatis dari base_url. Perlu diuji dan diperbarui ke halaman listing, RSS, atau sitemap yang lebih spesifik.',
    NOW(),
    NOW(),
    s.id
FROM public.sources_source AS s
WHERE
    s.base_url IS NOT NULL
    AND TRIM(s.base_url) <> ''
ON CONFLICT (source_id, url)
DO UPDATE SET
    seed_type = EXCLUDED.seed_type,
    is_active = EXCLUDED.is_active,
    notes = EXCLUDED.notes,
    updated_at = NOW();

COMMIT;