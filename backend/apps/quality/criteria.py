"""Instrumen baku untuk validasi ahli dan UAT MedIntel.

Instrumen disimpan sebagai kode agar definisi pertanyaan konsisten lintas
responden, sedangkan jawaban dan catatan disimpan pada EvaluationRecord.
"""

EXPERT_CRITERIA = (
    {
        "code": "scope_alignment",
        "dimension": "Kesesuaian sistem",
        "label": "Kesesuaian fungsi dengan kebutuhan intelijen medik",
    },
    {
        "code": "collection_validity",
        "dimension": "Validitas proses",
        "label": "Ketepatan alur pengumpulan dan seleksi artikel OSINT",
    },
    {
        "code": "extraction_validity",
        "dimension": "Validitas proses",
        "label": "Ketepatan ekstraksi penyakit, lokasi, dan fakta",
    },
    {
        "code": "signal_validity",
        "dimension": "Efektivitas",
        "label": "Kelayakan logika pembentukan dan konfirmasi sinyal",
    },
    {
        "code": "assessment_traceability",
        "dimension": "Kualitas informasi",
        "label": "Ketertelusuran bukti pada assessment ancaman",
    },
    {
        "code": "early_warning_usefulness",
        "dimension": "Efektivitas",
        "label": "Kegunaan peringatan dini untuk kewaspadaan dan cegah dini",
    },
    {
        "code": "recommendation_actionability",
        "dimension": "Efektivitas",
        "label": "Keterlaksanaan rekomendasi intelijen",
    },
    {
        "code": "report_accountability",
        "dimension": "Akuntabilitas",
        "label": "Akuntabilitas laporan dan jejak keputusan analis",
    },
)


UAT_CRITERIA = (
    {
        "code": "usability",
        "dimension": "Kualitas sistem",
        "label": "Kemudahan penggunaan alur utama",
    },
    {
        "code": "speed",
        "dimension": "Kualitas sistem",
        "label": "Kecepatan respons dan penyelesaian tugas",
    },
    {
        "code": "information_quality",
        "dimension": "Kualitas informasi",
        "label": "Kejelasan dan kelengkapan informasi yang ditampilkan",
    },
    {
        "code": "reliability",
        "dimension": "Kualitas sistem",
        "label": "Keandalan fungsi selama skenario uji",
    },
    {
        "code": "visualization",
        "dimension": "Kualitas sistem",
        "label": "Keterbacaan dashboard, peta, dan produk analitis",
    },
    {
        "code": "validation_control",
        "dimension": "Kualitas sistem",
        "label": "Kejelasan kontrol validasi dan koreksi analis",
    },
    {
        "code": "decision_support",
        "dimension": "Efektivitas",
        "label": "Kegunaan sistem untuk mendukung keputusan intelijen",
    },
)


UAT_TASKS = (
    ("directed_collection", "Membuat kebutuhan dan menjalankan koleksi terarah"),
    ("article_validation", "Memvalidasi artikel, penyakit, lokasi, dan fakta"),
    ("signal_confirmation", "Meninjau dan mengonfirmasi sinyal intelijen"),
    ("threat_assessment", "Menyelesaikan assessment ancaman"),
    ("warning_recommendation", "Menerbitkan peringatan/rekomendasi"),
    ("intelligence_report", "Menyusun dan mengekspor laporan intelijen"),
)


def criteria_for(evaluation_type: str):
    return EXPERT_CRITERIA if evaluation_type == "expert" else UAT_CRITERIA

