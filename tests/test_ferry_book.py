"""The book: who travels, where they go, and what was booked before.

Typing a national ID into a phone before a hold runs out is the worst part of
booking, so the point of all this is that it is a tap instead. What must not
happen is the tap being wrong: the wrong person on a ticket is worse than
typing the right one.

The rules with teeth are in People - somebody is matched by their ID and never
by their name, because two people share a name and nobody shares an ID - and
in Bookings, where the ticket that arrived by email is matched back to the
booking it belongs to.
"""

import tempfile
import unittest
from pathlib import Path

from mind.ferry_book import (
    Booking,
    Route,
    Traveller,
    booking_by_reference,
    booking_from,
    booking_to,
    decode,
    encode,
    find_travellers,
    forget_traveller,
    order_routes,
    order_travellers,
    recent_bookings,
    remember_booking,
    remember_route,
    remember_traveller,
    route_from,
    route_to,
    ticket_file,
    top_routes,
    traveller_from,
    traveller_to,
)


class Stop:
    """What the island picker deals in, as far as this cares."""

    def __init__(self, code, name):
        self.code = code
        self.name = name


NAIVAADHOO = Stop("105", "Naivaadhoo")
KULHUDHUFFUSHI = Stop("106", "Kulhudhuffushi")
BAARAH = Stop("115", "Baarah")


