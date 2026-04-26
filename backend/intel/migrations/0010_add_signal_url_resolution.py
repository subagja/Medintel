from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0009_resolvedsourceurl_signal_resolved_url_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="signal",
            name="resolved_url",
            field=models.URLField(blank=True, default="", max_length=1000),
        ),
        migrations.AddField(
            model_name="signal",
            name="url_resolution_status",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="signal",
            name="url_resolution_method",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="signal",
            name="url_resolution_error",
            field=models.TextField(blank=True, default=""),
        ),
    ]