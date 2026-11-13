"""UI regression tests for 02B8M contextual footer.

This is a template contract test. Browser action must still be checked manually
via DevTools Network; Django POST handler is already covered in previous tests.
"""
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


class ValidationFooterContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template_text = (
            Path(settings.BASE_DIR) / "templates" / "dashboard" / "article_validation.html"
        ).read_text(encoding="utf-8")

    def test_sticky_footer_is_context_aware(self):
        html = self.template_text
        self.assertIn('data-medintel-context-save', html)
        self.assertIn('syncContextualFooterAction();', html)
        self.assertIn('syncContextualFooterAction();\n        });', html)

    def test_disease_tab_submits_disease_action(self):
        html = self.template_text
        self.assertIn('disease: {\n            value: "correct_primary_disease"', html)
        self.assertIn('label: "Simpan Penyakit Utama"', html)
        self.assertIn('notesField.reportValidity();', html)

    def test_other_tab_actions_preserved(self):
        html = self.template_text
        for action in (
            'save_validation_status',
            'correct_primary_location',
            'save_assessment',
        ):
            self.assertIn('value: "' + action + '"', html)
        self.assertIn('contextualSaveButton.classList.add("d-none");', html)
        self.assertIn('contextualSaveButton.value = config.value;', html)

    def test_submitter_action_still_copied_to_hidden_field(self):
        html = self.template_text
        self.assertIn('const action = submitter.value || "save_assessment";', html)
        self.assertIn('actionInput.value = action;', html)
        self.assertIn('submitter.disabled = true;', html)
