from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import HttpResponse
from django.template import Context, Template as DjangoTemplate
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.safestring import SafeData

from .cookies import CookieConsentForm, cookie_consent, has_cookie_consent
from .middleware import CookieConsentMiddleware, CopyMiddleware, TrackMiddleware
from .models import Copy, Navbar, Page, PageVisit, Redirect, Template
from .templatetags.djangocopy import (
    faicon,
    list_to_2_column,
    list_to_3_column,
    list_to_4_column,
    numeric_range,
)
from .utils import choices_as_string, get_ip_address, html2text, ip_to_country_code
from .views import BasicView, static_page, tracked_redirect


__djangocopy_navbar__ = import_module(
    'djangocopy.templatetags.djangocopy-internal'
).__djangocopy_navbar__


def add_session(request):
    middleware = SessionMiddleware(lambda req: HttpResponse())
    middleware.process_request(request)
    request.session.save()
    return request


class CopyModelTests(TestCase):
    def make_copy(self, **overrides):
        values = {
            'fieldid': 'headline',
            'text': 'Hello',
            'format': Copy.FORMAT_PLAIN,
            'status': Copy.STATUS_PUBLISHED,
        }
        values.update(overrides)
        return Copy.objects.create(**values)

    def test_render_supports_all_content_formats(self):
        self.assertEqual(self.make_copy().render(), 'Hello')
        markdown = self.make_copy(fieldid='markdown', text='**Bold**', format=Copy.FORMAT_MARKDOWN)
        self.assertEqual(str(markdown.render()), '<p><strong>Bold</strong></p>')
        self.assertIsInstance(markdown.render(), SafeData)
        self.assertEqual(
            self.make_copy(fieldid='json', text='{"enabled": true}', format=Copy.FORMAT_JSON).render(),
            {'enabled': True},
        )
        html = self.make_copy(fieldid='html', text='<em>Safe</em>', format=Copy.FORMAT_SAFE_HTML)
        self.assertEqual(str(html.render()), '<em>Safe</em>')
        self.assertIsInstance(html.render(), SafeData)

    def test_invalid_json_fails_silently(self):
        item = self.make_copy(text='{invalid', format=Copy.FORMAT_JSON)
        with self.assertLogs(level='ERROR'):
            self.assertIsNone(item.render())

    def test_display_helpers(self):
        item = self.make_copy(text='x' * 90, locale='en_GB', geo='GB')
        self.assertEqual(item.status_as_string, 'Published')
        self.assertEqual(item.short_text(5), 'xxxxx...')
        self.assertIn('(en_GB,GB) PUBLISHED', str(item))

    def test_get_for_url_applies_specific_values_over_defaults(self):
        self.make_copy(text='default', url='', locale='', geo='')
        self.make_copy(text='british', url='/copy/example/', locale='en_GB', geo='GB')

        result = Copy.get_for_url('/copy/example/', 'en_GB', 'GB')

        self.assertEqual(result['headline'], 'british')

    def test_get_for_url_excludes_drafts_unless_requested(self):
        self.make_copy(text='published')
        self.make_copy(text='draft', status=Copy.STATUS_DRAFT)

        self.assertEqual(Copy.get_for_url('/copy/example/', 'en_GB', 'GB')['headline'], 'published')
        self.assertEqual(Copy.get_for_url('/copy/example/', 'en_GB', 'GB', draft=True)['headline'], 'draft')


class MiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch('djangocopy.middleware.get_client_country_code', return_value='GB')
    @patch('djangocopy.middleware.Copy.get_for_url', return_value={'headline': 'Hello'})
    def test_copy_middleware_populates_request(self, get_for_url, country):
        request = self.factory.get('/example/')
        request.user = AnonymousUser()
        response = CopyMiddleware(lambda req: HttpResponse(req.copy['headline']))(request)

        self.assertEqual(response.content, b'Hello')
        get_for_url.assert_called_once_with('/example/', 'en_US', 'GB', False)

    @patch('djangocopy.middleware.Copy.get_for_url')
    def test_copy_middleware_preserves_existing_copy(self, get_for_url):
        request = self.factory.get('/example/')
        request.user = AnonymousUser()
        request.copy = {'existing': True}

        CopyMiddleware(lambda req: HttpResponse())(request)

        get_for_url.assert_not_called()

    def test_tracking_records_successful_visit(self):
        request = add_session(self.factory.get(
            '/tracked/',
            REMOTE_ADDR='203.0.113.9',
            HTTP_SEC_FETCH_DEST='document',
            HTTP_USER_AGENT='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)',
            HTTP_REFERER='https://example.com/source',
        ))
        request.user = AnonymousUser()

        TrackMiddleware(lambda req: HttpResponse('ok'))(request)

        visit = PageVisit.objects.get()
        self.assertEqual(visit.ip, '203.0.113.9')
        self.assertEqual(visit.status_code, 200)
        self.assertEqual(visit.referrer, 'https://example.com/source')
        self.assertEqual(visit.device_info, 'iPhone')

    def test_tracking_ignores_static_and_api_responses(self):
        requests_and_responses = (
            ('/static/site.css', 'style', 'text/css'),
            ('/static/site.js', 'script', 'text/javascript'),
            ('/media/logo.png', 'image', 'image/png'),
            ('/api/status/', 'empty', 'application/json'),
        )

        for path, fetch_destination, content_type in requests_and_responses:
            request = add_session(self.factory.get(
                path,
                REMOTE_ADDR='203.0.113.9',
                HTTP_SEC_FETCH_DEST=fetch_destination,
            ))
            request.user = AnonymousUser()
            response = HttpResponse('content', content_type=content_type)

            TrackMiddleware(lambda req, response=response: response)(request)

        self.assertFalse(PageVisit.objects.exists())

    def test_tracking_ignores_html_fragment_requests(self):
        request = add_session(self.factory.get(
            '/fragment/',
            REMOTE_ADDR='203.0.113.9',
            HTTP_SEC_FETCH_DEST='empty',
        ))
        request.user = AnonymousUser()

        TrackMiddleware(lambda req: HttpResponse('<div>Fragment</div>'))(request)

        self.assertFalse(PageVisit.objects.exists())

    def test_tracking_uses_html_response_as_legacy_fallback(self):
        request = add_session(self.factory.get('/legacy/', REMOTE_ADDR='203.0.113.9'))
        request.user = AnonymousUser()

        TrackMiddleware(lambda req: HttpResponse('<html></html>'))(request)

        self.assertEqual(PageVisit.objects.count(), 1)

    def test_tracking_ignores_unsuccessful_visit_by_default(self):
        request = add_session(self.factory.get('/missing/', REMOTE_ADDR='203.0.113.9'))
        request.user = AnonymousUser()
        TrackMiddleware(lambda req: HttpResponse(status=404))(request)
        self.assertFalse(PageVisit.objects.exists())

    def test_tracking_records_tracked_redirects(self):
        Redirect.objects.create(slug='email-campaign', destination_url='/destination/')
        request = add_session(self.factory.get('/copy/r/email-campaign/', REMOTE_ADDR='203.0.113.9'))
        request.user = AnonymousUser()

        TrackMiddleware(lambda req: tracked_redirect(req, 'email-campaign'))(request)

        visit = PageVisit.objects.get()
        self.assertEqual(visit.status_code, 302)
        self.assertTrue(visit.url.endswith('/copy/r/email-campaign/'))

    def test_cookie_middleware_adds_form_only_without_consent(self):
        request = add_session(self.factory.get('/'))
        CookieConsentMiddleware(lambda req: HttpResponse())(request)
        self.assertIsInstance(request.cookie_consent_form, CookieConsentForm)

        consented = add_session(self.factory.get('/'))
        consented.session['cookie_consent'] = {'necessary': True}
        CookieConsentMiddleware(lambda req: HttpResponse())(consented)
        self.assertFalse(hasattr(consented, 'cookie_consent_form'))


