"""Membuat akun uji operasional untuk setiap role MedIntel."""

from __future__ import annotations

import os
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.permissions import Roles


@dataclass(frozen=True)
class RoleUserSeed:
    username: str
    first_name: str
    last_name: str
    email: str
    role: str
    is_staff: bool = False


ROLE_USERS = (
    RoleUserSeed(
        username="medintel_admin",
        first_name="Admin",
        last_name="MedIntel",
        email="admin@medintel.test",
        role=Roles.ADMIN,
        is_staff=True,
    ),
    RoleUserSeed(
        username="medintel_analyst",
        first_name="Analis",
        last_name="MedIntel",
        email="analyst@medintel.test",
        role=Roles.ANALYST,
    ),
    RoleUserSeed(
        username="medintel_reviewer",
        first_name="Reviewer",
        last_name="MedIntel",
        email="reviewer@medintel.test",
        role=Roles.REVIEWER,
    ),
    RoleUserSeed(
        username="medintel_viewer",
        first_name="Viewer",
        last_name="MedIntel",
        email="viewer@medintel.test",
        role=Roles.VIEWER,
    ),
)


class Command(BaseCommand):
    help = (
        "Membuat satu akun aktif untuk setiap role MedIntel tanpa "
        "mengubah akun pengguna lain."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            help=(
                "Password awal seluruh akun role. Jika tidak diberikan, "
                "gunakan environment variable MEDINTEL_ROLE_USER_PASSWORD."
            ),
        )
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help=(
                "Ganti password akun role yang sudah ada. Tanpa opsi ini, "
                "password akun lama dipertahankan."
            ),
        )

    def handle(self, *args, **options):
        password = options.get("password") or os.getenv(
            "MEDINTEL_ROLE_USER_PASSWORD"
        )
        if not password:
            raise CommandError(
                "Password wajib diberikan melalui --password atau "
                "MEDINTEL_ROLE_USER_PASSWORD."
            )

        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc

        reset_password = bool(options.get("reset_password"))
        User = get_user_model()
        created_count = 0
        updated_count = 0
        password_reset_count = 0

        with transaction.atomic():
            for seed in ROLE_USERS:
                group, _ = Group.objects.get_or_create(name=seed.role)
                user, created = User.objects.get_or_create(
                    username=seed.username,
                    defaults={
                        "first_name": seed.first_name,
                        "last_name": seed.last_name,
                        "email": seed.email,
                        "is_active": True,
                        "is_staff": seed.is_staff,
                        "is_superuser": False,
                    },
                )

                changed_fields = []
                profile_values = {
                    "first_name": seed.first_name,
                    "last_name": seed.last_name,
                    "email": seed.email,
                    "is_active": True,
                    "is_staff": seed.is_staff,
                }
                for field_name, expected_value in profile_values.items():
                    if getattr(user, field_name) != expected_value:
                        setattr(user, field_name, expected_value)
                        changed_fields.append(field_name)

                if created or reset_password:
                    user.set_password(password)
                    changed_fields.append("password")
                    if not created:
                        password_reset_count += 1

                if changed_fields:
                    user.save(update_fields=sorted(set(changed_fields)))

                # Akun contoh merepresentasikan tepat satu role. Group lain
                # pada akun operasional, termasuk akun suryadi, tidak disentuh.
                current_roles = user.groups.filter(name__in=Roles.ALL)
                if set(current_roles.values_list("name", flat=True)) != {
                    seed.role
                }:
                    user.groups.remove(*current_roles)
                    user.groups.add(group)

                if created:
                    created_count += 1
                    result = "dibuat"
                else:
                    updated_count += 1
                    result = "sudah ada/diperbarui"

                self.stdout.write(
                    f"- {seed.username} | {seed.role} | {result}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Seed akun role selesai | "
                f"dibuat={created_count} | "
                f"sudah_ada={updated_count} | "
                f"password_direset={password_reset_count}"
            )
        )

