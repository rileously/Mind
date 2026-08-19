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


SEATS_REPLY = {
    "resultType": 1,
    "schedules": {
        "fromStationName": "Hdh.Naivaadhoo",
        "toStationName": "Hdh.Kulhudhuffushi",
        "journey": [
            {
                "totalFare": 70,
                "instances": [
                    {
                        "scheduleId": "9911",
                        "routeName": "R1C5",
                        "routeCode": "1210",
                        "assetName": "RTL109",
                        "startTime": "20260820083000",
                        "endTime": "20260820092000",
                        "intermediateStops": 2,
                        "deck": {
                            "seatCount": 4,
                            "seats": [
                                {"code": 1, "status": 1},
                                {"code": 2, "status": 0},
                                {"code": 3, "status": 1},
                                {"code": 4, "status": 1},
                            ],
                        },
                    }
                ],
            }
        ],
    },
}


class ReadingSailings(unittest.TestCase):
    """The seats reply, which is the part that changes while somebody decides."""

    def test_a_sailing_is_read_whole(self):
        from mind.ferry_client import parse_sailings

        sail = parse_sailings(SEATS_REPLY)[0]
        self.assertEqual((sail.route, sail.boat, sail.stops), ("R1C5", "RTL109", 2))
        self.assertEqual(sail.fare, 70)

    def test_the_times_come_out_of_fourteen_digits(self):
        from mind.ferry_client import parse_sailings

        sail = parse_sailings(SEATS_REPLY)[0]
        self.assertEqual(sail.departs_at, "08:30")
        self.assertEqual(sail.arrives_at, "09:20")

    def test_free_seats_are_counted_not_taken_on_trust(self):
        # RTL sends every seat with a status; only status 1 is free.
        from mind.ferry_client import parse_sailings

        sail = parse_sailings(SEATS_REPLY)[0]
        self.assertEqual((sail.seats_free, sail.seats_total), (3, 4))
        self.assertFalse(sail.full)

    def test_a_sailing_with_no_seats_left_says_so(self):
        from mind.ferry_client import parse_sailings

        payload = json.loads(json.dumps(SEATS_REPLY))
        for seat in payload["schedules"]["journey"][0]["instances"][0]["deck"]["seats"]:
            seat["status"] = 0
        self.assertTrue(parse_sailings(payload)[0].full)

    def test_nothing_sailing_is_not_an_error(self):
        from mind.ferry_client import parse_sailings

        self.assertEqual(parse_sailings({"schedules": {"journey": []}}), [])
        self.assertEqual(parse_sailings({}), [])


class TheTimeStuckOnTheDate(unittest.TestCase):
    """RTL wants yyyyMMdd with a time after it, and the time is not decorative."""

    def test_today_is_asked_about_from_now(self):
        # So sailings that have already gone are not offered.
        from mind.ferry_client import trip_stamp

        moment = time.mktime((2026, 8, 20, 14, 30, 0, 0, 0, -1))
        self.assertEqual(trip_stamp("20260820", now=moment), "20260820143000")

    def test_a_later_day_is_asked_about_from_midnight(self):
        # Using the current time would hide that morning's sailings.
        from mind.ferry_client import trip_stamp

        moment = time.mktime((2026, 8, 20, 14, 30, 0, 0, 0, -1))
        self.assertEqual(trip_stamp("20260825", now=moment), "20260825000001")


class TheRequestRtlAcceptsel(unittest.TestCase):
    """One field decides whether any of this works."""

    def sent(self):
        import io as _io
        from mind.ferry_client import sailings

        captured = {}

        class Answer:
            def __enter__(self_inner):
                return _io.BytesIO(json.dumps(SEATS_REPLY).encode())

            def __exit__(self_inner, *a):
                return False

        def opener(request):
            captured["body"] = json.loads(request.data.decode())
            return Answer()

        sailings("105", "104", "20260820", opener=opener)
        return captured["body"]

    def test_schedule_id_is_null_and_not_an_empty_list(self):
        # An empty list is accepted and then returns no sailings at all, which
        # reads exactly like a day with no service. This is that bug, pinned.
        body = self.sent()
        self.assertIsNone(body["scheduleId"])

    def test_the_device_is_a_number(self):
        self.assertEqual(self.sent()["deviceType"], 1)

    def test_the_stations_travel_as_codes(self):
        body = self.sent()
        self.assertEqual((body["sourceStation"], body["destinationStation"]), ("105", "104"))


