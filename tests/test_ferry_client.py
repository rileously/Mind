"""Asking RTL which ferries go where, without asking RTL.

The description is a hundred kilobytes of islands and routes, so what is
tested here is the reading of it: that a route knows which way round it goes,
that an island is found by the name somebody would actually type, and that a
cache which cannot be written slows things down rather than breaking them.

The shapes below are the ones the real endpoint returns, trimmed. A route's
stop arrives as a whole island nested under "stop", not as a name, which is
the detail that makes a naive reading return nothing at all.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from mind.ferry_client import (
    FerryError,
    Route,
    Stop,
    match_stops,
    network,
    parse_routes,
    parse_stops,
    routes_between,
)


def island(name, code, dv=""):
    return {"name": name, "code": code, "dvname": dv, "latitude": "6.1", "longitude": "73.2"}


PAYLOAD = {
    "ferryStops": [
        island("Hdh.Naivaadhoo", "105", "ހދ.ނައިވާދޫ"),
        island("Hdh.Kulhudhuffushi", "104"),
        island("Hdh.Nellaidhoo", "106"),
        island("Ha.Ihavandhoo", "113"),
    ],
    "ferryZones": [
        {
            "name": "Zone 1",
            "ferryRoutes": [
                {
                    "name": "R1C5", "code": "1210", "zone": "Zone 1",
                    "description": "Hdh.Naivaadhoo - Hdh.Kulhudhuffushi",
                    "stops": [
                        {"order": 1, "stop": island("Hdh.Naivaadhoo", "105")},
                        {"order": 2, "stop": island("Hdh.Nellaidhoo", "106")},
                        {"order": 3, "stop": island("Hdh.Kulhudhuffushi", "104")},
                    ],
                },
                {
                    "name": "R1C55", "code": "140", "zone": "Zone 1",
                    "description": "Hdh.Kulhudhuffushi - Hdh.Naivaadhoo",
                    "stops": [
                        {"order": 1, "stop": island("Hdh.Kulhudhuffushi", "104")},
                        {"order": 2, "stop": island("Hdh.Naivaadhoo", "105")},
                    ],
                },
            ],
        }
    ],
}


class ReadingTheNetwork(unittest.TestCase):
    def test_every_island_is_read(self):
        self.assertEqual(len(parse_stops(PAYLOAD)), 4)

    def test_an_island_keeps_its_dhivehi_name(self):
        naiva = next(s for s in parse_stops(PAYLOAD) if s.code == "105")
        self.assertTrue(naiva.dhivehi)

    def test_the_island_name_drops_its_atoll(self):
        self.assertEqual(Stop(name="Hdh.Naivaadhoo", code="105").island, "Naivaadhoo")

    def test_an_island_with_no_prefix_is_left_alone(self):
        self.assertEqual(Stop(name="Male", code="1").island, "Male")

    def test_a_stop_arrives_nested_and_is_still_read(self):
        # The whole point: routes carry islands, not names.
        routes = parse_routes(PAYLOAD)
        self.assertEqual(routes[0].stops[0], "Hdh.Naivaadhoo")

    def test_stops_come_back_in_calling_order(self):
        # They are given out of order more often than not.
        payload = json.loads(json.dumps(PAYLOAD))
        payload["ferryZones"][0]["ferryRoutes"][0]["stops"].reverse()
        route = parse_routes(payload)[0]
        self.assertEqual(route.stops[0], "Hdh.Naivaadhoo")
        self.assertEqual(route.stops[-1], "Hdh.Kulhudhuffushi")

    def test_nothing_at_all_is_not_a_crash(self):
        self.assertEqual(parse_stops({}), [])
        self.assertEqual(parse_routes({}), [])


class FindingAnIsland(unittest.TestCase):
    def setUp(self):
        self.stops = parse_stops(PAYLOAD)

    def test_the_name_people_actually_type(self):
        self.assertEqual([s.name for s in match_stops(self.stops, "naivaadhoo")], ["Hdh.Naivaadhoo"])

    def test_part_of_a_name_is_enough(self):
        self.assertEqual(len(match_stops(self.stops, "kulhudhu")), 1)

    def test_an_exact_name_is_not_buried_among_the_others(self):
        stops = self.stops + [Stop(name="Hdh.Naivaadhoo Two", code="999")]
        self.assertEqual([s.name for s in match_stops(stops, "naivaadhoo")], ["Hdh.Naivaadhoo"])

    def test_nothing_typed_matches_nothing(self):
        # Rather than everything, which would be a long and useless answer.
        self.assertEqual(match_stops(self.stops, "   "), [])

    def test_an_island_that_is_not_there(self):
        self.assertEqual(match_stops(self.stops, "zzzz"), [])


class WhichWayRound(unittest.TestCase):
    def setUp(self):
        self.routes = parse_routes(PAYLOAD)

    def test_only_the_route_going_that_way_is_offered(self):
        going = routes_between(self.routes, "Hdh.Naivaadhoo", "Hdh.Kulhudhuffushi")
        self.assertEqual([r.name for r in going], ["R1C5"])

    def test_and_the_other_way_gives_the_other_route(self):
        back = routes_between(self.routes, "Hdh.Kulhudhuffushi", "Hdh.Naivaadhoo")
        self.assertEqual([r.name for r in back], ["R1C55"])

    def test_the_stops_on_the_way_exclude_both_ends(self):
        route = self.routes[0]
        self.assertEqual(route.between("Hdh.Naivaadhoo", "Hdh.Kulhudhuffushi"), ("Hdh.Nellaidhoo",))

    def test_a_pair_no_route_serves(self):
        self.assertEqual(routes_between(self.routes, "Ha.Ihavandhoo", "Hdh.Nellaidhoo"), [])

    def test_an_island_that_is_not_on_the_route(self):
        self.assertEqual(self.routes[0].position("Ha.Ihavandhoo"), -1)

    def test_the_fewest_stops_is_offered_first(self):
        long_way = Route(name="Slow", code="9", zone="Z", description="",
                         stops=("Hdh.Naivaadhoo", "A", "B", "C", "Hdh.Kulhudhuffushi"))
        going = routes_between(self.routes + [long_way], "Hdh.Naivaadhoo", "Hdh.Kulhudhuffushi")
        self.assertEqual(going[0].name, "R1C5")


class KeepingIt(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "sub" / "ferry.json"

    def test_the_first_ask_is_written_down(self):
        calls = []
        network(cache=self.cache, fetch=lambda: (calls.append(1), PAYLOAD)[1])
        self.assertTrue(self.cache.exists())
        self.assertEqual(len(calls), 1)

    def test_the_second_ask_does_not_leave_the_machine(self):
        calls = []
        fetch = lambda: (calls.append(1), PAYLOAD)[1]
        network(cache=self.cache, fetch=fetch)
        network(cache=self.cache, fetch=fetch)
        self.assertEqual(len(calls), 1)

    def test_a_stale_copy_is_asked_for_again(self):
        calls = []
        fetch = lambda: (calls.append(1), PAYLOAD)[1]
        network(cache=self.cache, fetch=fetch)
        later = time.time() + 60 * 60 * 48
        network(cache=self.cache, fetch=fetch, now=lambda: later)
        self.assertEqual(len(calls), 2)

    def test_a_corrupt_copy_is_replaced_rather_than_raised(self):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text("not json at all", encoding="utf-8")
        got = network(cache=self.cache, fetch=lambda: PAYLOAD)
        self.assertIn("ferryStops", got)

    def test_no_cache_at_all_still_works(self):
        self.assertIn("ferryStops", network(cache=None, fetch=lambda: PAYLOAD))

    def test_a_failure_to_reach_rtl_says_so(self):
        def refuse():
            raise FerryError("Could not reach RTL")

        with self.assertRaises(FerryError):
            network(cache=self.cache, fetch=refuse)


if __name__ == "__main__":
    unittest.main()
