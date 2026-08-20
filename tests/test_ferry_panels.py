"""What a journey looks like in the chat, before somebody pays for it.

These panels are the only description of the trip anybody sees until the
ticket arrives, so the things worth protecting are the ones that would send a
person to the wrong jetty: the seat that belongs to each boat, the wait
between two of them, and the total.

The rule with teeth is the last group. A fare multiplied by the number of
passengers is the one figure people check, and a return is priced as a pair
rather than as two fares - so the total is never a sum of the two directions.
"""

import unittest

from mind.ferry_client import Leg, Sailing
from mind.telegram_ui import (
    fare_line,
    itinerary_lines,
    journey_length,
    journey_summary,
    minutes_between,
    passengers_in,
    sailings_text,
    seat_groups_of,
    seat_held_text,
    spell_minutes,
    spell_seats,
)


def leg(depart, arrive, start, end, route="RTL109", free=30):
    return Leg(
        schedule_id="1",
        route=route,
        boat=route,
        departs=depart,
        arrives=arrive,
        from_name=start,
        to_name=end,
        from_code="104",
        to_code="110",
        free_seats=tuple(range(1, free)),
    )


def direct():
    """One boat: Naivaadhoo to Kulhudhuffushi, fifty minutes."""
    return Sailing(
        fare=65.0,
        legs=(leg("20260821083000", "20260821092000", "Naivaadhoo", "Kulhudhuffushi"),),
    )


def three_boats():
    """The real trip: two changes, one of them nearly five hours."""
    return Sailing(
        fare=170.0,
        legs=(
            leg("20260821083000", "20260821092000", "Naivaadhoo", "Kulhudhuffushi", "RTL109"),
            leg("20260821141500", "20260821151000", "Kulhudhuffushi", "Dhihdhoo", "RTL111"),
            leg("20260821151500", "20260821154500", "Dhihdhoo", "Baarah", "RTL110"),
        ),
    )


class Durations(unittest.TestCase):
    def test_minutes_across_a_morning(self):
        self.assertEqual(minutes_between("20260821083000", "20260821092000"), 50)

    def test_minutes_across_midnight(self):
        self.assertEqual(minutes_between("20260821233000", "20260822001500"), 45)

    def test_an_unreadable_stamp_costs_a_duration_not_a_panel(self):
        self.assertEqual(minutes_between("", "20260821092000"), 0)
        self.assertEqual(minutes_between("nonsense", "also nonsense"), 0)
        self.assertEqual(minutes_between(None, None), 0)

    def test_a_time_that_runs_backwards_is_not_negative(self):
        self.assertEqual(minutes_between("20260821092000", "20260821083000"), 0)

    def test_spelling(self):
        self.assertEqual(spell_minutes(50), "50m")
        self.assertEqual(spell_minutes(60), "1h")
        self.assertEqual(spell_minutes(295), "4h 55m")
        self.assertEqual(spell_minutes(0), "0m")

    def test_a_journey_is_measured_door_to_door(self):
        # Waits included: 08:30 to 15:45 is what the day actually costs, not
        # the two hours fifteen actually spent on boats.
        self.assertEqual(journey_length(three_boats()), "7h 15m")


class Seats(unittest.TestCase):
    def test_the_wire_form_is_understood(self):
        self.assertEqual(seat_groups_of("33;3;27"), [["33"], ["3"], ["27"]])
        self.assertEqual(seat_groups_of("11,12"), [["11", "12"]])

    def test_lists_are_understood_too(self):
        self.assertEqual(seat_groups_of([[11, 12]]), [[11, 12]])
        self.assertEqual(seat_groups_of([11, 12]), [[11, 12]])

    def test_a_bare_number_is_one_seat_on_one_boat(self):
        self.assertEqual(seat_groups_of(33), [["33"]])

    def test_passengers_are_counted_per_boat_not_in_total(self):
        # Three boats, one person: the bug this replaced counted three.
        self.assertEqual(passengers_in("33;3;27"), 1)
        self.assertEqual(passengers_in("11,12"), 2)
        self.assertEqual(passengers_in([[11, 12], [5, 6]]), 2)

    def test_the_semicolon_never_reaches_a_person(self):
        spelled = spell_seats("33;3;27")
        self.assertNotIn(";", spelled)
        for number in ("33", "3", "27"):
            self.assertIn(number, spelled)


