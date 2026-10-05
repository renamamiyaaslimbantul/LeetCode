"""
test_shortener.py — public unit tests for URLShortener
Run: python test_shortener.py
"""

import os
import tempfile
import threading
import time
import unittest

from db import reset_connections_for_tests
from shortener import (
    CodeGenerationError,
    ConflictError,
    ExpiredLinkError,
    NotFoundError,
    PermissionDeniedError,
    URLShortener,
    ValidationError,
)


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        reset_connections_for_tests()
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.svc = URLShortener(self.tmp.name)

    def tearDown(self):
        reset_connections_for_tests()
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)


class TestCreate(ServiceTestCase):
    def test_create_returns_required_fields(self):
        link = self.svc.create("https://example.com/path")
        data = link.to_dict()

        self.assertRegex(data["short_code"], r"^[A-Za-z0-9]{6,10}$")
        self.assertEqual(data["long_url"], "https://example.com/path")
        self.assertIn("created_at", data)
        self.assertIsNone(data["expires_at"])
        self.assertEqual(data["hit_count"], 0)
        self.assertFalse(data["is_expired"])
        self.assertNotIn("owner_token", data)
        self.assertTrue(link.owner_token.startswith("tok_"))

    def test_custom_code(self):
        link = self.svc.create("https://example.com", custom_code="docs_2026")

        self.assertEqual(link.short_code, "docs_2026")
        self.assertEqual(self.svc.get("docs_2026").long_url, "https://example.com")

    def test_duplicate_custom_code_raises_conflict(self):
        self.svc.create("https://a.example", custom_code="same-code")

        with self.assertRaises(ConflictError):
            self.svc.create("https://b.example", custom_code="same-code")

    def test_validation(self):
        bad_urls = [
            None,
            "",
            "   ",
            "example.com",
            "www.example.com",
            "ftp://example.com",
            "https://",
            "http://",
        ]

        for url in bad_urls:
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    self.svc.create(url)

        bad_codes = [
            "",
            "ab",
            "has space",
            "bad/code",
            "bad.code",
            "bad@code",
            "x" * 33,
            123,
            True,
        ]

        for code in bad_codes:
            with self.subTest(code=code):
                with self.assertRaises(ValidationError):
                    self.svc.create("https://example.com", custom_code=code)

        bad_ttls = [
            0,
            -1,
            -0.5,
            "10",
            "",
            True,
            False,
        ]

        for ttl in bad_ttls:
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValidationError):
                    self.svc.create("https://example.com", ttl_seconds=ttl)

    def test_valid_custom_code_boundaries(self):
        a = self.svc.create("https://a.example", custom_code="abc")
        b = self.svc.create("https://b.example", custom_code="x" * 32)
        c = self.svc.create("https://c.example", custom_code="Ab9_-Z")

        self.assertEqual(a.short_code, "abc")
        self.assertEqual(b.short_code, "x" * 32)
        self.assertEqual(c.short_code, "Ab9_-Z")

    def test_ttl_expiry(self):
        link = self.svc.create("https://example.com", ttl_seconds=0.25)

        self.assertFalse(self.svc.get(link.short_code).is_expired)

        time.sleep(0.35)

        self.assertTrue(self.svc.get(link.short_code).is_expired)


class TestRedirectAndHits(ServiceTestCase):
    def test_resolve_redirect_counts_one_hit(self):
        link = self.svc.create("https://example.com/target")

        self.assertEqual(
            self.svc.resolve_redirect(link.short_code),
            "https://example.com/target",
        )
        self.assertEqual(self.svc.get(link.short_code).hit_count, 1)

    def test_missing_redirect_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.svc.resolve_redirect("missing")

    def test_increment_missing_code_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.svc.increment_hits("missing")

    def test_increment_hits_counts_one_hit(self):
        link = self.svc.create("https://example.com")

        self.svc.increment_hits(link.short_code)

        self.assertEqual(self.svc.get(link.short_code).hit_count, 1)

    def test_expired_redirect_does_not_count_hit(self):
        link = self.svc.create("https://example.com", ttl_seconds=0.2)

        time.sleep(0.3)

        with self.assertRaises(ExpiredLinkError):
            self.svc.resolve_redirect(link.short_code)

        self.assertEqual(self.svc.get(link.short_code).hit_count, 0)

    def test_concurrent_redirects_count_exactly(self):
        link = self.svc.create("https://example.com")

        threads = [
            threading.Thread(
                target=self.svc.resolve_redirect,
                args=(link.short_code,),
            )
            for _ in range(100)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        self.assertEqual(self.svc.get(link.short_code).hit_count, 100)

    def test_concurrent_increment_hits_count_exactly(self):
        link = self.svc.create("https://example.com")

        threads = [
            threading.Thread(
                target=self.svc.increment_hits,
                args=(link.short_code,),
            )
            for _ in range(100)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        self.assertEqual(self.svc.get(link.short_code).hit_count, 100)


class TestDeleteAndStats(ServiceTestCase):
    def test_delete_requires_matching_owner_token(self):
        link = self.svc.create("https://example.com")

        with self.assertRaises(ValidationError):
            self.svc.delete(link.short_code, "")

        with self.assertRaises(PermissionDeniedError):
            self.svc.delete(link.short_code, "wrong")

        self.assertIsNotNone(self.svc.get(link.short_code))
        self.assertTrue(self.svc.delete(link.short_code, link.owner_token))
        self.assertIsNone(self.svc.get(link.short_code))
        self.assertFalse(self.svc.delete(link.short_code, link.owner_token))

    def test_stats(self):
        codes = []

        for i in range(6):
            link = self.svc.create(f"https://example.com/{i}", custom_code=f"code{i}")
            codes.append(link.short_code)

            for _ in range(i):
                self.svc.resolve_redirect(link.short_code)

        stats = self.svc.get_stats()

        self.assertEqual(stats["total_links"], 6)
        self.assertEqual(stats["total_hits"], 15)
        self.assertEqual(len(stats["top_5_links"]), 5)
        self.assertEqual(
            [row["short_code"] for row in stats["top_5_links"]],
            list(reversed(codes[1:])),
        )

    def test_deleted_link_removed_from_stats(self):
        link = self.svc.create("https://example.com", custom_code="gone1")
        self.svc.resolve_redirect("gone1")

        self.assertTrue(self.svc.delete("gone1", link.owner_token))

        stats = self.svc.get_stats()

        self.assertEqual(stats["total_links"], 0)
        self.assertEqual(stats["total_hits"], 0)
        self.assertEqual(stats["top_5_links"], [])


class TestCodeGenerationFailure(ServiceTestCase):
    def test_collision_retry_limit(self):
        self.svc.create("https://taken.example", custom_code="ABCDEF")
        self.svc._generate_code = lambda long_url: "ABCDEF"

        with self.assertRaises(CodeGenerationError):
            self.svc.create("https://new.example")

    def test_generated_code_invalid_format(self):
        self.svc._generate_code = lambda long_url: "bad/code"

        with self.assertRaises(CodeGenerationError):
            self.svc.create("https://new.example")


if __name__ == "__main__":
    unittest.main(verbosity=2)