class TheSeatMap(unittest.TestCase):
    """The boat's own arrangement: six across, three each side of the aisle."""

    def rows(self, codes):
        from mind.telegram_ui import seat_rows

        return seat_rows(codes)

    def test_six_to_a_row(self):
        self.assertEqual(self.rows(range(1, 13)), [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]])

    def test_a_seat_keeps_the_column_its_number_gives_it(self):
        # Closing the gap would slide every seat after it one place along, and
        # somebody would pick a window seat and get the aisle.
        rows = self.rows([1, 3, 4, 6])
        self.assertEqual(rows, [[1, None, 3, 4, None, 6]])

    def test_a_short_last_row_stays_where_its_numbers_put_it(self):
        rows = self.rows(range(1, 45))
        self.assertEqual(rows[-1], [43, 44, None, None, None, None])

    def test_no_seats_at_all(self):
        self.assertEqual(self.rows([]), [])

    def test_taken_seats_keep_the_shape_and_cannot_be_picked(self):
        from mind.ferry_client import Sailing
        from mind.telegram_ui import build_seat_map_keyboard

        sail = Sailing(free_seats=(1, 3), taken_seats=(2,))
        rows = build_seat_map_keyboard(sail, 0)["inline_keyboard"]
        first = rows[0]
        self.assertEqual(first[0]["text"], "1")
        self.assertEqual(first[1]["text"], "✕")
        self.assertEqual(first[2]["text"], "3")
        # The taken one leads nowhere.
        self.assertNotIn("2", first[1]["callback_data"])

    def test_a_free_seat_carries_its_sailing_and_its_number(self):
        from mind.ferry_client import Sailing
        from mind.telegram_ui import build_seat_map_keyboard

        sail = Sailing(free_seats=(7,))
        rows = build_seat_map_keyboard(sail, 2)["inline_keyboard"]
        seat = [b for row in rows for b in row if b["text"] == "7"][0]
        self.assertTrue(seat["callback_data"].endswith("2.7"))


class HoldingASeat(unittest.TestCase):
    """The one call in here with a consequence at the other end.

    A seat held is a seat nobody else can buy for the next few minutes, so the
    request is built from the shape RTL's own page sends rather than discovered
    by trying things until one works.
    """

    def sail(self):
        from mind.ferry_client import Sailing

        return Sailing(schedule_id="125034", fare=70, route="R1C5")

    def test_the_seat_travels_with_its_deck(self):
        from mind.ferry_client import reserve_body

        body = reserve_body(self.sail(), 7, "105", "104")
        seat = body["inbound"][0]["seats"][0]
        self.assertEqual(seat, {"deckCode": "1", "seatNumber": 7})

    def test_the_sailing_is_named_by_its_schedule(self):
        from mind.ferry_client import reserve_body

        self.assertEqual(reserve_body(self.sail(), 7, "105", "104")["inbound"][0]["scheduleId"], "125034")

    def test_a_one_way_carries_no_outbound(self):
        from mind.ferry_client import reserve_body

        self.assertIsNone(reserve_body(self.sail(), 7, "105", "104")["outbound"])

    def test_the_price_is_the_sailings_own(self):
        from mind.ferry_client import reserve_body

        self.assertEqual(reserve_body(self.sail(), 7, "105", "104")["totalPrice"], 70)

    def test_a_reply_without_a_booking_is_a_failure_not_a_hold(self):
        # The worst outcome would be reporting a seat held when it is not.
        from mind.ferry_client import FerryError, parse_reservation

        with self.assertRaises(FerryError):
            parse_reservation({"message": "Seat already taken"})

    def test_a_held_seat_reports_its_booking(self):
        from mind.ferry_client import parse_reservation

        held = parse_reservation({"bookingId": "00000014B8E4", "totalPrice": 70})
        self.assertTrue(held.held)
        self.assertEqual(held.booking_id, "00000014B8E4")

    def test_the_refusal_rtl_gives_is_the_one_reported(self):
        from mind.ferry_client import FerryError, parse_reservation

        with self.assertRaises(FerryError) as caught:
            parse_reservation({"message": "Ticket mode is not found in request"})
        self.assertIn("Ticket mode", str(caught.exception))


