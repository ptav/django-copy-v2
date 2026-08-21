from user_agents import parse
from django.conf import settings
from django.shortcuts import redirect
from django.utils.translation import to_locale, get_language
from .models import Copy, PageVisit
from .utils import get_ip_address, get_client_country_code, ip_to_location, ip_to_organization
from .cookies import CookieConsentForm, cookie_consent

class CopyMiddleware:
    "Load copy for the current page"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request, *args, **kwargs):
        if not hasattr(request, 'copy'): # If copy is already loaded, don't reload it"
            url = request.path
            locale = to_locale(get_language())
            geo = get_client_country_code(request)
            draft = request.user.is_authenticated and 'draft' in request.GET

            request.copy = Copy.get_for_url(url, locale, geo, draft)

        return self.get_response(request, *args, **kwargs)


def copy_decorator(view_func):
    def _wrapped_view_func(request, *args, **kwargs):
        middleware = CopyMiddleware(view_func)
        return middleware(request, *args, **kwargs)
    return _wrapped_view_func


class TrackMiddleware:
    "Log of page visits."

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request, *args, **kwargs):
        response = self.get_response(request, *args, **kwargs)
        # tracking is logged after the view is run

        # A page visit is a top-level document response, not every resource the
        # browser requests while rendering that page. Explicitly tracked
        # redirects remain eligible regardless of their response type.
        if not self.is_page_request(request, response):
            return response

        # If request is unsuccesful, ignore it (default unless DJANGOCOPY_LOG_ALL_VISITS is True)
        if (not hasattr(settings, 'DJANGOCOPY_LOG_ALL_VISITS') or  \
            settings.DJANGOCOPY_LOG_ALL_VISITS == False) and \
            response.status_code != 200 and \
            not hasattr(response, 'djangocopy_redirect'):
            return response

        # Code to be executed for each request/response after the view is called.
        try:
            url = request.build_absolute_uri()
            ip = get_ip_address(request)
            status_code = response.status_code
            user = request.user if request.user.is_authenticated else None
            referrer = request.META.get('HTTP_REFERER', '')
            user_agent_string = request.META.get('HTTP_USER_AGENT', '')
            session = request.session.session_key
            device_info = self.get_device_info(user_agent_string)
            language = get_language()
            location = ip_to_location(ip)
            organization = ip_to_organization(ip)

            PageVisit.objects.create(
                url=url,
                ip=ip,
                user=user,
                status_code=status_code,
                referrer=referrer,
                user_agent=user_agent_string,
                session=session,
                device_info=device_info,
                language=language,
                country_code=location.get('country_code', ''),
                city=location.get('city', ''),
                organization=organization,
            )

        except Exception as err:
            # You might want to log the error here or send it to an error tracking service
            pass

        return response

    @staticmethod
    def is_page_request(request, response):
        if hasattr(response, 'djangocopy_redirect'):
            return True

        # Modern browsers identify the resource they are fetching. Restrict
        # tracking to the top-level document when that signal is available.
        fetch_destination = request.META.get('HTTP_SEC_FETCH_DEST', '').lower()
        if fetch_destination:
            return fetch_destination == 'document'

        # Fall back to the response media type for clients which do not send
        # Fetch Metadata headers. Exclude common legacy fragment requests too.
        if request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest':
            return False
        if request.META.get('HTTP_HX_REQUEST', '').lower() == 'true':
            return False

        content_type = response.get('Content-Type', '').partition(';')[0].strip().lower()
        return content_type in {'text/html', 'application/xhtml+xml'}

    @staticmethod
    def get_device_info(user_agent_string):
        try:
            ua = parse(user_agent_string)
            return ua.device.family
        except Exception as e:
            # Return 'Unknown' or any default value in case of an error
            return 'Unknown'


def track_decorator(view_func):
    def _wrapped_view_func(request, *args, **kwargs):
        middleware = TrackMiddleware(view_func)
        return middleware(request, *args, **kwargs)
    return _wrapped_view_func


class CookieConsentMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request, *args, **kwargs):
        if not cookie_consent(request):
            # Adds cookie consent form to request context
            request.cookie_consent_form = CookieConsentForm()

        return self.get_response(request, *args, **kwargs)

    """
    def __call__(self, request):
        cookie_consent = request.session.get('cookie_consent')
        if cookie_consent is None and not request.path.endswith('/__cookie_consent__/'):
            return redirect('cookie_consent')
        response = self.get_response(request)
        return response
    """
