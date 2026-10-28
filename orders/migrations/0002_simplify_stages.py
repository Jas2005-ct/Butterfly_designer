"""Fold cutting/fitting orders into stitching and drop those two choices.

The shop runs four stages: measurement -> stitching -> ready -> delivery.
Cutting is part of the stitching work, and a fitting trial is not a stage an
order sits in, so both were removed from OrderStage.

Existing orders in either stage are remapped to "stitching". That is the
conservative direction: it never presents work as cleared for hand-over that
has not been, and an order that was in fitting still has a "stitching" Stage
History row, so the timeline's current-stage highlight keeps working.

Stage History rows are deliberately NOT remapped. They are an audit trail of
what actually happened; rewriting them would claim milestones the order never
reached. Old rows keep their cutting/fitting values and are rendered by the
explicit branches that remain in _stage_timeline.html.

The choice changes are app-level only - OrderStage is a plain CharField with
no database constraint - so AlterField touches no data.

Remap runs first on purpose: if the second half failed, code reading a
leftover 'cutting' still works (choices are only app-level), whereas a
half-migrated schema with stale data would leave orders showing a stage the
new code has no column colours or transitions for.
"""

from django.db import migrations, models

# Stages removed from the model, folded into the stage that replaced them.
REMOVED_STAGES = ("cutting", "fitting")


def fold_removed_stages(apps, schema_editor):
    """Move orders out of the two stages that no longer exist.

    Why stitching for both: an order in cutting has not been finished, and an
    order in fitting may still need altering, so neither is "ready". Staff see
    them as work in progress, which is exactly where they were.

    Args:
        apps: historical model registry, used to fetch Order without
            importing the current model definition.
        schema_editor: present because RunPython calls it; unused here.

    Returns:
        None. Rewrites Order.stage in place for the affected rows only.
    """
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(stage__in=REMOVED_STAGES).update(stage="stitching")


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        # Data first - see module docstring for why the order matters.
        migrations.RunPython(fold_removed_stages, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="order",
            name="stage",
            field=models.CharField(
                choices=[
                    ("measurement", "Measurement"),
                    ("stitching", "Stitching"),
                    ("ready", "Ready"),
                    ("delivered", "Delivery"),
                    ("cancelled", "Cancelled"),
                ],
                default="measurement",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="orderstagehistory",
            name="from_stage",
            field=models.CharField(
                blank=True,
                choices=[
                    ("measurement", "Measurement"),
                    ("stitching", "Stitching"),
                    ("ready", "Ready"),
                    ("delivered", "Delivery"),
                    ("cancelled", "Cancelled"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="orderstagehistory",
            name="to_stage",
            field=models.CharField(
                choices=[
                    ("measurement", "Measurement"),
                    ("stitching", "Stitching"),
                    ("ready", "Ready"),
                    ("delivered", "Delivery"),
                    ("cancelled", "Cancelled"),
                ],
                max_length=20,
            ),
        ),
    ]
