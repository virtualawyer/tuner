"""Task 3.1 -- conditional GET against the YouTube feed.

No test here touches the network: feed.OPENER is replaced with a stub that
records the request it was handed.
"""

import contextlib
import io
import unittest
import urllib.error

from tuner import USER_AGENT, feed

CHANNEL = "UCabcdefghijklmnopqrstuv"


class FakeResponse(object):

    def __init__(self, body=b"<feed/>", status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.closed = False

    def read(self):
        return self.body

    def getcode(self):
        return self.status

    def close(self):
        self.closed = True


@contextlib.contextmanager
def stub_opener(response=None, raises=None):
    """Swap in a recording opener for the duration of a test."""
    seen = {}

    def opener(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        seen["headers"] = dict(request.header_items())
        if raises is not None:
            raise raises
        return response

    previous = feed.OPENER
    feed.OPENER = opener
    try:
        yield seen
    finally:
        feed.OPENER = previous


class FetchTest(unittest.TestCase):

    def test_plain_fetch_returns_body_and_validators(self):
        response = FakeResponse(b"<feed>hello</feed>", 200,
                                {"ETag": 'W/"abc"',
                                 "Last-Modified": "Mon, 11 Aug 2026 10:00:00 GMT"})
        with stub_opener(response):
            status, body, etag, modified = feed.fetch(CHANNEL)

        self.assertEqual(status, 200)
        self.assertEqual(body, "<feed>hello</feed>")
        self.assertEqual(etag, 'W/"abc"')
        self.assertEqual(modified, "Mon, 11 Aug 2026 10:00:00 GMT")
        self.assertTrue(response.closed)

    def test_requests_the_right_url_with_our_user_agent(self):
        with stub_opener(FakeResponse()) as seen:
            feed.fetch(CHANNEL)

        self.assertEqual(
            seen["request"].full_url,
            "https://www.youtube.com/feeds/videos.xml?channel_id=" + CHANNEL)
        self.assertEqual(seen["headers"].get("User-agent"), USER_AGENT)
        self.assertEqual(seen["timeout"], feed.TIMEOUT)

    def test_validators_are_sent_when_stored(self):
        with stub_opener(FakeResponse()) as seen:
            feed.fetch(CHANNEL, etag='W/"abc"',
                       last_modified="Mon, 11 Aug 2026 10:00:00 GMT")

        headers = seen["headers"]
        self.assertEqual(headers.get("If-none-match"), 'W/"abc"')
        self.assertEqual(headers.get("If-modified-since"),
                         "Mon, 11 Aug 2026 10:00:00 GMT")

    def test_validators_are_omitted_when_absent(self):
        with stub_opener(FakeResponse()) as seen:
            feed.fetch(CHANNEL)

        self.assertNotIn("If-none-match", seen["headers"])
        self.assertNotIn("If-modified-since", seen["headers"])

    def test_304_short_circuits_and_keeps_the_validators(self):
        error = urllib.error.HTTPError(
            "url", 304, "Not Modified", {}, io.BytesIO(b""))
        with stub_opener(raises=error):
            status, body, etag, modified = feed.fetch(
                CHANNEL, etag='W/"abc"', last_modified="Mon, 11 Aug 2026 10:00:00 GMT")

        self.assertEqual(status, 304)
        self.assertIsNone(body)
        self.assertEqual(etag, 'W/"abc"')
        self.assertEqual(modified, "Mon, 11 Aug 2026 10:00:00 GMT")

    def test_304_as_a_plain_response_is_also_handled(self):
        with stub_opener(FakeResponse(b"", 304)):
            status, body, etag, _ = feed.fetch(CHANNEL, etag='W/"abc"')
        self.assertEqual(status, 304)
        self.assertIsNone(body)
        self.assertEqual(etag, 'W/"abc"')

    def test_a_missing_etag_in_the_response_keeps_the_old_one(self):
        with stub_opener(FakeResponse(b"<feed/>", 200, {})):
            _, _, etag, modified = feed.fetch(CHANNEL, etag='W/"old"',
                                              last_modified="yesterday")
        self.assertEqual(etag, 'W/"old"')
        self.assertEqual(modified, "yesterday")

    def test_body_is_decoded_as_utf8(self):
        with stub_opener(FakeResponse("<feed>Ünïcødé</feed>".encode("utf-8"))):
            _, body, _, _ = feed.fetch(CHANNEL)
        self.assertIn("Ünïcødé", body)


class FetchFailureTest(unittest.TestCase):

    def assertFeedError(self, error, status=None):
        with stub_opener(raises=error):
            with self.assertRaises(feed.FeedError) as caught:
                feed.fetch(CHANNEL)
        if status is not None:
            self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_403_404_and_500_are_feed_errors_carrying_the_code(self):
        for code in (403, 404, 500, 503):
            error = urllib.error.HTTPError("url", code, "boom", {},
                                           io.BytesIO(b""))
            self.assertFeedError(error, status=code)

    def test_connection_failure_is_a_feed_error(self):
        exc = self.assertFeedError(urllib.error.URLError("no route to host"))
        self.assertIn("cannot reach YouTube", str(exc))

    def test_non_200_response_is_a_feed_error(self):
        with stub_opener(FakeResponse(b"", 204)):
            with self.assertRaises(feed.FeedError):
                feed.fetch(CHANNEL)


if __name__ == "__main__":
    unittest.main()
