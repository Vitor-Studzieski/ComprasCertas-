"""Connect approved supplier receipts to the immutable stock ledger."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_replenishment_rules_and_evidence"),
        ("purchasing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="receipt_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="stock_movements",
                to="purchasing.purchasereceiptline",
            ),
        ),
        migrations.AlterField(
            model_name="stockmovement",
            name="kind",
            field=models.CharField(
                choices=[
                    ("opening", "Abertura de estoque"),
                    ("sale", "Venda diária"),
                    ("sale_correction", "Correção de venda"),
                    ("receipt", "Recebimento de compra"),
                ],
                max_length=16,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="stockmovement",
            name="inv_movement_reference_valid",
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="opening",
                        opening_line__isnull=False,
                        daily_sale__isnull=True,
                        sale_correction__isnull=True,
                        receipt_line__isnull=True,
                    )
                    | models.Q(
                        kind="sale",
                        opening_line__isnull=True,
                        daily_sale__isnull=False,
                        sale_correction__isnull=True,
                        receipt_line__isnull=True,
                        quantity_delta__lte=0,
                    )
                    | (
                        models.Q(
                            kind="sale_correction",
                            opening_line__isnull=True,
                            daily_sale__isnull=True,
                            sale_correction__isnull=False,
                            receipt_line__isnull=True,
                        )
                        & ~models.Q(quantity_delta=0)
                    )
                    | models.Q(
                        kind="receipt",
                        opening_line__isnull=True,
                        daily_sale__isnull=True,
                        sale_correction__isnull=True,
                        receipt_line__isnull=False,
                        quantity_delta__gte=0,
                    )
                ),
                name="inv_movement_reference_valid",
            ),
        ),
    ]
