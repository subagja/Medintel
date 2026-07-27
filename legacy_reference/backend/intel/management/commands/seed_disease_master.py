from django.core.management.base import BaseCommand

from intel.models import DiseaseMaster, Signal
from intel.services.disease_master import sync_signal_disease_master


SKDR_DISEASES = [
    {"name": "Diare Akut", "code": "A", "aliases": "diare, diare akut", "type": "waterborne", "severity": "medium", "rule": "trend_based"},
    {"name": "Malaria", "legacy_names": ["Malaria Konfirmasi"], "code": "B", "aliases": "malaria", "type": "vector_borne", "severity": "high", "rule": "trend_based"},
    {"name": "Demam Dengue", "legacy_names": ["Tersangka Demam Dengue"], "code": "C", "aliases": "dbd, dengue, demam berdarah, demam dengue", "type": "vector_borne", "severity": "high", "rule": "trend_based"},
    {"name": "Pneumonia", "code": "D", "aliases": "pneumonia, radang paru", "type": "respiratory", "severity": "high", "rule": "trend_based"},
    {"name": "Disentri", "legacy_names": ["Diare Berdarah atau Disentri"], "code": "E", "aliases": "disentri, diare berdarah", "type": "waterborne", "severity": "high", "rule": "immediate"},
    {"name": "Demam Tifoid", "legacy_names": ["Tersangka Demam Tifoid"], "code": "F", "aliases": "tifoid, typhoid, demam tifoid, tipes", "type": "foodborne", "severity": "medium", "rule": "trend_based"},
    {"name": "Jaundis Akut", "legacy_names": ["Sindrom Jaundis Akut"], "code": "G", "aliases": "jaundis akut, ikterus akut, hepatitis akut", "type": "other", "severity": "high", "rule": "immediate"},
    {"name": "Chikungunya", "legacy_names": ["Tersangka Chikungunya"], "code": "H", "aliases": "chikungunya", "type": "vector_borne", "severity": "medium", "rule": "trend_based"},
    {"name": "Flu Burung pada Manusia", "legacy_names": ["Tersangka Flu Burung pada Manusia"], "code": "J", "aliases": "flu burung manusia, flu burung, avian influenza, h5n1, h5n6, h9n2", "type": "zoonosis", "severity": "critical", "rule": "immediate", "emerging": True},
    {"name": "Campak", "legacy_names": ["Tersangka Campak"], "code": "K", "aliases": "campak, measles", "type": "vaccine_preventable", "severity": "high", "rule": "reemerging", "reemerging": True},
    {"name": "Difteri", "legacy_names": ["Tersangka Difteri"], "code": "L", "aliases": "difteri, diphtheria", "type": "vaccine_preventable", "severity": "critical", "rule": "immediate", "reemerging": True},
    {"name": "Pertussis", "legacy_names": ["Tersangka Pertussis"], "code": "M", "aliases": "pertussis, pertusis, batuk rejan", "type": "vaccine_preventable", "severity": "high", "rule": "reemerging", "reemerging": True},
    {"name": "Polio/AFP", "legacy_names": ["AFP / Lumpuh Layuh Mendadak"], "code": "N", "aliases": "afp, lumpuh layuh mendadak, polio", "type": "vaccine_preventable", "severity": "critical", "rule": "immediate", "reemerging": True},
    {"name": "Rabies", "legacy_names": ["Kasus Gigitan Hewan Penular Rabies"], "code": "P", "aliases": "rabies, gigitan hewan penular rabies, ghpr", "type": "zoonosis", "severity": "critical", "rule": "immediate", "reemerging": True},
    {"name": "Antraks", "legacy_names": ["Tersangka Antraks"], "code": "Q", "aliases": "antraks, anthrax", "type": "zoonosis", "severity": "critical", "rule": "immediate", "emerging": True},
    {"name": "Leptospirosis", "legacy_names": ["Tersangka Leptospirosis"], "code": "R", "aliases": "leptospirosis", "type": "zoonosis", "severity": "high", "rule": "immediate"},
    {"name": "Kolera", "legacy_names": ["Tersangka Kolera"], "code": "S", "aliases": "kolera, cholera", "type": "waterborne", "severity": "critical", "rule": "immediate"},
    {"name": "Klaster Penyakit yang Tidak Lazim", "code": "T", "aliases": "klaster penyakit tidak lazim, penyakit tidak lazim, penyakit tidak diketahui, unknown outbreak", "type": "unknown_cluster", "severity": "high", "rule": "unknown_cluster", "emerging": True},
    {"name": "Meningitis/Ensefalitis", "legacy_names": ["Tersangka Meningitis/Ensefalitis"], "code": "U", "aliases": "meningitis, ensefalitis, encephalitis", "type": "other", "severity": "critical", "rule": "immediate"},
    {"name": "Tersangka Tetanus Neonatorum", "code": "V", "aliases": "tetanus neonatorum", "type": "vaccine_preventable", "severity": "critical", "rule": "immediate"},
    {"name": "Tetanus", "legacy_names": ["Tersangka Tetanus"], "code": "W", "aliases": "tetanus", "type": "vaccine_preventable", "severity": "critical", "rule": "immediate"},
    {"name": "Influenza Like Illness", "legacy_names": ["ILI / Influenza Like Illness"], "code": "X", "aliases": "ili, influenza like illness, influenza, flu", "type": "respiratory", "severity": "medium", "rule": "trend_based"},
    {"name": "HFMD", "legacy_names": ["Tersangka HFMD / Hand Foot Mouth Disease"], "code": "Y", "aliases": "hfmd, hand foot mouth disease, flu singapura", "type": "other", "severity": "medium", "rule": "trend_based"},
    {"name": "Covid-19", "legacy_names": ["Tersangka Covid-19"], "code": "AC", "aliases": "covid, covid-19, coronavirus, sars-cov-2", "type": "respiratory", "severity": "high", "rule": "trend_based"},
]


