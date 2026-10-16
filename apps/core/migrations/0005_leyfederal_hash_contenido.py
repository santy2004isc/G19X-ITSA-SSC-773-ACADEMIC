from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_leyfederal_fecha_modificacion_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='leyfederal',
            name='hash_contenido',
            field=models.CharField(
                blank=True,
                max_length=64,
                null=True,
                unique=True,
                verbose_name='Hash SHA-256 del texto extraído',
            ),
        ),
    ]