class People(unittest.TestCase):
    def test_somebody_new_is_added(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        self.assertEqual(len(book), 1)
        self.assertEqual(book[0].name, "Mohamed Maazinu")
        self.assertEqual(book[0].used, 1)

    def test_the_same_person_is_counted_not_duplicated(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        book = remember_traveller(book, "Mohamed Maazinu", "A375667", now=200)
        self.assertEqual(len(book), 1)
        self.assertEqual(book[0].used, 2)

    def test_matching_is_by_id_and_not_by_name(self):
        # Two people called Mohamed are two people.
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        book = remember_traveller(book, "Mohamed Ali", "A111222", now=200)
        self.assertEqual(len(book), 2)

    def test_an_id_typed_in_lower_case_is_the_same_person(self):
        book = remember_traveller([], "Mohamed Maazinu", "a375667", now=100)
        book = remember_traveller(book, "Mohamed Maazinu", "A375667", now=200)
        self.assertEqual(len(book), 1)
        self.assertEqual(book[0].number, "A375667")

    def test_a_respelled_name_replaces_the_old_one(self):
        # The newer spelling is the one just used on a ticket.
        book = remember_traveller([], "mohamed maazinu", "A375667", now=100)
        book = remember_traveller(book, "Mohamed Maazinu", "A375667", now=200)
        self.assertEqual(book[0].name, "Mohamed Maazinu")

    def test_nothing_is_saved_without_both_halves(self):
        self.assertEqual(remember_traveller([], "", "A375667"), [])
        self.assertEqual(remember_traveller([], "Mohamed", ""), [])

    def test_the_most_travelled_come_first(self):
        book = remember_traveller([], "Rare", "A000001", now=100)
        book = remember_traveller(book, "Often", "A000002", now=100)
        book = remember_traveller(book, "Often", "A000002", now=200)
        self.assertEqual([who.name for who in order_travellers(book)], ["Often", "Rare"])

    def test_searching_by_part_of_a_name(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        book = remember_traveller(book, "Aishath Ali", "A111222", now=100)
        self.assertEqual([w.name for w in find_travellers(book, "maaz")], ["Mohamed Maazinu"])

    def test_searching_by_part_of_an_id(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        book = remember_traveller(book, "Aishath Ali", "A111222", now=100)
        self.assertEqual([w.name for w in find_travellers(book, "1122")], ["Aishath Ali"])

    def test_an_empty_search_is_everybody(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        self.assertEqual(len(find_travellers(book, "")), 1)

    def test_a_search_matching_nobody_is_empty(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        self.assertEqual(find_travellers(book, "zzz"), [])

    def test_forgetting_somebody(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        book = remember_traveller(book, "Aishath Ali", "A111222", now=100)
        self.assertEqual([w.name for w in forget_traveller(book, "A375667")], ["Aishath Ali"])

    def test_the_label_tells_two_people_apart(self):
        who = Traveller(name="Mohamed", number="A375667")
        self.assertIn("Mohamed", who.label)
        self.assertIn("5667", who.label)


class Routes(unittest.TestCase):
    def test_a_journey_is_remembered(self):
        routes = remember_route([], NAIVAADHOO, KULHUDHUFFUSHI, now=100)
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].label, "Naivaadhoo → Kulhudhuffushi")

    def test_the_same_journey_is_counted(self):
        routes = remember_route([], NAIVAADHOO, KULHUDHUFFUSHI, now=100)
        routes = remember_route(routes, NAIVAADHOO, KULHUDHUFFUSHI, now=200)
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].used, 2)

    def test_the_other_direction_is_its_own_journey(self):
        routes = remember_route([], NAIVAADHOO, KULHUDHUFFUSHI, now=100)
        routes = remember_route(routes, KULHUDHUFFUSHI, NAIVAADHOO, now=200)
        self.assertEqual(len(routes), 2)

    def test_a_journey_to_where_you_already_are_is_not_a_journey(self):
        self.assertEqual(remember_route([], NAIVAADHOO, NAIVAADHOO, now=100), [])

    def test_the_most_travelled_route_comes_first(self):
        routes = remember_route([], NAIVAADHOO, BAARAH, now=100)
        routes = remember_route(routes, NAIVAADHOO, KULHUDHUFFUSHI, now=100)
        routes = remember_route(routes, NAIVAADHOO, KULHUDHUFFUSHI, now=200)
        self.assertEqual(order_routes(routes)[0].to_name, "Kulhudhuffushi")

    def test_the_way_home_is_offered_after_one_journey(self):
        # Nobody has booked it yet, and everybody wants it.
        routes = remember_route([], NAIVAADHOO, KULHUDHUFFUSHI, now=100)
        offered = top_routes(routes, 4)
        self.assertEqual(
            [r.label for r in offered],
            ["Naivaadhoo → Kulhudhuffushi", "Kulhudhuffushi → Naivaadhoo"],
        )

    def test_the_way_home_is_not_offered_twice(self):
        routes = remember_route([], NAIVAADHOO, KULHUDHUFFUSHI, now=100)
        routes = remember_route(routes, KULHUDHUFFUSHI, NAIVAADHOO, now=100)
        offered = top_routes(routes, 4)
        self.assertEqual(len(offered), 2)


class Bookings(unittest.TestCase):
    def entry(self, reference="00000014B8A0", made=100.0):
        return Booking(
            reference=reference,
            from_name="Naivaadhoo",
            to_name="Baarah",
            fare=170.0,
            made=made,
            from_code="105",
            to_code="115",
        )

    def test_newest_first(self):
        history = remember_booking([], self.entry("AAA", made=100))
        history = remember_booking(history, self.entry("BBB", made=200))
        self.assertEqual([b.reference for b in history], ["BBB", "AAA"])

    def test_the_same_reference_replaces_rather_than_piles_up(self):
        history = remember_booking([], self.entry("AAA", made=100))
        history = remember_booking(history, self.entry("AAA", made=200))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].made, 200)

    def test_a_return_says_so_in_its_label(self):
        booking = Booking(reference="A", from_name="X", to_name="Y", returning=True)
        self.assertIn("⇄", booking.label)

    def test_finding_one_by_reference(self):
        history = remember_booking([], self.entry("AAA"))
        self.assertIsNotNone(booking_by_reference(history, "aaa"))
        self.assertIsNone(booking_by_reference(history, "zzz"))

    def test_only_the_recent_ones_are_offered(self):
        history = []
        for n in range(10):
            history = remember_booking(history, self.entry(f"REF{n}", made=n))
        self.assertEqual(len(recent_bookings(history, 3)), 3)


class Tickets(unittest.TestCase):
    """The PDF the mail watcher saved, matched back to its booking."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_the_ticket_is_found_by_its_reference(self):
        # MTCC names the attachment after the booking, which is what makes
        # handing it back later possible at all.
        (self.folder / "MTCC-DOC-00000014B8A0-100756.pdf").write_bytes(b"%PDF")
        found = ticket_file(self.folder, "00000014B8A0")
        self.assertIsNotNone(found)
        self.assertTrue(found.name.startswith("MTCC-DOC"))

    def test_a_reference_in_lower_case_still_finds_it(self):
        (self.folder / "MTCC-DOC-00000014B8A0.pdf").write_bytes(b"%PDF")
        self.assertIsNotNone(ticket_file(self.folder, "00000014b8a0"))

    def test_another_booking_is_not_returned(self):
        (self.folder / "MTCC-DOC-00000014B8A0.pdf").write_bytes(b"%PDF")
        self.assertIsNone(ticket_file(self.folder, "00000014B999"))

    def test_nothing_saved_is_not_an_error(self):
        self.assertIsNone(ticket_file(self.folder, "00000014B8A0"))
        self.assertIsNone(ticket_file(self.folder, ""))
        self.assertIsNone(ticket_file(None, "00000014B8A0"))


class Storage(unittest.TestCase):
    def test_people_survive_a_round_trip(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        back = decode(encode(book, traveller_to), traveller_from)
        self.assertEqual(back[0].name, "Mohamed Maazinu")
        self.assertEqual(back[0].number, "A375667")

    def test_routes_survive_a_round_trip(self):
        routes = remember_route([], NAIVAADHOO, KULHUDHUFFUSHI, now=100)
        back = decode(encode(routes, route_to), route_from)
        self.assertEqual(back[0].label, "Naivaadhoo → Kulhudhuffushi")

    def test_bookings_survive_a_round_trip(self):
        history = remember_booking([], Booking(reference="AAA", from_name="X", to_name="Y"))
        back = decode(encode(history, booking_to), booking_from)
        self.assertEqual(back[0].reference, "AAA")

    def test_rubbish_decodes_to_nothing_rather_than_raising(self):
        # A settings file edited by hand, or a half-written one.
        self.assertEqual(decode("not json", traveller_from), [])
        self.assertEqual(decode("", traveller_from), [])
        self.assertEqual(decode('{"not": "a list"}', traveller_from), [])
        self.assertEqual(decode('[{"name": "no id"}]', traveller_from), [])


class EncryptedStorage(unittest.TestCase):
    """The book holds national IDs, so it is not kept in readable JSON."""

    def setUp(self):
        from mind.config_store import ConfigStore

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")

    def test_people_are_stored_and_read_back(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        config = self.store.set_ferry_travellers(self.store.load(), book)
        back = self.store.get_ferry_travellers(config)
        self.assertEqual(back[0].number, "A375667")

    def test_an_id_is_not_readable_in_the_settings(self):
        book = remember_traveller([], "Mohamed Maazinu", "A375667", now=100)
        config = self.store.set_ferry_travellers(self.store.load(), book)
        self.assertNotIn("A375667", str(config))
        self.assertNotIn("Maazinu", str(config))

    def test_history_is_not_readable_either(self):
        history = remember_booking(
            [], Booking(reference="AAA", from_name="Naivaadhoo", to_name="Baarah",
                        who="Mohamed Maazinu")
        )
        config = self.store.set_ferry_history(self.store.load(), history)
        self.assertNotIn("Maazinu", str(config))
        self.assertEqual(self.store.get_ferry_history(config)[0].reference, "AAA")

    def test_routes_are_plain_because_islands_are_not_personal(self):
        routes = remember_route([], NAIVAADHOO, KULHUDHUFFUSHI, now=100)
        config = self.store.set_ferry_routes(self.store.load(), routes)
        self.assertIn("Naivaadhoo", str(config))
        self.assertEqual(self.store.get_ferry_routes(config)[0].to_name, "Kulhudhuffushi")

    def test_clearing_the_book_empties_it(self):
        config = self.store.set_ferry_travellers(self.store.load(), [])
        self.assertEqual(self.store.get_ferry_travellers(config), [])


if __name__ == "__main__":
    unittest.main()


class OnePersonsJourneys(unittest.TestCase):
    """Finding where somebody has been, to book it again for them."""

    def trip(self, reference, who, numbers="", made=100.0, to="Baarah"):
        return Booking(
            reference=reference,
            from_name="Naivaadhoo",
            to_name=to,
            who=who,
            numbers=numbers,
            made=made,
            from_code="105",
            to_code="115",
        )

    def maazinu(self):
        return Traveller(name="Mohamed Maazinu", number="A375667")

    def test_their_own_journeys_come_back(self):
        from mind.ferry_book import bookings_for

        history = [
            self.trip("AAA", "Mohamed Maazinu", "A375667"),
            self.trip("BBB", "Aishath Ali", "A111222"),
        ]
        found = bookings_for(history, self.maazinu())
        self.assertEqual([b.reference for b in found], ["AAA"])

    def test_matched_on_the_number_rather_than_the_name(self):
        from mind.ferry_book import bookings_for

        # Somebody else with the same name is not the same person.
        history = [self.trip("AAA", "Mohamed Maazinu", "A999999")]
        self.assertEqual(bookings_for(history, self.maazinu()), [])

    def test_older_bookings_without_numbers_fall_back_to_the_name(self):
        # Bookings made before the number was kept are still the ones somebody
        # is looking for; refusing to show them looks like the feature is broken.
        from mind.ferry_book import bookings_for

        history = [self.trip("AAA", "Mohamed Maazinu")]
        self.assertEqual([b.reference for b in bookings_for(history, self.maazinu())], ["AAA"])

    def test_one_of_two_passengers_still_counts_as_theirs(self):
        from mind.ferry_book import bookings_for

        history = [self.trip("AAA", "Aishath Ali, Mohamed Maazinu", "A111222, A375667")]
        self.assertEqual(len(bookings_for(history, self.maazinu())), 1)

    def test_newest_first(self):
        from mind.ferry_book import bookings_for

        history = [
            self.trip("OLD", "Mohamed Maazinu", "A375667", made=100),
            self.trip("NEW", "Mohamed Maazinu", "A375667", made=200),
        ]
        self.assertEqual([b.reference for b in bookings_for(history, self.maazinu())][0], "NEW")

    def test_nobody_is_not_an_error(self):
        from mind.ferry_book import bookings_for

        self.assertEqual(bookings_for([], self.maazinu()), [])
        self.assertEqual(bookings_for(None, None), [])


class WhoWasOnIt(unittest.TestCase):
    """Repeating a booking for the people who were actually on it."""

    def book(self):
        return [
            Traveller(name="Mohamed Maazinu", number="A375667"),
            Traveller(name="Aishath Ali", number="A111222"),
        ]

    def test_resolved_by_number(self):
        from mind.ferry_book import travellers_in

        booking = Booking(reference="A", from_name="X", to_name="Y",
                          who="Mohamed Maazinu", numbers="A375667")
        self.assertEqual([w.name for w in travellers_in(booking, self.book())],
                         ["Mohamed Maazinu"])

    def test_resolved_by_name_when_no_number_was_kept(self):
        from mind.ferry_book import travellers_in

        booking = Booking(reference="A", from_name="X", to_name="Y", who="Aishath Ali")
        self.assertEqual([w.name for w in travellers_in(booking, self.book())], ["Aishath Ali"])

    def test_both_passengers_come_back(self):
        from mind.ferry_book import travellers_in

        booking = Booking(reference="A", from_name="X", to_name="Y",
                          who="Mohamed Maazinu, Aishath Ali",
                          numbers="A375667, A111222")
        self.assertEqual(len(travellers_in(booking, self.book())), 2)

    def test_somebody_no_longer_in_the_book_is_left_out(self):
        # Left out rather than guessed at: a booking repeated for the wrong
        # person is the one failure worth being careful about.
        from mind.ferry_book import travellers_in

        booking = Booking(reference="A", from_name="X", to_name="Y",
                          who="Gone Away", numbers="A000000")
        self.assertEqual(travellers_in(booking, self.book()), [])

    def test_the_numbers_survive_a_round_trip(self):
        booking = Booking(reference="A", from_name="X", to_name="Y", numbers="A375667")
        back = decode(encode([booking], booking_to), booking_from)
        self.assertEqual(back[0].numbers, "A375667")
