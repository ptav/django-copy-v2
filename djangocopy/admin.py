import re
from datetime import timedelta
from urllib.parse import urlsplit

from django import forms
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.contrib import admin
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.template.response import TemplateResponse

from django_summernote.widgets import SummernoteWidget

from .models import Template, Image, Page, Copy, Navbar, PageVisit, Redirect



ELEMENTS_HELPTEXT = \
"""
Simple entry: { 'label':"Home", url:"/" }
Add image: { 'label':"Logout", url:"/accounts/logout", img:"/avatar.jpg" }
Add FA icon: { 'label':"Login", url:"/accounts/login", faicon:"fa-signin" }
Dropdown (and example of divider):  
    {
        "label":" ",
        "img":"/game/useravatar",
        "dropdown":[
            { "label":"Profile", "url":"/game/profile", "divider":1 },
            { "label":"Sign-out", "url":"/accounts/logout" }
        ]
    }

"""

@admin.register(Navbar)
class NavbarAdmin(admin.ModelAdmin):
    model = Navbar

    list_display = ('label', 'get_groups', 'anonymous', 'z_index', )
    autocomplete_fields = ('groups', )

    help_texts = {
        'elements': ELEMENTS_HELPTEXT,
    }

    def get_groups(self, obj):
        txt = ""
        for grp in obj.groups.all():
            txt += ', ' + grp.name
        return txt[2:] if len(txt) > 2 else ""
    get_groups.short_description = 'Groups'


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    model = Page

    ordering= ('slug', )
    list_display = ('slug', 'template', )
    search_fields = ('slug', )

    fieldsets = (
        (None, {
            'fields': ('slug', 'template', 'authenticated', 'groups')
        }),
        ('SEO', {
            #'classes': ('collapse', ),
            'fields': ('title', 'description', 'keywords', ),
        }),
    )



def publish_drafts(modeladmin, request, queryset):
    for obj in queryset:
        pub, created = Copy.objects.get_or_create(
            url=obj.url,
            fieldid=obj.fieldid,
            locale=obj.locale,
            geo=obj.geo,
            status=Copy.STATUS_PUBLISHED,
        )

        pub.text = obj.text
        pub.format = obj.format
        pub.save()

        obj.delete()

    return redirect(request.path)



class CopyAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(CopyAdminForm, self).__init__(*args, **kwargs)

        # Show Summernote for new instances or when format is FORMAT_SAFE_HTML
        if not self.instance.pk or self.instance.format == Copy.FORMAT_SAFE_HTML:
            self.fields['text'].widget = SummernoteWidget()

    class Meta:
        model = Copy
        fields = '__all__'



@admin.register(Copy)
class CopyAdmin(admin.ModelAdmin):
    model = Copy
    form = CopyAdminForm
    actions = (publish_drafts, )

    ordering= ('url', 'fieldid', 'locale', 'geo', '-status')
    list_display = ('fieldid', 'url', 'locale', 'geo', 'short_text', 'format', 'status')
    list_filter = ('status', 'locale', 'geo', 'url', 'fieldid')
    search_fields = ('text', 'locale', 'geo', 'fieldid', 'url')


