from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .permissions import Roles

User = get_user_model()


class UserManagementFixture(TestCase):
    def _user_with_role(self, username: str, role: str):
        user = User.objects.create_user(
            username=username,
            password="test-password-123",
        )
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return user


class UserRoleListAccessTests(UserManagementFixture):
    def test_admin_can_access_role_list(self):
        admin = self._user_with_role("role-admin", Roles.ADMIN)
        self.client.force_login(admin)

        response = self.client.get(reverse("accounts:user-role-list"))

        self.assertEqual(response.status_code, 200)

    def test_non_admin_cannot_access_role_list(self):
        analyst = self._user_with_role("role-analyst", Roles.ANALYST)
        self.client.force_login(analyst)

        response = self.client.get(reverse("accounts:user-role-list"))

        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_access_role_list(self):
        viewer = self._user_with_role("role-viewer", Roles.VIEWER)
        self.client.force_login(viewer)

        response = self.client.get(reverse("accounts:user-role-list"))

        self.assertEqual(response.status_code, 403)

    def test_search_filters_by_username(self):
        admin = self._user_with_role("role-admin-2", Roles.ADMIN)
        self._user_with_role("findme-user", Roles.VIEWER)
        self._user_with_role("other-user", Roles.VIEWER)
        self.client.force_login(admin)

        response = self.client.get(
            reverse("accounts:user-role-list"), {"q": "findme"}
        )

        usernames = [u.username for u in response.context["page_obj"]]
        self.assertIn("findme-user", usernames)
        self.assertNotIn("other-user", usernames)

    def test_role_filter_only_shows_matching_role(self):
        admin = self._user_with_role("role-admin-3", Roles.ADMIN)
        self._user_with_role("only-analyst", Roles.ANALYST)
        self._user_with_role("only-viewer", Roles.VIEWER)
        self.client.force_login(admin)

        response = self.client.get(
            reverse("accounts:user-role-list"), {"role": Roles.ANALYST}
        )

        usernames = [u.username for u in response.context["page_obj"]]
        self.assertIn("only-analyst", usernames)
        self.assertNotIn("only-viewer", usernames)


class UserCreateTests(UserManagementFixture):
    def test_admin_can_create_user_with_role(self):
        admin = self._user_with_role("creator-admin", Roles.ADMIN)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("accounts:user-create"),
            {
                "username": "user-baru",
                "email": "baru@example.com",
                "first_name": "Budi",
                "last_name": "Santoso",
                "role": Roles.ANALYST,
                "password1": "kompleks-password-123",
                "password2": "kompleks-password-123",
            },
        )

        self.assertEqual(response.status_code, 302)

        created = User.objects.get(username="user-baru")
        self.assertEqual(created.email, "baru@example.com")
        self.assertTrue(
            created.groups.filter(name=Roles.ANALYST).exists()
        )

    def test_password_is_hashed_not_stored_plain(self):
        admin = self._user_with_role("creator-admin-2", Roles.ADMIN)
        self.client.force_login(admin)

        self.client.post(
            reverse("accounts:user-create"),
            {
                "username": "user-hash-check",
                "role": Roles.VIEWER,
                "password1": "kompleks-password-123",
                "password2": "kompleks-password-123",
            },
        )

        created = User.objects.get(username="user-hash-check")
        self.assertNotEqual(created.password, "kompleks-password-123")
        self.assertTrue(created.check_password("kompleks-password-123"))

    def test_mismatched_passwords_rejected(self):
        admin = self._user_with_role("creator-admin-3", Roles.ADMIN)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("accounts:user-create"),
            {
                "username": "user-mismatch",
                "role": Roles.VIEWER,
                "password1": "kompleks-password-123",
                "password2": "beda-sekali-456",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            User.objects.filter(username="user-mismatch").exists()
        )

    def test_weak_password_rejected(self):
        admin = self._user_with_role("creator-admin-4", Roles.ADMIN)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("accounts:user-create"),
            {
                "username": "user-weak",
                "role": Roles.VIEWER,
                "password1": "12345678",
                "password2": "12345678",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            User.objects.filter(username="user-weak").exists()
        )

    def test_non_admin_cannot_create_user(self):
        analyst = self._user_with_role("creator-analyst", Roles.ANALYST)
        self.client.force_login(analyst)

        response = self.client.post(
            reverse("accounts:user-create"),
            {
                "username": "should-not-exist",
                "role": Roles.VIEWER,
                "password1": "kompleks-password-123",
                "password2": "kompleks-password-123",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            User.objects.filter(username="should-not-exist").exists()
        )


class UserRoleUpdateTests(UserManagementFixture):
    def test_admin_can_change_user_role(self):
        admin = self._user_with_role("updater-admin", Roles.ADMIN)
        target = self._user_with_role("target-user", Roles.VIEWER)
        self.client.force_login(admin)

        response = self.client.post(
            reverse("accounts:user-role-update", args=[target.id]),
            {"role": Roles.REVIEWER},
        )

        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertTrue(
            target.groups.filter(name=Roles.REVIEWER).exists()
        )
        self.assertFalse(
            target.groups.filter(name=Roles.VIEWER).exists()
        )

    def test_invalid_role_rejected(self):
        admin = self._user_with_role("updater-admin-2", Roles.ADMIN)
        target = self._user_with_role("target-user-2", Roles.VIEWER)
        self.client.force_login(admin)

        self.client.post(
            reverse("accounts:user-role-update", args=[target.id]),
            {"role": "BukanRoleValid"},
        )

        target.refresh_from_db()
        self.assertTrue(
            target.groups.filter(name=Roles.VIEWER).exists()
        )

    def test_admin_cannot_demote_self(self):
        admin = self._user_with_role("self-demote-admin", Roles.ADMIN)
        self.client.force_login(admin)

        self.client.post(
            reverse("accounts:user-role-update", args=[admin.id]),
            {"role": Roles.VIEWER},
        )

        admin.refresh_from_db()
        self.assertTrue(
            admin.groups.filter(name=Roles.ADMIN).exists()
        )
        self.assertFalse(
            admin.groups.filter(name=Roles.VIEWER).exists()
        )

    def test_non_admin_cannot_update_role(self):
        analyst = self._user_with_role("updater-analyst", Roles.ANALYST)
        target = self._user_with_role("target-user-3", Roles.VIEWER)
        self.client.force_login(analyst)

        response = self.client.post(
            reverse("accounts:user-role-update", args=[target.id]),
            {"role": Roles.ADMIN},
        )

        self.assertEqual(response.status_code, 403)
        target.refresh_from_db()
        self.assertTrue(
            target.groups.filter(name=Roles.VIEWER).exists()
        )
