class PostStore:
    def __init__(self):
        self._posts = {
            10: "Welcome to LeetCode",
            20: "System Design Guide",
        }

    def exists(self, post_id: int) -> bool:
        return post_id in self._posts