class PageVisitCleanupMigrationTests(TestCase):
    @override_settings(STATIC_URL='/assets/', MEDIA_URL='/uploads/')
    def test_cleanup_removes_non_page_visits_and_preserves_pages(self):
        cleanup_migration = import_module(
            'djangocopy.migrations.0016_remove_non_page_visits'
        )

        removed_urls = (
            'https://example.com/static/site.css',
            'https://example.com/assets/app.js?v=1',
            'https://example.com/uploads/photo.jpg',
            'https://example.com/api/status/',
            'https://example.com/favicon.ico',
        )
        preserved_urls = (
            'https://example.com/',
            'https://example.com/about/',
            'https://example.com/reports.html',
        )

        for url in removed_urls:
            PageVisit.objects.create(
                url=url,
                status_code=200,
                ip='203.0.113.9',
                referrer='',
                user_agent='',
                device_info='',
                language='en',
            )
        for url in preserved_urls:
            PageVisit.objects.create(
                url=url,
                status_code=200,
                ip='203.0.113.9',
                referrer='',
                user_agent='',
                device_info='',
                language='en',
            )

        migration_apps = SimpleNamespace(get_model=lambda app, model: PageVisit)
        schema_editor = SimpleNamespace(connection=connection)
        cleanup_migration.remove_non_page_visits(migration_apps, schema_editor)

        self.assertCountEqual(
            PageVisit.objects.values_list('url', flat=True),
            preserved_urls,
        )


class CookieConsentTests(TestCase):
    def test_consent_helpers(self):
        request = add_session(RequestFactory().get('/'))
        self.assertFalse(cookie_consent(request))
        self.assertFalse(has_cookie_consent(request))

        request.session['cookie_consent'] = {
            'necessary': True,
            'preferences': True,
            'functional': False,
            '__timestamp__': 'ignored',
        }
        self.assertCountEqual(cookie_consent(request), ['necessary', 'preferences'])
        self.assertTrue(has_cookie_consent(request))
        self.assertTrue(has_cookie_consent(request, 'preferences'))
        self.assertFalse(has_cookie_consent(request, 'marketing'))

    def test_cookie_consent_view_stores_choices_and_redirects(self):
        session = self.client.session
        session['next'] = '/after-consent/'
        session.save()

        response = self.client.post(reverse('cookie_consent'), {
            'necessary': 'on',
            'preferences': 'on',
        })

        self.assertRedirects(response, '/after-consent/', fetch_redirect_response=False)
        consent = self.client.session['cookie_consent']
        self.assertTrue(consent['necessary'])
        self.assertTrue(consent['preferences'])
        self.assertFalse(consent['functional'])
        self.assertIn('__timestamp__', consent)

    def test_basic_view_renders_bound_form_errors(self):
        class RequiredForm(forms.Form):
            value = forms.CharField()

        class RequiredView(BasicView):
            FORM_CLASS = RequiredForm
            FORM_TEMPLATE = 'form.html'

        request = RequestFactory().post('/', {'value': ''})
        with patch('djangocopy.views.render', return_value=HttpResponse('invalid')) as render:
            response = RequiredView()(request)

        self.assertEqual(response.content, b'invalid')
        bound_form = render.call_args.args[2]['form']
        self.assertTrue(bound_form.is_bound)
        self.assertIn('value', bound_form.errors)


class UtilityAndTemplateTagTests(TestCase):
    def test_utility_functions(self):
        self.assertEqual(choices_as_string((('a', 'Alpha'),), 'a'), 'Alpha')
        self.assertEqual(choices_as_string((('a', 'Alpha'),), 'missing'), '--')
        self.assertIn('Hello', html2text('<h1>Hello</h1><p>World</p>'))
        self.assertEqual(ip_to_country_code('127.0.0.1'), 'GB')

    def test_ip_address_honors_proxy_headers(self):
        factory = RequestFactory()
        self.assertEqual(get_ip_address(factory.get('/', HTTP_X_FORWARDED_FOR='198.51.100.1, 10.0.0.1')), '198.51.100.1')
        self.assertEqual(get_ip_address(factory.get('/', HTTP_FORWARDED='for=198.51.100.2;proto=https')), '198.51.100.2')
        self.assertEqual(get_ip_address(factory.get('/', REMOTE_ADDR='203.0.113.1')), '203.0.113.1')

    def test_collection_filters_split_items_without_loss(self):
        items = list(range(7))
        for splitter, columns in ((list_to_2_column, 2), (list_to_3_column, 3), (list_to_4_column, 4)):
            result = splitter(items)
            self.assertEqual(len(result), columns)
            self.assertEqual([item for column in result for item in column], items)
        self.assertEqual(list(numeric_range(3)), [0, 1, 2])

    def test_icon_and_uuid_tags_render(self):
        self.assertIn('fa-user', str(faicon('user')))
        rendered = DjangoTemplate('{% load djangocopy %}{% uuid token %}{{ token }}').render(Context())
        self.assertRegex(rendered, r'^[0-9a-f-]{36}$')


