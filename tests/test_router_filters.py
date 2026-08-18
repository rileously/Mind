"""Finding the router page that could block a device.

Blocking has to happen on the router, and which page does it differs by
firmware. This is the looking, not the blocking: what is tested here is that a
page about filtering by address is recognised as one, that a page which merely
says "control" somewhere is not, and that the survey only ever reads.

Nothing here touches a router. The pages are the shapes Huawei's firmwares
return, and a fake session answers with them.
"""

import unittest

from mind.router_client import (
    FilterPage,
    FilterSurvey,
    RouterSession,
    find_endpoints,
    find_markers,
    harvest_paths,
    survey_report,
    survey_summary,
)


FILTER_PAGE = """
<html><head><script>
var WlanFilterList = new Array(
  new WlanFilter("InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.X_HW_WlanFilter.1",
                 "a2:27:ec:61:6a:a6", "1")
);
var MacFilterMode = "blacklist";
</script></head>
<body><form action="/html/bbsp/wlanfilter/set.cgi?x=InternetGatewayDevice.LANDevice.1"
      method="post"><input name="x.X_HW_Token"></form></body></html>
"""

MENU_PAGE = """
<html><body>
<a href="../wlanfilter/wlanfilter.asp">WLAN MAC filter</a>
<a href="/html/bbsp/dhcp/dhcp.asp">DHCP</a>
<a href="../wlanacl/wlanacl.asp">Access control list</a>
</body></html>
"""

ORDINARY_PAGE = """
<html><body><h1>WAN configuration</h1>
<script>var controlPanel = 1;</script></body></html>
"""


class MarkerTests(unittest.TestCase):
    def test_a_filter_page_is_recognised_by_what_only_it_says(self):
        markers = find_markers(FILTER_PAGE)
        self.assertIn("MacFilter", markers)
        self.assertIn("WlanFilter", markers)

    def test_a_page_that_merely_mentions_control_is_not_a_block_list(self):
        # "control" appears on half the pages a router serves. One coincidence
        # is not evidence, which is what the two-marker rule is for.
        page = FilterPage("/html/bbsp/wan/wan.asp", 200, len(ORDINARY_PAGE),
                          find_markers(ORDINARY_PAGE))
        self.assertFalse(page.promising)

    def test_two_markers_make_a_page_worth_reporting(self):
        page = FilterPage("/x.asp", 200, 100, find_markers(FILTER_PAGE))
        self.assertTrue(page.promising)

    def test_the_hex_escaped_shape_reads_the_same(self):
        # These firmwares escape every value, exactly as the device list does.
        escaped = FILTER_PAGE.replace("blacklist", "blackl\\x69st")
        self.assertIn("blacklist", find_markers(escaped))


class EndpointTests(unittest.TestCase):
    def test_where_the_page_submits_is_read_off_it(self):
        endpoints = find_endpoints(FILTER_PAGE)
        self.assertTrue(any("set.cgi" in endpoint for endpoint in endpoints))

    def test_a_page_that_submits_nowhere_offers_nothing(self):
        self.assertEqual(find_endpoints(ORDINARY_PAGE), ())


class HarvestTests(unittest.TestCase):
    def test_a_relative_link_is_resolved_against_the_page_it_was_found_on(self):
        found = harvest_paths(MENU_PAGE, "/html/bbsp/common/menu.asp")
        self.assertIn("/html/bbsp/wlanfilter/wlanfilter.asp", found)

    def test_an_absolute_link_is_kept_as_it_is(self):
        found = harvest_paths(MENU_PAGE, "/html/bbsp/common/menu.asp")
        self.assertIn("/html/bbsp/dhcp/dhcp.asp", found)

    def test_a_page_linking_to_nothing_gives_nothing_rather_than_raising(self):
        self.assertEqual(harvest_paths("", "/"), [])
        self.assertEqual(harvest_paths("<html></html>", "/index.asp"), [])


