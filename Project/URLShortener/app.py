"""
app.py — candidate implementation file

Complete the Flask routes for the URL shortener API. The service logic belongs
in shortener.py; the route layer should parse requests, call URLShortener, and
map service outcomes to the required HTTP responses.
"""

from flask import Flask, jsonify, redirect, request

from shortener import (
    CodeGenerationError,
    ConflictError,
    ExpiredLinkError,
    NotFoundError,
    PermissionDeniedError,
    URLShortener,
    ValidationError,
)

app = Flask(__name__)
app.url_map.strict_slashes = False
svc = URLShortener()


def error(message: str, status: int):
    return jsonify({"error": message}), status


def short_url_for(short_code: str) -> str:
    return request.host_url.rstrip("/") + "/" + short_code


def get_json_object():
    """
    Return the request body as a dict.

    Raises ValidationError when the body is missing, malformed, or not a JSON
    object.
    """
    if not request.is_json:
        raise ValidationError("request body must be JSON")
    try:
        body = request.get_json(silent=False)
    except Exception:
        raise ValidationError("request body must be valid JSON")
    if not isinstance(body, dict):
        raise ValidationError("request body must be a JSON object")
    return body


@app.post("/links")
def create_link():
    """
    Create a short link.

    JSON body:
      long_url: required string starting with http:// or https://
      custom_code: optional [A-Za-z0-9_-]{3,32}
      ttl_seconds: optional positive number

    Success: 201 with link metadata, short_url, and owner_token.
    """
    try:
        body = get_json_object()
        link = svc.create(
            body.get("long_url"),
            custom_code=body.get("custom_code"),
            ttl_seconds=body.get("ttl_seconds"),
        )
    except ValidationError as exc:
        return error(str(exc), 400)
    except ConflictError as exc:
        return error(str(exc), 409)
    except CodeGenerationError as exc:
        return error(str(exc), 500)

    payload = link.to_dict()
    payload["short_url"] = short_url_for(link.short_code)
    payload["owner_token"] = link.owner_token
    return jsonify(payload), 201


@app.get("/<short_code>")
def redirect_link(short_code: str):
    """
    Redirect to the original URL.

    Success: 302 with Location header.
    Missing code: 404.
    Expired code: 410.
    A successful redirect must atomically increment hit_count exactly once.
    """
    try:
        long_url = svc.resolve_redirect(short_code)
    except NotFoundError as exc:
        return error(str(exc), 404)
    except ExpiredLinkError as exc:
        return error(str(exc), 410)

    return redirect(long_url, code=302)


@app.get("/links/<short_code>")
def get_link(short_code: str):
    """Return metadata for a short link, or 404 when it does not exist."""
    try:
        link = svc.get(short_code)
    except ValidationError as exc:
        return error(str(exc), 400)

    if link is None:
        return error("short code not found", 404)

    return jsonify(link.to_dict()), 200


@app.delete("/links/<short_code>")
def delete_link(short_code: str):
    """
    Delete a short link.

    JSON body must contain owner_token.
    Missing token: 400.
    Wrong token: 403.
    Missing code: 404.
    Success: 200 with {"deleted": short_code}.
    """
    try:
        body = get_json_object()
    except ValidationError as exc:
        return error(str(exc), 400)

    try:
        deleted = svc.delete(short_code, body.get("owner_token"))
    except ValidationError as exc:
        return error(str(exc), 400)
    except PermissionDeniedError as exc:
        return error(str(exc), 403)

    if not deleted:
        return error("short code not found", 404)

    return jsonify({"deleted": short_code}), 200


@app.get("/stats")
def stats():
    """Return aggregate statistics across non-deleted links."""
    return jsonify(svc.get_stats()), 200


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(410)
@app.errorhandler(405)
def handle_http_error(exc):
    return error(getattr(exc, "description", "request failed"), exc.code)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True, threaded=True)