class NavbarTests(TestCase):
    def test_anonymous_navbars_are_combined_by_priority(self):
        Navbar.objects.create(label='low', anonymous=True, z_index=1, elements=[{'label': 'Low'}])
        Navbar.objects.create(label='high', anonymous=True, z_index=5, elements=[{'label': 'High'}])
        request = RequestFactory().get('/')
        request.user = AnonymousUser()

        result = __djangocopy_navbar__(SimpleNamespace(request=request))

        self.assertEqual([item['label'] for item in result['navbar_items']], ['High', 'Low'])

    def test_authenticated_user_receives_group_navbar(self):
        group = Group.objects.create(name='editors')
        user = get_user_model().objects.create_user('editor')
        user.groups.add(group)
        navbar = Navbar.objects.create(label='editors', elements=[{'label': 'Edit'}])
        navbar.groups.add(group)
        request = RequestFactory().get('/')
        request.user = user

        result = __djangocopy_navbar__(SimpleNamespace(request=request))

        self.assertEqual(result['navbar_items'], [{'label': 'Edit'}])


class ViewTests(TestCase):
    def setUp(self):
        self.template = Template.objects.create(label='sample', template='sample.html')
        self.factory = RequestFactory()

    def test_index_redirects_staff_to_admin(self):
        user = get_user_model().objects.create_user('staff', is_staff=True)
        self.client.force_login(user)
        self.assertRedirects(self.client.get('/'), reverse('admin:index'), fetch_redirect_response=False)

    def test_public_static_page_renders_metadata_and_content(self):
        Page.objects.create(slug='public', template=self.template, title='Public title')
        response = self.client.get(reverse('static', args=['public']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Public title')

    def test_tracked_redirect_makes_a_relative_site_path_root_relative(self):
        Redirect.objects.create(slug='linkedin', destination_url='campaign-page/')

        response = self.client.get(reverse('tracked_redirect', args=['linkedin']))

        self.assertRedirects(response, '/campaign-page/', fetch_redirect_response=False)

    def test_tracked_redirect_preserves_a_root_relative_site_path(self):
        Redirect.objects.create(slug='newsletter', destination_url='/campaign-page/')

        response = self.client.get(reverse('tracked_redirect', args=['newsletter']))

        self.assertRedirects(response, '/campaign-page/', fetch_redirect_response=False)

    def test_tracked_redirect_can_target_an_absolute_url(self):
        Redirect.objects.create(slug='external', destination_url='https://example.com/campaign')

        response = self.client.get(reverse('tracked_redirect', args=['external']))

        self.assertRedirects(response, 'https://example.com/campaign', fetch_redirect_response=False)

    def test_authenticated_page_rejects_anonymous_user(self):
        Page.objects.create(slug='private', template=self.template, authenticated=True)
        request = self.factory.get('/copy/private/')
        request.user = AnonymousUser()
        with self.assertRaises(PermissionDenied):
            static_page(request, 'private')

    def test_group_page_allows_member_and_rejects_non_member(self):
        allowed_group = Group.objects.create(name='allowed')
        page = Page.objects.create(slug='group-page', template=self.template)
        page.groups.add(allowed_group)
        member = get_user_model().objects.create_user('member')
        member.groups.add(allowed_group)
        outsider = get_user_model().objects.create_user('outsider')

        member_request = self.factory.get('/copy/group-page/')
        member_request.user = member
        self.assertEqual(static_page(member_request, 'group-page').status_code, 200)

        outsider_request = self.factory.get('/copy/group-page/')
        outsider_request.user = outsider
        with self.assertRaises(PermissionDenied):
            static_page(outsider_request, 'group-page')
