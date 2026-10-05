"""
test_client.py — candidate-visible smoke test

This script is a quick behavioral check for comments, replies, isolation, and
basic validation. It is not the primary concurrency grader.

Run server.py first, then run this file in another terminal.
"""

import asyncio
import sys

import httpx

BASE_URL = "http://localhost:8080"


async def main():
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            health = await client.get(f"{BASE_URL}/health")
            health.raise_for_status()

            initial = await client.get(f"{BASE_URL}/post/10/comments")
            initial.raise_for_status()
            assert initial.json() == [], "Server state is not fresh. Restart server before running test_client.py"

            c1_response = await client.post(
                f"{BASE_URL}/post/10/comment/add",
                json={"user_id": "u1", "content": "First comment"},
            )
            assert c1_response.status_code == 201, c1_response.text
            c1 = c1_response.json()

            c2_response = await client.post(
                f"{BASE_URL}/post/10/comment/add",
                json={"user_id": "u2", "content": "Second comment"},
            )
            assert c2_response.status_code == 201, c2_response.text
            c2 = c2_response.json()

            r1_response = await client.post(
                f"{BASE_URL}/post/10/comment/{c1['id']}/reply",
                json={"user_id": "u3", "content": "First reply"},
            )
            assert r1_response.status_code == 201, r1_response.text
            r1 = r1_response.json()

            r2_response = await client.post(
                f"{BASE_URL}/post/10/comment/{c1['id']}/reply",
                json={"user_id": "u4", "content": "Second reply"},
            )
            assert r2_response.status_code == 201, r2_response.text
            r2 = r2_response.json()

            tree_response = await client.get(f"{BASE_URL}/post/10/comments")
            tree_response.raise_for_status()
            tree = tree_response.json()

            assert len(tree) == 2, f"expected 2 comments, got {len(tree)}"
            assert tree[0]["id"] == c1["id"], "comments are not in creation order"
            assert tree[1]["id"] == c2["id"], "comments are not in creation order"
            assert len(tree[0]["replies"]) == 2, "first comment should have 2 replies"
            assert tree[0]["replies"][0]["id"] == r1["id"], "replies are not in creation order"
            assert tree[0]["replies"][1]["id"] == r2["id"], "replies are not in creation order"
            assert tree[1]["replies"] == [], "second comment should have no replies"

            post20_response = await client.get(f"{BASE_URL}/post/20/comments")
            post20_response.raise_for_status()
            assert post20_response.json() == [], "comments leaked between posts"

            wrong_post_response = await client.post(
                f"{BASE_URL}/post/20/comment/{c1['id']}/reply",
                json={"user_id": "u5", "content": "wrong post"},
            )
            assert wrong_post_response.status_code == 404, "reply through wrong post should fail"

            empty_user_response = await client.post(
                f"{BASE_URL}/post/10/comment/add",
                json={"user_id": "", "content": "bad"},
            )
            assert empty_user_response.status_code == 400, "empty user_id should fail"

            missing_post_response = await client.post(
                f"{BASE_URL}/post/999/comment/add",
                json={"user_id": "u1", "content": "bad"},
            )
            assert missing_post_response.status_code == 404, "missing post should fail"

            print("ALL TESTS PASSED")

    except AssertionError as e:
        print(f"TEST FAILED: {e}")
        sys.exit(1)
    except httpx.HTTPError as e:
        print(f"HTTP ERROR: {e}")
        print("Make sure server.py is running first.")
        sys.exit(1)
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
