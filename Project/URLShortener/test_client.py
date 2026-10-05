"""
test_client.py — public end-to-end smoke tests
Run: python test_client.py

This script uses Flask's local test client, so candidates do not need to start a
separate server process.
"""

import importlib
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from db import reset_connections_for_tests

PASS = []
FAIL = []


def record(name, ok, details=""):
    if ok:
        PASS.append(name)
        print(f"PASS: {name}")
    else:
        FAIL.append(name)
        print(f"FAIL: {name} {details}")


def json_body(response):
    try:
        return response.get_json()
    except Exception:
        return None


def expect_json_error(name, response, status):
    data = json_body(response)
    ok = response.status_code == status and isinstance(data, dict) and set(data) == {"error"}
    details = {
        "status": response.status_code,
        "body": data,
        "raw": response.get_data(as_text=True),
    }
    record(name, ok, details)


def main():
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()

    os.environ["URL_SHORTENER_DB_PATH"] = tmp.name

    try:
        reset_connections_for_tests()

        import app

        app_module = importlib.reload(app)
        flask_app = app_module.app
        client = flask_app.test_client()

        print("\nA. Create links")

        response = client.post("/links/", json={"long_url": "https://example.com/a"})
        data = json_body(response)
        record(
            "POST /links auto code",
            response.status_code == 201
            and isinstance(data, dict)
            and all(
                key in data
                for key in [
                    "short_code",
                    "short_url",
                    "long_url",
                    "owner_token",
                    "hit_count",
                    "is_expired",
                ]
            )
            and data["long_url"] == "https://example.com/a"
            and data["hit_count"] == 0
            and data["is_expired"] is False
            and data["short_code"] in data["short_url"],
            data,
        )

        auto_code = data["short_code"] if isinstance(data, dict) and "short_code" in data else "missing"

        response = client.post(
            "/links",
            json={
                "long_url": "https://example.com/custom",
                "custom_code": "my-link_99",
                "ttl_seconds": 2,
            },
        )
        custom = json_body(response)
        record(
            "POST /links custom code with ttl",
            response.status_code == 201
            and isinstance(custom, dict)
            and custom.get("short_code") == "my-link_99"
            and custom.get("expires_at") is not None
            and isinstance(custom.get("owner_token"), str)
            and custom["owner_token"].startswith("tok_"),
            custom,
        )

        custom_token = (
            custom["owner_token"]
            if isinstance(custom, dict) and "owner_token" in custom
            else "missing-token"
        )

        print("\nB. Redirect and hit count")

        response = client.get(f"/{auto_code}", follow_redirects=False)
        record(
            "GET /<short_code> redirects with 302",
            response.status_code == 302
            and response.headers.get("Location") == "https://example.com/a",
            {
                "status": response.status_code,
                "location": response.headers.get("Location"),
                "body": response.get_data(as_text=True),
            },
        )

        response = client.get(f"/links/{auto_code}")
        info = json_body(response)
        record(
            "GET metadata shows hit_count 1",
            response.status_code == 200
            and isinstance(info, dict)
            and info.get("hit_count") == 1
            and "owner_token" not in info,
            info,
        )

        print("\nC. Concurrent redirect count")

        def hit_once():
            with flask_app.test_client() as c:
                return c.get(f"/{auto_code}", follow_redirects=False).status_code

        with ThreadPoolExecutor(max_workers=20) as pool:
            statuses = list(pool.map(lambda _: hit_once(), range(50)))

        response = client.get(f"/links/{auto_code}")
        info = json_body(response)
        record(
            "50 concurrent redirects all counted",
            statuses.count(302) == 50
            and response.status_code == 200
            and isinstance(info, dict)
            and info.get("hit_count") == 51,
            {
                "statuses": statuses,
                "metadata": info,
            },
        )

        print("\nD. Expiration")

        time.sleep(2.2)

        response = client.get("/my-link_99", follow_redirects=False)
        expect_json_error("expired redirect returns 410", response, 410)

        response = client.get("/links/my-link_99")
        data = json_body(response)
        record(
            "expired metadata remains readable",
            response.status_code == 200
            and isinstance(data, dict)
            and data.get("is_expired") is True,
            data,
        )

        print("\nE. Delete")

        expect_json_error(
            "delete missing owner_token returns 400",
            client.delete("/links/my-link_99", json={}),
            400,
        )
        expect_json_error(
            "delete wrong owner_token returns 403",
            client.delete("/links/my-link_99", json={"owner_token": "wrong"}),
            403,
        )

        response = client.delete("/links/my-link_99", json={"owner_token": custom_token})
        data = json_body(response)
        record(
            "delete correct owner_token returns 200",
            response.status_code == 200 and data == {"deleted": "my-link_99"},
            data,
        )

        expect_json_error(
            "deleted link returns 404",
            client.get("/links/my-link_99/"),
            404,
        )

        print("\nF. Error response format")

        expect_json_error(
            "missing JSON body returns 400",
            client.post("/links", data="not-json", content_type="text/plain"),
            400,
        )
        expect_json_error(
            "malformed JSON body returns 400",
            client.post("/links", data="{bad", content_type="application/json"),
            400,
        )
        expect_json_error(
            "non-object JSON body returns 400",
            client.post("/links", json=["not", "an", "object"]),
            400,
        )
        expect_json_error(
            "unsupported method returns JSON 405",
            client.put("/links", json={"long_url": "https://example.com"}),
            405,
        )
        expect_json_error(
            "missing long_url returns 400",
            client.post("/links", json={}),
            400,
        )
        expect_json_error(
            "invalid URL returns 400",
            client.post("/links", json={"long_url": "ftp://bad"}),
            400,
        )
        expect_json_error(
            "invalid custom_code returns 400",
            client.post(
                "/links",
                json={
                    "long_url": "https://example.com",
                    "custom_code": "ab",
                },
            ),
            400,
        )
        expect_json_error(
            "invalid ttl returns 400",
            client.post(
                "/links",
                json={
                    "long_url": "https://example.com",
                    "ttl_seconds": True,
                },
            ),
            400,
        )
        expect_json_error(
            "duplicate custom_code returns 409",
            client.post(
                "/links",
                json={
                    "long_url": "https://other.example",
                    "custom_code": auto_code,
                },
            ),
            409,
        )
        expect_json_error(
            "missing redirect code returns 404",
            client.get("/missing-code"),
            404,
        )

        print("\nG. Stats")

        response = client.get("/stats")
        data = json_body(response)
        record(
            "GET /stats shape",
            response.status_code == 200
            and isinstance(data, dict)
            and set(data) == {"total_links", "total_hits", "top_5_links"}
            and isinstance(data["total_links"], int)
            and isinstance(data["total_hits"], int)
            and isinstance(data["top_5_links"], list),
            data,
        )

        print("\n" + "=" * 60)
        print(f"Results: {len(PASS)} passed, {len(FAIL)} failed")

        if FAIL:
            print("Failed checks:")
            for name in FAIL:
                print(f"  - {name}")

        print("=" * 60)

        sys.exit(0 if not FAIL else 1)

    finally:
        reset_connections_for_tests()
        os.environ.pop("URL_SHORTENER_DB_PATH", None)

        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


if __name__ == "__main__":
    main()