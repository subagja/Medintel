from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .permissions import Roles


User = get_user_model()


class WorkspaceRoleEnforcementTests(TestCase):
    workspace_urls = (
        "indicators:review",
        "dashboard:signal-workspace",
        "dashboard:threat-assessment",
        "dashboard:early-warning",
        "dashboard:intelligence-recommendation",
    )

    def _user_with_role(self, username: str, role: str):
        user = User.objects.create_user(
            username=username,
            password="test-password-123",
        )
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return user

    def test_viewer_can_read_operational_workspaces(self):
        viewer = self._user_with_role("workspace-viewer", Roles.VIEWER)
        self.client.force_login(viewer)

        for url_name in self.workspace_urls:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_submit_operational_mutations(self):
        viewer = self._user_with_role("mutation-viewer", Roles.VIEWER)
        self.client.force_login(viewer)

        for url_name in self.workspace_urls:
            with self.subTest(url_name=url_name):
                response = self.client.post(reverse(url_name), {"action": "x"})
                self.assertEqual(response.status_code, 403)

    def test_contributor_requests_reach_workspace_handlers(self):
        analyst = self._user_with_role("workspace-analyst", Roles.ANALYST)
        self.client.force_login(analyst)

        for url_name in self.workspace_urls:
            with self.subTest(url_name=url_name):
                response = self.client.post(reverse(url_name), {"action": "x"})
                self.assertNotEqual(response.status_code, 403)
