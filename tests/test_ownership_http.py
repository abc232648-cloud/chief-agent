import json
import urllib.error
import urllib.request

import pytest

from identity.service import IdentityService
from tests.checkpoint_f_fixture import PASSWORD


def request_json(url, path, *, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(request) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_owner_can_create_and_read_multi_company_catalog_over_http(dashboard):
    status, created = request_json(dashboard.url, "/api/ownership", method="POST", body={
        "event_id": "http-org-yellow",
        "operation": "create_organization",
        "organization_id": "org-yellow",
        "name": "Yellow Farm Ltd",
    })
    assert status == 200
    assert created["status"] == "RECORDED"

    request_json(dashboard.url, "/api/ownership", method="POST", body={
        "event_id": "http-farm-lagos",
        "operation": "create_farm",
        "farm_id": "farm-lagos",
        "organization_id": "org-yellow",
        "name": "Lagos Farm",
    })
    status, catalog = request_json(dashboard.url, "/api/ownership")
    assert status == 200
    assert catalog["organization_count"] == 1
    assert catalog["farm_count"] == 1
    assert catalog["organizations"][0]["organization_id"] == "org-yellow"
    assert catalog["organizations"][0]["farms"][0]["farm_id"] == "farm-lagos"


def test_ownership_post_still_requires_same_origin_csrf(dashboard):
    request = urllib.request.Request(
        dashboard.url + "/api/ownership",
        data=json.dumps({
            "event_id": "no-csrf",
            "operation": "create_organization",
            "organization_id": "org-no-csrf",
            "name": "No CSRF Ltd",
        }).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Cookie": "chief_session=" + dashboard.credentials["raw"],
            "Origin": dashboard.url,
            "X-Chief-CSRF": "incorrect",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(request)
    assert error.value.code == 403


def test_manager_cannot_discover_other_company_catalog_over_http(dashboard):
    service = IdentityService(dashboard.store)
    service.create_user(
        dashboard.credentials["principal"],
        "ownership-http-manager",
        PASSWORD,
        "Manager",
        ("farming",),
    )
    raw, principal = service.login("ownership-http-manager", PASSWORD)
    dashboard.credentials.update(raw=raw, principal=principal, csrf=service.csrf(raw))

    with pytest.raises(urllib.error.HTTPError) as error:
        request_json(dashboard.url, "/api/ownership")
    assert error.value.code == 403
