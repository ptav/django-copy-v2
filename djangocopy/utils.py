import os
import ipaddress
from html2text import html2text as h2t
from django.conf import settings
from django.contrib.gis.geoip2 import GeoIP2
from geoip2.database import Reader as ASNReader
from geoip2.errors import AddressNotFoundError


def choices_as_string(choices, param, default="--"):
    "Return the string representation of a choice field"
    return dict(choices).get(param, default)


def html2text(text):
    "Convert HTML to text"
    return h2t(text).replace('#','').replace('**','').replace('__','')


def get_ip_address(request):
    "Map request to external IP address resolving internal address if necessary"

    standardised_headers = {key.lower(): value for key, value in request.headers.items()}

    if "x-forwarded-for" in standardised_headers:
        return standardised_headers.get("x-forwarded-for").split(',')[0]
    elif "forwarded" in standardised_headers:
        # Header format: "Forwarded": "for=<for_ip>, for=<proxy_ip>;host=<host>;proto=https"
        # Extract the for=... component
        forwarded_for = next(
            component.lower() for component in request.headers.get("Forwarded").split(";")
            if component.lower().startswith("for")
        )
        # Get the first IP address in the redirect chain (original client)
        ip_address = forwarded_for.strip("for=").split(",")[0]

        return ip_address
    else:
        return request.META.get('REMOTE_ADDR')


def ip_to_country_code(addr, default_code='GB'):
    "Map request to client's country based on IP address"

    if not hasattr(settings, 'GEOIP_PATH') or addr == '127.0.0.1' or addr == 'localhost':
        return default_code

    g = GeoIP2(path = settings.GEOIP_PATH)
    return g.country(addr)['country_code']


def get_client_country_code(request):
    "Shorthand to request country code directly from request"
    return ip_to_country_code(get_ip_address(request))


def _is_local_address(addr):
    "True for loopback/private addresses, which GeoIP databases can't resolve"
    if addr in ('127.0.0.1', 'localhost', '::1'):
        return True
    try:
        ip = ipaddress.ip_address(addr)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return True


def ip_to_location(addr):
    "Map an IP address to a city-level location using GeoIP2's City database"

    if not hasattr(settings, 'GEOIP_PATH') or _is_local_address(addr):
        return {}

    g = GeoIP2(path=settings.GEOIP_PATH)
    try:
        city = g.city(addr)
    except Exception:
        return {}

    return {
        'country_code': city.get('country_code') or '',
        'city': city.get('city') or '',
    }


_asn_reader = None

def _get_asn_reader():
    "Lazily open (and cache) the GeoLite2 ASN database used to resolve IP -> organisation"
    global _asn_reader

    if _asn_reader is None and hasattr(settings, 'GEOIP_PATH'):
        db_name = getattr(settings, 'DJANGOCOPY_GEOIP_ASN_DB', 'GeoLite2-ASN.mmdb')
        path = os.path.join(str(settings.GEOIP_PATH), db_name)
        if os.path.exists(path):
            _asn_reader = ASNReader(path)

    return _asn_reader


def ip_to_organization(addr):
    "Map an IP address to the organisation (ISP/network operator) that owns it, via GeoIP2's ASN database"

    if _is_local_address(addr):
        return ''

    reader = _get_asn_reader()
    if reader is None:
        return ''

    try:
        return reader.asn(addr).autonomous_system_organization or ''
    except AddressNotFoundError:
        return ''
    except Exception:
        return ''
