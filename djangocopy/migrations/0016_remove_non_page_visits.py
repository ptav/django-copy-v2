from urllib.parse import urlsplit

from django.conf import settings
from django.db import migrations


BATCH_SIZE = 1000
NON_PAGE_PREFIXES = ('/api/', '/media/', '/static/')
NON_PAGE_SUFFIXES = (
    '.avif',
    '.bmp',
    '.css',
    '.eot',
    '.gif',
    '.ico',
    '.jpeg',
    '.jpg',
    '.js',
    '.mjs',
    '.map',
    '.mp3',
    '.mp4',
    '.ogg',
    '.otf',
    '.png',
    '.svg',
    '.ttf',
    '.wav',
    '.webm',
    '.webp',
    '.woff',
    '.woff2',
)


def _path_prefix(url):
    path = urlsplit(url or '').path
    if not path or path == '/':
        return None
    return f'/{path.strip("/").lower()}/'


def _non_page_prefixes():
    prefixes = set(NON_PAGE_PREFIXES)
    for setting_name in ('STATIC_URL', 'MEDIA_URL'):
        prefix = _path_prefix(getattr(settings, setting_name, ''))
        if prefix:
            prefixes.add(prefix)
    return tuple(prefixes)


def _is_non_page_visit(url, prefixes):
    path = urlsplit(url or '').path.lower()
    normalized_path = f'/{path.lstrip("/")}'
    if any(
        normalized_path == prefix.rstrip('/') or normalized_path.startswith(prefix)
        for prefix in prefixes
    ):
        return True
    return normalized_path.endswith(NON_PAGE_SUFFIXES)


def remove_non_page_visits(apps, schema_editor):
    PageVisit = apps.get_model('djangocopy', 'PageVisit')
    database_alias = schema_editor.connection.alias
    prefixes = _non_page_prefixes()
    last_pk = 0

    while True:
        visits = list(
            PageVisit.objects.using(database_alias)
            .filter(pk__gt=last_pk)
            .order_by('pk')
            .values_list('pk', 'url')[:BATCH_SIZE]
        )
        if not visits:
            break

        stale_ids = [
            visit_id
            for visit_id, url in visits
            if _is_non_page_visit(url, prefixes)
        ]
        if stale_ids:
            PageVisit.objects.using(database_alias).filter(pk__in=stale_ids).delete()

        last_pk = visits[-1][0]


class Migration(migrations.Migration):

    dependencies = [
        ('djangocopy', '0015_pagevisit_country_code_pagevisit_city_and_more'),
    ]

    operations = [
        migrations.RunPython(remove_non_page_visits, migrations.RunPython.noop),
    ]
