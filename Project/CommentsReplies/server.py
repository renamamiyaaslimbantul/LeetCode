"""
Comments & Replies
==================

The HTTP server is already implemented. Do not modify this file.
Implement CommentService in comment_service.py.
"""

from aiohttp import web

from comment_service import CommentService, NotFoundError, ValidationError
from post_store import PostStore

service = CommentService(PostStore())


async def parse_json(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise ValidationError("request body must be valid JSON")

    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")

    return data


async def add_comment(request: web.Request) -> web.Response:
    try:
        post_id = int(request.match_info["post_id"])
        data = await parse_json(request)
        result = service.add_comment(post_id, data.get("user_id"), data.get("content"))
        return web.json_response(result, status=201)
    except ValidationError as e:
        return web.json_response({"error": str(e)}, status=400)
    except NotFoundError as e:
        return web.json_response({"error": str(e)}, status=404)


async def add_reply(request: web.Request) -> web.Response:
    try:
        post_id = int(request.match_info["post_id"])
        comment_id = int(request.match_info["comment_id"])
        data = await parse_json(request)
        result = service.add_reply(post_id, comment_id, data.get("user_id"), data.get("content"))
        return web.json_response(result, status=201)
    except ValidationError as e:
        return web.json_response({"error": str(e)}, status=400)
    except NotFoundError as e:
        return web.json_response({"error": str(e)}, status=404)


async def get_comments(request: web.Request) -> web.Response:
    try:
        post_id = int(request.match_info["post_id"])
        return web.json_response(service.get_comments(post_id))
    except NotFoundError as e:
        return web.json_response({"error": str(e)}, status=404)


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


app = web.Application()
app.router.add_get("/health", health)
app.router.add_post("/post/{post_id}/comment/add", add_comment)
app.router.add_post("/post/{post_id}/comment/{comment_id}/reply", add_reply)
app.router.add_get("/post/{post_id}/comments", get_comments)

if __name__ == "__main__":
    print("Comments & Replies server listening on http://localhost:8080")
    web.run_app(app, port=8080)
