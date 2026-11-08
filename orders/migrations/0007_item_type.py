"""Garment type per line: add OrderItem.item_type, retire Order.service_type.

The shop stopped telling the system what is being made twice. The garment
now lives on each line (the ITEM dropdown), so this one migration, in order:

1. add item_type - every existing line starts at Blouse,
2. backfill it from the order's old service_type: blouse stays blouse,
   saree alteration / bridal / other all fold into "Other tailoring",
3. drop service_type.

The reverse walks back from each order's FIRST line, so rolling this
migration back restores meaningful board badges instead of resetting every
order to the field default of "Other".
"""

from django.db import migrations, models

# The old ServiceType.choices, verbatim - forms enforced those four values,
# so they are everything the column could legitimately hold.
SERVICE_TO_ITEM_TYPE = {
    "blouse": "blouse",
    "saree_alteration": "other",
    "bridal": "other",
    "other": "other",
}

# A line whose order carries something outside those four (only reachable by
# writing the ORM directly) takes the old field's own default.
UNKNOWN_SERVICE_TYPE = "other"


def backfill_item_types(apps, schema_editor):
    """Copy each order's old service_type down onto its lines.

    Four statements rather than a Python loop: the mapping is fixed and
    the database can apply it set-wise. The trailing exclude catches
    values outside the choices so no line is left on the AddField default
    of Blouse by accident.
    """
    OrderItem = apps.get_model("orders", "OrderItem")
    for service_type, item_type in SERVICE_TO_ITEM_TYPE.items():
        OrderItem.objects.filter(order__service_type=service_type).update(item_type=item_type)
    OrderItem.objects.exclude(
        order__service_type__in=list(SERVICE_TO_ITEM_TYPE)
    ).update(item_type=UNKNOWN_SERVICE_TYPE)


def restore_service_types(apps, schema_editor):
    """Reverse of the backfill: the first line decides the order's type.

    Runs while service_type exists again (RemoveField reverses first) and
    item_type is still present, so the meaning saved forward can be read
    back. Lines carry only the new vocabulary, so chudi and pattu pavadai
    fold to "other" - the exact mirror of what forward did to bridal.
    """
    Order = apps.get_model("orders", "Order")
    OrderItem = apps.get_model("orders", "OrderItem")
    legacy = {
        "blouse": "blouse",
        "chudi": "other",
        "pattu_pavadai": "other",
        "other": "other",
    }
    first_by_order = {}
    for order_id, item_type in OrderItem.objects.order_by("id").values_list(
        "order_id", "item_type"
    ):
        first_by_order.setdefault(order_id, item_type)
    for order_id, item_type in first_by_order.items():
        Order.objects.filter(id=order_id).update(
            service_type=legacy.get(item_type, UNKNOWN_SERVICE_TYPE)
        )


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0006_delivery_deliveryline"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="item_type",
            field=models.CharField(
                choices=[
                    ("blouse", "Blouse"),
                    ("chudi", "Chudi"),
                    ("pattu_pavadai", "Pattu pavadai"),
                    ("other", "Other tailoring"),
                ],
                default="blouse",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_item_types, restore_service_types),
        migrations.RemoveField(
            model_name="order",
            name="service_type",
        ),
        # Only "Other tailoring" lines carry a note now, so description
        # stops being required. No column change - blank is a Django-level
        # flag - but the model state has to say the same thing the model
        # does or makemigrations would flag drift.
        migrations.AlterField(
            model_name="orderitem",
            name="description",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