class Itinerary(unittest.TestCase):
    def test_every_boat_appears_with_its_own_seat(self):
        lines = itinerary_lines(three_boats(), [[33], [3], [27]])
        body = "\n".join(lines)
        self.assertIn("Naivaadhoo", body)
        self.assertIn("Kulhudhuffushi", body)
        self.assertIn("Dhihdhoo", body)
        self.assertIn("Baarah", body)
        for route, seat in (("RTL109", "33"), ("RTL111", "3"), ("RTL110", "27")):
            self.assertIn(route, body)
            self.assertIn(f"seat {seat}", body)

    def test_the_wait_between_boats_is_named(self):
        # Five minutes and nearly five hours look identical in a list of times,
        # and only one of them is worth knowing before paying.
        body = "\n".join(itinerary_lines(three_boats(), [[33], [3], [27]]))
        self.assertIn("4h 55m in Kulhudhuffushi", body)
        self.assertIn("5m in Dhihdhoo", body)

    def test_there_is_no_wait_after_the_last_boat(self):
        body = "\n".join(itinerary_lines(three_boats(), [[33], [3], [27]]))
        self.assertNotIn("in Baarah", body)

    def test_a_direct_trip_needs_no_change_at_all(self):
        body = "\n".join(itinerary_lines(direct(), [[11]]))
        self.assertNotIn("⏳", body)

    def test_it_survives_being_given_no_seats(self):
        # Sailings are listed before anything is picked.
        body = "\n".join(itinerary_lines(three_boats()))
        self.assertIn("RTL109", body)
        self.assertNotIn("seat ", body)


class Fares(unittest.TestCase):
    def test_one_passenger_is_just_the_fare(self):
        line = fare_line(direct(), 1)
        self.assertIn("65", line)
        self.assertNotIn("×", line)

    def test_more_than_one_shows_the_working(self):
        line = fare_line(direct(), 3)
        self.assertIn("195", line)
        self.assertIn("65", line)
        self.assertIn("3 passengers", line)

    def test_a_return_is_not_the_outbound_fare_doubled(self):
        # RTL prices a return as a pair. Dividing the total is right; reading
        # the per-person figure off the outbound sailing is not.
        back = Sailing(
            fare=130.0,
            legs=(leg("20260823160000", "20260823165500", "Kulhudhuffushi", "Naivaadhoo"),),
        )
        line = fare_line(back, 2, total=260.0)
        self.assertIn("260", line)
        self.assertIn("130", line)
        self.assertIn("2 passengers", line)


class Panels(unittest.TestCase):
    def test_the_summary_carries_the_whole_journey(self):
        body = journey_summary(three_boats(), [[33], [3], [27]])
        self.assertIn("7h 15m", body)
        self.assertIn("2 boat changes", body)
        self.assertIn("4h 55m in Kulhudhuffushi", body)
        self.assertIn("170", body)

    def test_a_direct_journey_says_direct(self):
        self.assertIn("direct", journey_summary(direct(), [[11]]))

    def test_the_held_panel_names_every_seat_and_the_booking(self):
        body = seat_held_text(
            "Naivaadhoo", "Baarah", three_boats(), "33;3;27", "00000014B8A0", left=390
        )
        self.assertIn("00000014B8A0", body)
        self.assertIn("6:30", body)
        self.assertIn("Not paid yet", body)
        self.assertNotIn(";", body)

    def test_the_held_panel_is_singular_for_one_seat(self):
        body = seat_held_text("A", "B", direct(), "11", "00000014B8A0", left=390)
        self.assertIn("Seat <b>11</b>", body)

    def test_the_held_panel_is_plural_for_two(self):
        body = seat_held_text("A", "B", direct(), "11,12", "00000014B8A0", left=390)
        self.assertIn("Seats", body)

    def test_the_sailings_list_says_how_long_and_how_many_changes(self):
        body = sailings_text("Naivaadhoo", "Baarah", "Fri 21 Aug", [three_boats()])
        self.assertIn("7h 15m", body)
        self.assertIn("2 changes", body)

    def test_a_direct_sailing_is_marked_direct_in_the_list(self):
        body = sailings_text("A", "B", "Fri 21 Aug", [direct()])
        self.assertIn("direct", body)


if __name__ == "__main__":
    unittest.main()
