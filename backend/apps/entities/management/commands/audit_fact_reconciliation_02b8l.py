"""Read-only reconciliation for numeric facts, defaulting to six audited IDs."""
from __future__ import annotations
import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand
from apps.entities.models import ArticleFact
from apps.entities.services.fact_reconciliation import assess_fact, suggested_numeric_quote

AUDITED_IDS = (
    "ab307533-791a-40d1-92c9-1b1aa9e7f878",  # rabies Jembrana
    "995e8fec-ac5d-41b3-95c3-6ecd8e5191dd",  # DBD Badung
    "51736a56-c4b1-478c-8251-85ac18c649bc",  # DBD Pekanbaru
    "a55b149d-8b9c-4b0b-ac80-0ef6724191c9",  # malaria Korea
    "6d732635-523d-4a80-a043-5aed1979dd00",  # COVID Denpasar
    "f857e555-0917-40ef-ba61-6f9b0f59def6",  # campak Aceh
)


def csv_safe(value):
    """Keep untrusted article text from becoming spreadsheet formulas."""
    result = str(value if value is not None else "")
    if result.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + result
    return result


class Command(BaseCommand):
    help = "Audit fakta MEDINTEL tanpa memperbarui data (02B8L)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Periksa seluruh ArticleFact.")
        parser.add_argument("--article-id", action="append", default=[], help="UUID artikel; boleh berulang.")
        parser.add_argument("--output", help="Lokasi CSV. Default: Desktop/MEDINTEL_REKONSILIASI_02B8L_*.csv")

    def handle(self, *args, **options):
        if options["all"] and options["article_id"]:
            raise ValueError("Pilih --all ATAU --article-id, bukan keduanya.")
        ids = None if options["all"] else options["article_id"] or AUDITED_IDS
        q = ArticleFact.objects.select_related("article", "disease", "location")
        if ids is not None:
            q = q.filter(article_id__in=ids)
        rows = list(q.order_by("article_id", "created_at", "id"))
        groups = defaultdict(list)
        for fact in rows:
            groups[str(fact.article_id)].append(fact)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = Path(options["output"]).expanduser() if options["output"] else (
            Path.home()/"Desktop"/f"MEDINTEL_REKONSILIASI_02B8L_{stamp}.csv"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        counts = Counter()
        fields = ["article_id", "fact_id", "title", "url", "case_count", "death_count",
                  "disease", "location", "event_date", "reporting_period_start", "reporting_period_end", "reporting_period_precision", "case_count_basis", "status", "period_hint",
                  "suggested_primary_disease_NOT_APPROVED", "issues", "possible_sibling_ids",
                  "fact_quote", "suggested_quote_REVIEW_REQUIRED", "review_decision_MANUAL", "review_notes_MANUAL"]
        with output.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for fact in rows:
                finding = assess_fact(fact, siblings=groups[str(fact.article_id)])
                counts.update(finding.issues)
                writer.writerow(dict(zip(fields, map(csv_safe, [
                    fact.article_id, fact.pk, fact.article.title,
                    fact.article.original_url, fact.case_count, fact.death_count,
                    fact.disease.name if fact.disease else "",
                    fact.location.name if fact.location else "",
                    fact.event_date,
                    fact.reporting_period_start, fact.reporting_period_end,
                    fact.reporting_period_precision, fact.case_count_basis,
                    fact.validation_status, finding.period_hint, finding.primary_disease_suggestion,
                    " | ".join(finding.issues), " | ".join(finding.sibling_fact_ids),
                    (fact.fact_text or "")[:350],
                    suggested_numeric_quote(fact.article.content_text or "", fact.case_count), "", "",
                ]))))
        self.stdout.write(f"Fakta: {len(rows)} | Artikel: {len(groups)} | Tanpa perubahan database")
        for issue, count in sorted(counts.items()):
            self.stdout.write(f"  {issue}: {count}")
        self.stdout.write(f"CSV: {output}")
        self.stdout.write("Kolom saran bukan persetujuan. Koreksi hanya setelah pemeriksaan manusia.")