EMERGING_WATCHLIST = [
    {"name": "Mpox", "aliases": "mpox, monkeypox, cacar monyet", "type": "zoonosis", "severity": "high"},
    {"name": "MERS-CoV", "aliases": "mers, mers-cov, middle east respiratory syndrome", "type": "respiratory", "severity": "critical"},
    {"name": "Nipah", "aliases": "nipah, nipah virus", "type": "zoonosis", "severity": "critical"},
    {"name": "Ebola", "legacy_names": ["Ebola/Marburg"], "aliases": "ebola, ebola virus disease", "type": "zoonosis", "severity": "critical"},
    {"name": "Marburg", "legacy_names": ["Ebola/Marburg"], "aliases": "marburg, marburg virus disease", "type": "zoonosis", "severity": "critical"},
    {"name": "Yellow Fever", "aliases": "yellow fever, demam kuning", "type": "vector_borne", "severity": "critical"},
    {"name": "Lassa Fever", "aliases": "lassa, lassa fever, demam lassa", "type": "zoonosis", "severity": "critical"},
    {"name": "CCHF", "aliases": "cchf, crimean-congo haemorrhagic fever, crimean-congo hemorrhagic fever", "type": "zoonosis", "severity": "critical"},
    {"name": "Rift Valley Fever", "aliases": "rift valley fever, rvf", "type": "zoonosis", "severity": "critical"},
    {"name": "Hantavirus", "aliases": "hantavirus, hanta virus", "type": "zoonosis", "severity": "high"},
    {"name": "Legionellosis", "aliases": "legionellosis, legionella", "type": "respiratory", "severity": "high"},
]


class Command(BaseCommand):
    help = "Seed Disease Master with SKDR priority diseases and emerging watchlist."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync-signals",
            action="store_true",
            help="Match existing signals to Disease Master after seeding.",
        )

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        deactivated_legacy_count = 0

        for item in SKDR_DISEASES:
            obj, created = DiseaseMaster.objects.update_or_create(
                name=item["name"],
                defaults={
                    "aliases": item.get("aliases", ""),
                    "skdr_code": item.get("code", ""),
                    "skdr_priority": True,
                    "report_24h": item.get("rule") in ["immediate", "unknown_cluster"],
                    "emerging_watchlist": item.get("emerging", False),
                    "reemerging_watch": item.get("reemerging", False),
                    "disease_type": item.get("type", "other"),
                    "severity_weight": item.get("severity", "medium"),
                    "alert_rule": item.get("rule", "trend_based"),
                    "keyword_id": item.get("aliases", ""),
                    "is_active": True,
                },
            )
            created_count += 1 if created else 0
            updated_count += 0 if created else 1
            legacy_names = item.get("legacy_names", [])
            if legacy_names:
                deactivated_legacy_count += DiseaseMaster.objects.filter(
                    name__in=legacy_names,
                    is_active=True,
                ).exclude(id=obj.id).update(
                    is_active=False,
                    notes="Dinonaktifkan oleh seed karena crawling memakai nama subject penyakit.",
                )

        for item in EMERGING_WATCHLIST:
            obj, created = DiseaseMaster.objects.update_or_create(
                name=item["name"],
                defaults={
                    "aliases": item.get("aliases", ""),
                    "skdr_priority": False,
                    "report_24h": True,
                    "emerging_watchlist": True,
                    "reemerging_watch": False,
                    "disease_type": item.get("type", "other"),
                    "severity_weight": item.get("severity", "high"),
                    "alert_rule": "novelty_based",
                    "keyword_id": item.get("aliases", ""),
                    "keyword_en": item.get("aliases", ""),
                    "is_active": True,
                },
            )
            created_count += 1 if created else 0
            updated_count += 0 if created else 1
            legacy_names = item.get("legacy_names", [])
            if legacy_names:
                deactivated_legacy_count += DiseaseMaster.objects.filter(
                    name__in=legacy_names,
                    is_active=True,
                ).exclude(id=obj.id).update(
                    is_active=False,
                    notes="Dinonaktifkan oleh seed karena crawling memakai nama subject penyakit.",
                )

        synced_count = 0
        if options["sync_signals"]:
            for signal in Signal.objects.all().iterator():
                before_id = signal.disease_master_id
                disease = sync_signal_disease_master(signal, save=True)
                if disease and before_id != disease.id:
                    synced_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Disease Master seed done. Created: {created_count}. Updated: {updated_count}. Synced signals: {synced_count}."
                f" Deactivated legacy labels: {deactivated_legacy_count}."
            )
        )