class WhoTravels(unittest.TestCase):
    """Passenger details and payment are one call at RTL's end, not two."""

    def parts(self):
        from mind.ferry_client import Contact, Passenger, Sailing

        return (
            Sailing(schedule_id="125034", fare=70, route="R1C5"),
            Passenger(name="Mohamed Maazinu", id_number="A375667", id_type="2"),
            Contact(name="Adam", email="a@example.com", phone="9194744"),
        )

    def test_the_passenger_is_described_with_their_seat(self):
        from mind.ferry_client import payment_body

        sail, passenger, contact = self.parts()
        body = payment_body("00000014B909", sail, 3, passenger, contact)
        rider = body["inbound"][0]["selectedSeats"][0]
        self.assertEqual(rider["customerName"], "Mohamed Maazinu")
        self.assertEqual(rider["customerId"], "A375667")
        self.assertEqual(rider["seatNumber"], 3)
        self.assertEqual(rider["isPrimary"], 1)

    def test_the_person_and_the_seat_are_one_entry(self):
        # RTL calls it selectedSeats and puts the passenger in it. Splitting
        # them into a seat list and a passenger list is refused with
        # "seat.list.not.found", which names neither of the two real problems.
        from mind.ferry_client import payment_body

        sail, passenger, contact = self.parts()
        inbound = payment_body("00000014B909", sail, 3, passenger, contact)["inbound"][0]
        self.assertIn("selectedSeats", inbound)
        self.assertEqual(inbound["selectedSeats"][0]["customerCategoryId"], "2")

    def test_the_card_is_never_kept(self):
        from mind.ferry_client import payment_body

        sail, passenger, contact = self.parts()
        body = payment_body("00000014B909", sail, 3, passenger, contact)
        self.assertEqual(body["tokenize"], 0)
        self.assertIsNone(body["cardId"])

    def test_the_booking_being_paid_for_is_named(self):
        from mind.ferry_client import payment_body

        sail, passenger, contact = self.parts()
        self.assertEqual(payment_body("00000014B909", sail, 3, passenger, contact)["bookingId"], "00000014B909")

    def test_the_ticket_goes_to_the_contact_not_the_passenger(self):
        from mind.ferry_client import payment_body

        sail, passenger, contact = self.parts()
        body = payment_body("00000014B909", sail, 3, passenger, contact)
        self.assertEqual(body["customerEmail"], "a@example.com")

    def test_no_passenger_is_refused_before_rtl_is_asked(self):
        from mind.ferry_client import Contact, FerryError, Passenger, payment_body

        sail, _, contact = self.parts()
        with self.assertRaises(FerryError):
            payment_body("00000014B909", sail, 3, Passenger(name="X"), contact)
        with self.assertRaises(FerryError):
            payment_body("", sail, 3, Passenger(name="X", id_number="A1"), contact)

    def test_no_card_details_are_in_the_request(self):
        # Mind builds this and never handles a card; the bank page does. The
        # request does carry a cardId, which is a reference to a card already
        # saved at RTL rather than a card - and Mind always sends it empty.
        from mind.ferry_client import payment_body

        sail, passenger, contact = self.parts()
        body = payment_body("00000014B909", sail, 3, passenger, contact)
        self.assertIsNone(body["cardId"])
        flat = json.dumps(body).lower()
        # Not "pan": it hides inside isAccompanied, and a test that fails on
        # a word rather than a field teaches nothing.
        for word in ("cvv", "expiry", "cardnumber", "cvc", "securitycode"):
            self.assertNotIn(word, flat)

    def test_the_bank_page_is_found_whatever_rtl_calls_it(self):
        from mind.ferry_client import parse_payment

        self.assertEqual(parse_payment({"paymentUrl": "https://bank/x"}), "https://bank/x")
        self.assertEqual(parse_payment({"data": {"redirectUrl": "https://bank/y"}}), "https://bank/y")

    def test_no_link_is_a_failure_with_rtls_words(self):
        from mind.ferry_client import FerryError, parse_payment

        with self.assertRaises(FerryError) as caught:
            parse_payment({"message": "Booking already paid"})
        self.assertIn("already paid", str(caught.exception))
