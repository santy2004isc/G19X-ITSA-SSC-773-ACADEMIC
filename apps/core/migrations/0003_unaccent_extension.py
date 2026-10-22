from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('core', '0002_fragmentonormativo_articulo')]

    operations = [UnaccentExtension()]
