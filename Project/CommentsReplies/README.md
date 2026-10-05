
# Q2: In-memory comments and replies

## What you should know

You should be comfortable with:

- In-memory data modeling
- Parent-child resource relationships
- Input validation and error handling
- Basic concurrency and locking

---

## Scenario

The discussion platform currently supports posts. Add comments and replies to it.

Users should be able to leave a comment on a post and reply to someone else's comment. The HTTP server and mock post store are provided, so the implementation belongs in `comment_service.py`.

The server expects `CommentService` to handle writes and return the complete discussion under a post.

The system supports exactly two levels of discussion:

```text
Post -> Comment -> Reply
```

A reply belongs directly to a comment, with no deeper nesting.

---

## Goal

Finish the in-memory service behind the comment API. The dictionaries it returns must match the contract below exactly.

---

## Requirements

The service must:

- add comments to existing posts
- add replies to existing comments
- use independent auto-incrementing integer ID sequences for comments and replies
- return dictionaries matching the expected shapes
- prevent callers from mutating internal state through returned dictionaries
- be safe under concurrent requests

---

## Available Posts

Read the initial posts from `post_store.py`. The mock post store has two posts:

- `10`: `"Welcome to LeetCode"`
- `20`: `"System Design Guide"`

Any operation that refers to a missing `post_id` must fail.

---

## API Contract

For both write methods, `user_id` and `content` must be strings with at least one non-whitespace character. Reject `None`, other data types, empty strings, and strings made only of whitespace.

Validation order matters to the tests. Check `user_id` and `content` before looking up the post. When adding a reply, look up the post next, then check that the comment exists under that post.

### Add Comment

```python
add_comment(post_id: int, user_id: str, content: str) -> dict
```

Creates a comment on an existing post and returns:

```json
{
    "id": int,
    "post_id": int,
    "user_id": str,
    "content": str,
    "created_at": float,
    "replies": []
}
```

Validation:

- raises `NotFoundError` if `post_id` does not exist
- raises `ValidationError` if `user_id` or `content` is not a string, or is empty after trimming whitespace

### Add Reply

```python
add_reply(post_id: int, comment_id: int, user_id: str, content: str) -> dict
```

Creates a reply to an existing comment and returns:

```json
{
    "id": int,
    "post_id": int,
    "comment_id": int,
    "user_id": str,
    "content": str,
    "created_at": float
}
```

Validation:

- raises `NotFoundError` if `post_id` does not exist
- raises `NotFoundError` if `comment_id` does not exist, or exists but belongs to a different post
- raises `ValidationError` if `user_id` or `content` is not a string, or is empty after trimming whitespace

### Get Comments

```python
get_comments(post_id: int) -> list
```

Returns the post's entire comment thread. Use the same shapes and data types returned by `add_comment` and `add_reply`.

Example return value:

```json
[
    {
        "id": 1,
        "post_id": 10,
        "user_id": "u1",
        "content": "Great explanation!",
        "created_at": 1710000000.0,
        "replies": [
            {
                "id": 1,
                "post_id": 10,
                "comment_id": 1,
                "user_id": "u2",
                "content": "I agree.",
                "created_at": 1710000001.0
            }
        ]
    }
]
```

If the post exists but has no comments, return `[]`.

---

## Expected Semantics

### General

- comments belong to exactly one post
- replies belong to exactly one comment
- replies are not recursive
- state must be isolated per post
- comment IDs and reply IDs must be independent sequences
- returned dictionaries must be safe to mutate without changing internal service state
- `created_at` must be returned in **seconds**

### Ordering

- comments must be returned in creation order
- replies must be returned in creation order
- creation order means the order in which the service successfully commits each operation
- `created_at` is response metadata and should not be used as the source of truth for ordering

### Concurrency

Requests can reach the service concurrently. IDs must stay unique, writes must not corrupt one another, and activity on one post must not leak into another. A single `threading.Lock()` is enough for this exercise.

### Complexity

After correctness, pay attention to the basic cost of each operation:

- adding a comment should be efficient
- adding a reply should avoid unnecessary full-system scans
- retrieving comments should be proportional to the number of comments and replies returned
---

## Examples

### Comment and Reply

The calls below create and retrieve one complete thread.

1. Add a comment to post `10`:

    ```python
    add_comment(post_id=10, user_id="u1", content="Hello Leet")
    ```

    Returns:

    ```json
    {
        "id": 1,
        "post_id": 10,
        "user_id": "u1",
        "content": "Hello Leet",
        "created_at": 1781086734.00,
        "replies": []
    }
    ```

2. Add a reply to that comment:

    ```python
    add_reply(post_id=10, comment_id=1, user_id="u2", content="Hi Code")
    ```

    Returns:

    ```json
    {
        "id": 1,
        "post_id": 10,
        "comment_id": 1,
        "user_id": "u2",
        "content": "Hi Code",
        "created_at": 1781086739.00
    }
    ```

3. Retrieve comments for post `10`:

    ```python
    get_comments(post_id=10)
    ```

    Returns:

    ```json
    [
        {
            "id": 1,
            "post_id": 10,
            "user_id": "u1",
            "content": "Hello Leet",
            "created_at": 1781086734.00,
            "replies": [
                {
                    "id": 1,
                    "post_id": 10,
                    "comment_id": 1,
                    "user_id": "u2",
                    "content": "Hi Code",
                    "created_at": 1781086739.00
                }
            ]
        }
    ]
    ```

### Post Isolation

1. Create a comment on post `10`
2. Try to add a reply to that comment using `post_id=20`

The reply must raise `NotFoundError` because the comment belongs to post `10`, not post `20`.

### Independent IDs

```
First comment ID = 1
First reply ID   = 1
```

Both values are `1` because comments and replies use different counters.

### Mutation Safety

1. Retrieve comments for a post
2. Modify the returned list or dictionaries
3. Retrieve comments again

The second result should still contain the original stored values.

---

## Estimated Completion Time

45 minutes

---

## Files

You will find these files in the starter project:

- `server.py`: completed server, provided for reference
- `post_store.py`: completed mock post store, provided for reference
- `comment_service.py`: the file you need to implement
- `test_client.py`: smoke test
- `README.md`: this file
- `requirements.txt`: local dependencies
- `REFLECTION.essay`: your thoughts on locking strategy, snapshot semantics, and horizontal scaling

All of your changes belong in `comment_service.py` and `REFLECTION.essay`.

---

## Running Locally

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run the server:

```bash
python3 server.py
```

In another terminal, run the smoke test:

```bash
python3 test_client.py
```

> The smoke test is only a quick check. Hidden tests also cover edge cases, concurrent calls, mutation safety, response shapes, and isolation between posts.
