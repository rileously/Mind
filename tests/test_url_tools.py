import unittest

from mind.url_tools import extract_quick_metadata, is_http_url, strip_tracking_params


class UrlToolsTests(unittest.TestCase):
    def test_is_http_url(self):
        self.assertTrue(is_http_url("https://github.com/rileously/Mind"))
        self.assertTrue(is_http_url("http://example.com/path?arg=1"))
        self.assertTrue(is_http_url("www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertFalse(is_http_url("not a url at all"))
        self.assertFalse(is_http_url("multi word phrase"))
        self.assertFalse(is_http_url("def foo(): return 42"))

    def test_strip_tracking_params_utm(self):
        dirty_url = "https://example.com/article?utm_source=twitter&utm_medium=social&utm_campaign=launch&id=123"
        clean = strip_tracking_params(dirty_url)
        self.assertEqual(clean, "https://example.com/article?id=123")

    def test_strip_tracking_params_social(self):
        fb_url = "https://example.com/post?fbclid=IwAR123456789&gclid=Cj0KCQiA"
        clean_fb = strip_tracking_params(fb_url)
        self.assertEqual(clean_fb, "https://example.com/post")

    def test_preserve_youtube_params(self):
        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=123456789&t=45s"
        clean_yt = strip_tracking_params(yt_url)
        self.assertIn("v=dQw4w9WgXcQ", clean_yt)
        self.assertIn("t=45s", clean_yt)
        self.assertNotIn("si=", clean_yt)

    def test_extract_quick_metadata(self):
        meta = extract_quick_metadata("https://github.com/rileously/Mind")
        self.assertEqual(meta.domain, "github.com")
        self.assertTrue(meta.title != "")
        self.assertTrue(meta.clean_url != "")

    def test_extract_metadata_with_html(self):
        html = (
            "<html><head>"
            "<title>Mind • Desktop AI Assistant</title>"
            "<meta property='og:description' content='System-wide AI productivity tool.'>"
            "</head></html>"
        )
        meta = extract_quick_metadata("https://github.com/rileously/Mind", html_snippet=html)
        self.assertEqual(meta.title, "Mind • Desktop AI Assistant")
        self.assertEqual(meta.description, "System-wide AI productivity tool.")


if __name__ == "__main__":
    unittest.main()
