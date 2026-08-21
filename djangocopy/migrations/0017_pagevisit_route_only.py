from urllib.parse import urlsplit

from django.db import migrations, models


BATCH_SIZE = 1000


def _route_from_url(url):
    try:
        route = urlsplit(url or '').path
    except ValueError:
        route = ''

    if not route:
        return '/'
    return route if route.startswith('/') else f'/{route}'


def convert_visit_urls_to_routes(apps, schema_editor):
    PageVisit = apps.get_model('djangocopy', 'PageVisit')
    database_alias = schema_editor.connection.alias
    last_pk = 0

    while True:
        visits = list(
            PageVisit.objects.using(database_alias)
            .filter(pk__gt=last_pk)
            .order_by('pk')[:BATCH_SIZE]
        )
        if not visits:
            break

        for visit in visits:
            visit.route = _route_from_url(visit.route)
        PageVisit.objects.using(database_alias).bulk_update(
            visits,
            ['route'],
            batch_size=BATCH_SIZE,
        )
        last_pk = visits[-1].pk


class Migration(migrations.Migration):

    dependencies = [
        ('djangocopy', '0016_remove_non_page_visits'),
    ]

    operations = [
        migrations.RenameField(
            model_name='pagevisit',
            old_name='url',
            new_name='route',
        ),
        migrations.AlterField(
            model_name='pagevisit',
            name='route',
            field=models.CharField(
                db_index=True,
                help_text='Requested site path, without the hostname or query string.',
                max_length=2048,
            ),
        ),
        migrations.RunPython(
            convert_visit_urls_to_routes,
            migrations.RunPython.noop,
        ),
    ]