class FakeSession(RouterSession):
    """A session that answers from a dictionary and records what was asked."""

    def __init__(self, pages: dict[str, str]):
        super().__init__("192.168.18.1")
        self.pages = pages
        self.asked: list[str] = []

    def read(self, path):
        self.asked.append(path)
        body = self.pages.get(path)
        if body is None:
            return 404, "not found"
        return 200, body

    def _open(self, path, data=None, cookie=""):  # pragma: no cover - a guard
        raise AssertionError("the survey must never open a page except through read()")


class SurveyTests(unittest.TestCase):
    def setUp(self):
        self.pages = {
            "/": MENU_PAGE,
            "/html/bbsp/wlanfilter/wlanfilter.asp": FILTER_PAGE,
            "/html/bbsp/dhcp/dhcp.asp": ORDINARY_PAGE,
        }

    def test_the_page_that_keeps_the_block_list_is_found_and_reported_first(self):
        survey = FilterSurvey(FakeSession(self.pages))
        found = survey.run()
        self.assertTrue(found[0].promising)
        self.assertEqual(found[0].path, "/html/bbsp/wlanfilter/wlanfilter.asp")

    def test_the_wireless_list_is_named_before_the_wired_one(self):
        # A router keeps both, and the question being asked is about Wi-Fi.
        pages = dict(self.pages)
        pages["/html/bbsp/macfilter/macfilter.asp"] = FILTER_PAGE
        found = FilterSurvey(FakeSession(pages)).run()
        self.assertIn("wlan", found[0].path)

    def test_a_page_named_in_the_menu_is_followed(self):
        # The link is relative and the page is not in the guessed list, so
        # finding it proves the menu was read rather than guessed past - and
        # that "../" was resolved rather than asked for as it was written.
        session = FakeSession({"/": MENU_PAGE})
        FilterSurvey(session).run()
        self.assertIn("/wlanacl/wlanacl.asp", session.asked)

    def test_a_page_with_nothing_to_do_with_blocking_is_not_followed(self):
        session = FakeSession({"/": MENU_PAGE})
        FilterSurvey(session).run()
        self.assertNotIn("/html/bbsp/dhcp/dhcp.asp", session.asked)

    def test_the_survey_stops_rather_than_walking_the_whole_router(self):
        endless = {"/": MENU_PAGE}
        for index in range(200):
            endless[f"/html/bbsp/filter{index}/filter{index}.asp"] = (
                f'<a href="/html/bbsp/filter{index + 1}/filter{index + 1}.asp">on</a>'
            )
        session = FakeSession(endless)
        FilterSurvey(session, limit=12).run()
        self.assertLessEqual(len(session.asked), 12)

    def test_nothing_is_submitted_anywhere(self):
        # The point of the survey: it looks. FakeSession raises if anything
        # reaches the method that can carry a form body.
        FilterSurvey(FakeSession(self.pages)).run()

    def test_what_it_found_is_said_in_one_line(self):
        # It goes on a status line in the window. Four markers and three form
        # targets wrap to five lines there and say less than the name does.
        found = FilterSurvey(FakeSession(self.pages)).run()
        summary = survey_summary(found)
        self.assertEqual(len(summary), 1)
        self.assertIn("wlanfilter.asp", summary[0])
        self.assertLess(len(summary[0]), 120)

    def test_the_detail_is_kept_where_someone_acting_on_it_would_look(self):
        found = FilterSurvey(FakeSession(self.pages)).run()
        report = survey_report(found)
        self.assertIn("set.cgi", report)
        self.assertIn("MacFilter", report)

    def test_finding_nothing_says_so_rather_than_saying_nothing(self):
        found = FilterSurvey(FakeSession({"/": ORDINARY_PAGE})).run()
        summary = " ".join(survey_summary(found))
        self.assertIn("No page looked like a block list", summary)


if __name__ == "__main__":
    unittest.main()
