from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.accounts.management.commands.seed_role_users import ROLE_USERS
from apps.accounts.permissions import Roles


User = get_user_model()


class SeedRoleUsersCommandTests(TestCase):
    password = "MedIntel-Role-Test!2026"

    def test_creates_one_account_for_every_role_without_touching_suryadi(self):
        suryadi = User.objects.create_user(
            username="suryadi",
            password="Suryadi-Original!2026",
            email="suryadi@example.test",
        )

        call_command(
            "seed_role_users",
            password=self.password,
            stdout=StringIO(),
        )

        suryadi.refresh_from_db()
        self.assertTrue(suryadi.check_password("Suryadi-Original!2026"))
        self.assertEqual(suryadi.email, "suryadi@example.test")

        for seed in ROLE_USERS:
            with self.subTest(role=seed.role):
                user = User.objects.get(username=seed.username)
                self.assertTrue(user.is_active)
                self.assertEqual(user.is_staff, seed.is_staff)
                self.assertFalse(user.is_superuser)
                self.assertTrue(user.check_password(self.password))
                self.assertSetEqual(
                    set(user.groups.values_list("name", flat=True)),
                    {seed.role},
                )

        self.assertSetEqual(
            {
                seed.role
                for seed in ROLE_USERS
            },
            set(Roles.ALL),
        )
        self.assertEqual(User.objects.count(), 5)

    def test_second_run_is_idempotent_and_keeps_existing_passwords(self):
        call_command(
            "seed_role_users",
            password=self.password,
            stdout=StringIO(),
        )
        call_command(
            "seed_role_users",
            password="Password-Baru-Tidak-Dipakai!2026",
            stdout=StringIO(),
        )

        self.assertEqual(User.objects.count(), len(ROLE_USERS))
        for seed in ROLE_USERS:
            user = User.objects.get(username=seed.username)
            self.assertTrue(user.check_password(self.password))

    def test_reset_password_only_changes_seed_accounts(self):
        suryadi = User.objects.create_user(
            username="suryadi",
            password="Suryadi-Original!2026",
        )
        call_command(
            "seed_role_users",
            password=self.password,
            stdout=StringIO(),
        )

        new_password = "MedIntel-Role-Baru!2026"
        call_command(
            "seed_role_users",
            password=new_password,
            reset_password=True,
            stdout=StringIO(),
        )

        suryadi.refresh_from_db()
        self.assertTrue(suryadi.check_password("Suryadi-Original!2026"))
        for seed in ROLE_USERS:
            user = User.objects.get(username=seed.username)
            self.assertTrue(user.check_password(new_password))

