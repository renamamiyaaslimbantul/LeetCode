import threading
import time


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


def _validate_text(value, field_name):
    """Return the trimmed field value, or raise ValidationError."""
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a non-empty string")
    trimmed = value.strip()
    if not trimmed:
        raise ValidationError(f"{field_name} must be a non-empty string")
    return trimmed


class CommentService:
    def __init__(self, post_store):
        self.post_store = post_store

        # Serializes every read-modify-write sequence and every snapshot read.
        # A single lock is enough for this in-memory exercise and keeps the
        # ID counter and the comment/reply structures mutually consistent.
        self._lock = threading.Lock()

        # Independent ID sequences for comments and replies.
        self._next_comment_id = 1
        self._next_reply_id = 1

        # post_id -> {comment_id -> comment_dict}; comments keep insertion
        # order because Python dicts preserve it.
        self._comments_by_post = {}

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def add_comment(self, post_id: int, user_id: str, content: str) -> dict:
        """
        Create a comment and return its dictionary representation:

        {
            "id": int,
            "post_id": int,
            "user_id": str,
            "content": str,
            "created_at": float,
            "replies": []
        }

        Raises NotFoundError if post_id does not exist.
        Raises ValidationError if user_id or content is empty.
        """
        # Validation order matters: fields first, then the post lookup.
        user_id = _validate_text(user_id, "user_id")
        content = _validate_text(content, "content")

        with self._lock:
            if not self.post_store.exists(post_id):
                raise NotFoundError(f"post {post_id} not found")

            comment_id = self._next_comment_id
            self._next_comment_id += 1

            comment = {
                "id": comment_id,
                "post_id": post_id,
                "user_id": user_id,
                "content": content,
                "created_at": time.time(),
                "replies": [],
            }

            # The stored copy is what the reply lookup reads; the returned copy
            # is a snapshot the caller may freely mutate.
            self._comments_by_post.setdefault(post_id, {})[comment_id] = {
                "id": comment["id"],
                "post_id": comment["post_id"],
                "user_id": comment["user_id"],
                "content": comment["content"],
                "created_at": comment["created_at"],
                "replies": [],
            }
            return comment

    def add_reply(self, post_id: int, comment_id: int, user_id: str, content: str) -> dict:
        """
        Create a reply to a comment and return its dictionary representation:

        {
            "id": int,
            "post_id": int,
            "comment_id": int,
            "user_id": str,
            "content": str,
            "created_at": float
        }

        Raises NotFoundError if post_id does not exist.
        Raises NotFoundError if comment_id does not exist for the given post.
        Raises ValidationError if user_id or content is empty.
        """
        # Validation order: fields, then post, then comment-under-post.
        user_id = _validate_text(user_id, "user_id")
        content = _validate_text(content, "content")

        with self._lock:
            if not self.post_store.exists(post_id):
                raise NotFoundError(f"post {post_id} not found")

            # O(1) lookup scoped to the post, so activation on one post never
            # scans the whole system and comments cannot leak across posts.
            comments = self._comments_by_post.get(post_id)
            comment = comments.get(comment_id) if comments else None
            if comment is None:
                raise NotFoundError(
                    f"comment {comment_id} not found for post {post_id}"
                )

            reply_id = self._next_reply_id
            self._next_reply_id += 1

            reply = {
                "id": reply_id,
                "post_id": post_id,
                "comment_id": comment_id,
                "user_id": user_id,
                "content": content,
                "created_at": time.time(),
            }
            comment["replies"].append(reply)
            return dict(reply)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_comments(self, post_id: int) -> list:
        """
        Return all comments for the given post.

        Each comment must include its "replies" list.
        Comments and replies must be returned in creation order.

        Raises NotFoundError if post_id does not exist.
        """
        with self._lock:
            if not self.post_store.exists(post_id):
                raise NotFoundError(f"post {post_id} not found")

            comments = self._comments_by_post.get(post_id)
            if not comments:
                return []

            # Deep snapshot: callers cannot reach internal dicts or lists.
            return [
                {
                    "id": comment["id"],
                    "post_id": comment["post_id"],
                    "user_id": comment["user_id"],
                    "content": comment["content"],
                    "created_at": comment["created_at"],
                    "replies": [dict(reply) for reply in comment["replies"]],
                }
                for comment in comments.values()
            ]