@admin.register(PageVisit)
class PageVisitAdmin(admin.ModelAdmin):
    route_autocomplete_limit = 20

    list_display = ('time', 'route', 'status_code', 'user', 'ip', 'city', 'country_code', 'organization')
    list_filter = ('status_code', 'country_code')
    search_fields = ('route', 'referrer', 'user_agent', 'city', 'organization')
    change_list_template = 'admin/djangocopy/pagevisit/change_list.html'

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.get_fields()]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        custom_urls = [
            path('dashboard/', self.admin_site.admin_view(self.dashboard_view), name='djangocopy_pagevisit_dashboard'),
            path(
                'dashboard/routes/',
                self.admin_site.admin_view(self.route_autocomplete_view),
                name='djangocopy_pagevisit_route_autocomplete',
            ),
        ]
        return custom_urls + super().get_urls()

    @staticmethod
    def normalize_route(route):
        """Normalize a route filter to the path stored on PageVisit."""
        value = (route or '').strip()
        if not value:
            return ''

        try:
            route = urlsplit(value).path
        except ValueError:
            return ''

        if not route:
            return '/'
        return route if route.startswith('/') else f'/{route}'

    def route_autocomplete_view(self, request):
        """Return a small set of matching visited and published redirect routes."""
        term = request.GET.get('q', '').strip()[:255]
        term_lower = term.lower()
        candidates = {}

        redirects = Redirect.objects.all()
        if term:
            # A route search commonly ends in the redirect slug. Searching the
            # label and destination also makes named campaigns easy to find.
            slug_term = term.rstrip('/').rsplit('/', 1)[-1]
            redirects = redirects.filter(
                Q(slug__icontains=slug_term)
                | Q(label__icontains=term)
                | Q(destination_url__icontains=term)
            )

        for published_redirect in redirects.order_by('slug')[:self.route_autocomplete_limit * 2]:
            route = reverse('tracked_redirect', args=[published_redirect.slug])
            label = route
            if published_redirect.label:
                label += f' — {published_redirect.label}'
            label += f' → {published_redirect.destination_url}'
            candidates[route] = {'value': route, 'label': label}

        visited_routes = PageVisit.objects.all()
        if term:
            visited_routes = visited_routes.filter(route__icontains=term)
        visited_routes = (
            visited_routes.order_by('route')
            .values_list('route', flat=True)
            .distinct()[:self.route_autocomplete_limit * 5]
        )
        for route in visited_routes:
            if not route:
                continue
            candidates.setdefault(route, {'value': route, 'label': route})

        def sort_key(candidate):
            value = candidate['value'].lower()
            return (0 if value.startswith(term_lower) else 1, value)

        results = sorted(candidates.values(), key=sort_key)[:self.route_autocomplete_limit]
        return JsonResponse({'results': results})

    def dashboard_view(self, request):
        "Aggregate PageVisit data (traffic over time, top pages, locations, and organisations) for the visits dashboard"

        try:
            days = int(request.GET.get('days', 30))
        except ValueError:
            days = 30
        since = timezone.now() - timedelta(days=days)

        qs = PageVisit.objects.filter(time__gte=since)
        route = self.normalize_route(request.GET.get('route', ''))
        if route:
            qs = qs.filter(route=route)

        daily = list(
            qs.annotate(day=TruncDate('time'))
              .values('day')
              .annotate(count=Count('id'))
              .order_by('day')
        )
        top_pages = list(qs.values('route').annotate(count=Count('id')).order_by('-count')[:10])
        top_countries = list(
            qs.exclude(country_code='')
              .values('country_code')
              .annotate(count=Count('id'))
              .order_by('-count')[:10]
        )
        top_organizations = list(
            qs.exclude(organization='')
              .values('organization')
              .annotate(count=Count('id'))
              .order_by('-count')[:10]
        )
        top_devices = list(qs.values('device_info').annotate(count=Count('id')).order_by('-count')[:10])

        def with_pct(rows):
            top = max((row['count'] for row in rows), default=0)
            return [{**row, 'pct': (row['count'] * 100 // top) if top else 0} for row in rows]

        context = {
            **self.admin_site.each_context(request),
            'title': 'Page visit dashboard',
            'opts': self.model._meta,
            'days': days,
            'route': route,
            'route_autocomplete_url': reverse('admin:djangocopy_pagevisit_route_autocomplete'),
            'total_visits': qs.count(),
            'unique_ips': qs.values('ip').distinct().count(),
            'unique_sessions': qs.exclude(session__isnull=True).values('session').distinct().count(),
            'unlocated_visits': qs.filter(country_code='').count(),
            'daily': with_pct(daily),
            'top_pages': with_pct(top_pages),
            'top_countries': with_pct(top_countries),
            'top_organizations': with_pct(top_organizations),
            'top_devices': with_pct(top_devices),
        }
        return TemplateResponse(request, 'admin/djangocopy/pagevisit/dashboard.html', context)


@admin.register(Redirect)
class RedirectAdmin(admin.ModelAdmin):
    list_display = ('slug', 'destination_url', 'label')
    search_fields = ('slug', 'label', 'destination_url')


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    model = Template
    fields = ['label', 'template', 'get_copy_fields', 'get_src']
    readonly_fields = ['template', 'get_copy_fields', 'get_src']

    def get_copy_fields(self, obj):
        fields = re.findall(r'{{\s*[\w\.]+\s*}}', obj.content)
        return ', '.join(fields)
    get_copy_fields.short_description = 'Fields'

    def get_src(self, obj):
        # print self.template source. Need to open the file and render content
        src = obj.template.source
        return mark_safe(src)
    get_src.short_description = 'Source'

admin.site.register(Image)
