"""Printing files that arrive over Telegram.

Printing is physical: paper and ink are spent, on a machine the person tapping
the button may not be near. So the rules held here are about not surprising them
- nothing is offered that cannot work, nothing prints without three explicit
choices, and a setting that could not be applied is reported rather than ignored.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mind import telegram_print
from mind.config_store import ConfigStore
from mind.telegram_bridge import TelegramBridge
from mind.telegram_print import (
    MAX_PRINT_BYTES,
    PAPERS,
    COLOUR_MODES,
    STRATEGY_IMAGE,
    STRATEGY_TEXT,
    STRATEGY_VERB,
    PrintError,
    PrintJob,
    Printer,
    MAX_COPIES,
    SIDES_BOTH,
    SIDES_ONE,
    describe,
    is_printable,
    paper_by_key,
    colour_by_key,
    refusal_for,
    strategy_for,
    warnings_from,
)
from mind.telegram_ui import (
    CB_PRINT,
    PRINT_CANCEL,
    PRINT_PAPER,
    PRINT_PRINTER,
    PRINT_START,
    PRINT_COLOUR,
    PRINT_COPIES,
    PRINT_GO,
    PRINT_SIDES,
    build_paper_keyboard,
    build_print_offer,
    build_printer_keyboard,
    build_colour_keyboard,
    parse_print_callback,
    print_callback,
)

from tests.test_telegram_menu_flow import FakeClient


def one_pixel_png() -> bytes:
    """A real PNG, built here so Windows will actually open it.

    The image path is exercised for real, and GDI+ rejects an approximation, so
    the chunks and their checksums have to be right.
    """
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit RGB
    pixel = zlib.compress(b"\x00\xff\x00\x00")  # one filtered scanline: red
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixel)
        + chunk(b"IEND", b"")
    )


class StrategyTests(unittest.TestCase):
    def test_each_family_is_printed_the_way_windows_can_print_it(self):
        self.assertEqual(strategy_for("a.pdf"), STRATEGY_VERB)
        self.assertEqual(strategy_for("a.docx"), STRATEGY_VERB)
        self.assertEqual(strategy_for("a.xlsx"), STRATEGY_VERB)
        self.assertEqual(strategy_for("a.png"), STRATEGY_IMAGE)
        self.assertEqual(strategy_for("a.txt"), STRATEGY_TEXT)

    def test_the_extension_is_matched_whatever_its_case(self):
        self.assertEqual(strategy_for("HOLIDAY.JPEG"), STRATEGY_IMAGE)
        self.assertEqual(strategy_for("Report.PDF"), STRATEGY_VERB)

    def test_what_windows_cannot_print_is_not_printable(self):
        for name in ("clip.mp4", "archive.zip", "installer.exe", "noextension"):
            self.assertIsNone(strategy_for(name), name)
            self.assertFalse(is_printable(name), name)

    def test_the_refusal_says_what_to_send_instead(self):
        message = refusal_for("clip.mp4", size=10)
        self.assertIn("mp4", message)
        self.assertIn("PDF", message)

    def test_a_printable_file_of_reasonable_size_is_not_refused(self):
        self.assertEqual(refusal_for("a.pdf", size=1024), "")

    def test_something_too_large_to_print_is_refused(self):
        # A physical limit, not a technical one: nobody wants to find out at the
        # printer.
        self.assertIn("too large", refusal_for("a.pdf", size=MAX_PRINT_BYTES + 1))


class ChoiceTests(unittest.TestCase):
    def test_the_lists_are_short_enough_to_be_buttons(self):
        self.assertLessEqual(len(PAPERS), 6)
        self.assertLessEqual(len(COLOUR_MODES), 4)

    def test_keys_are_unique_so_a_choice_is_unambiguous(self):
        self.assertEqual(len({p.key for p in PAPERS}), len(PAPERS))
        self.assertEqual(len({p.key for p in COLOUR_MODES}), len(COLOUR_MODES))

    def test_every_paper_names_a_size_windows_knows(self):
        for paper in PAPERS:
            self.assertTrue(paper.windows_name)
            self.assertTrue(paper.windows_name[0].isupper())

    def test_colour_is_asked_directly_rather_than_inferred(self):
        # Two choices, each saying exactly what it does. The old "document type"
        # step decided colour behind a word that did not mention it.
        self.assertEqual([mode.key for mode in COLOUR_MODES], ["mono", "colour"])
        self.assertFalse(colour_by_key("mono").color)
        self.assertTrue(colour_by_key("colour").color)

    def test_both_choices_say_which_they_are_on_the_button(self):
        labels = " ".join(mode.label.lower() for mode in COLOUR_MODES)
        self.assertIn("black and white", labels)
        self.assertIn("colour", labels)

    def test_an_unknown_key_resolves_to_nothing_rather_than_a_default(self):
        # Falling back to a default would print something nobody chose.
        self.assertIsNone(paper_by_key("a2"))
        self.assertIsNone(colour_by_key("glossy"))


class JobTests(unittest.TestCase):
    def setUp(self):
        self.job = PrintJob(path=Path("a.pdf")).with_printers([Printer("HP", True, True), Printer("Canon")])

    def test_a_job_is_only_complete_once_all_three_are_chosen(self):
        self.assertFalse(self.job.is_complete)
        job = self.job.with_printer(0)
        self.assertFalse(job.is_complete)
        job = job.with_paper(0)
        self.assertFalse(job.is_complete)
        job = job.with_colour(0)
        self.assertTrue(job.is_complete)

    def test_a_button_means_the_printer_whose_name_was_on_it(self):
        self.assertEqual(self.job.with_printer(1).printer, "Canon")

    def test_an_index_outside_the_offered_list_changes_nothing(self):
        # A stale message must not print to a printer the user never saw.
        self.assertEqual(self.job.with_printer(9).printer, "")
        self.assertEqual(self.job.with_paper(99).paper, "")
        self.assertEqual(self.job.with_colour(-1).colour, "")

    def test_the_summary_reads_as_a_sentence(self):
        job = self.job.with_printer(0).with_paper(0).with_colour(0)
        self.assertEqual(
            describe(job), "HP · A4 · black and white · one side · 1 copy"
        )

    def test_an_incomplete_job_is_refused_before_anything_is_spent(self):
        with self.assertRaises(PrintError):
            telegram_print.print_job(self.job)

    def test_a_file_that_has_gone_is_refused(self):
        job = self.job.with_printer(0).with_paper(0).with_colour(0)
        with self.assertRaises(PrintError) as caught:
            telegram_print.print_job(job)
        self.assertIn("no longer there", str(caught.exception))


class ScriptArgumentTests(unittest.TestCase):
    def test_the_choices_reach_the_script_as_windows_names(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "report.pdf"
            target.write_bytes(b"%PDF-1.4")
            job = (
                PrintJob(path=target)
                .with_printers([Printer("HP Photo")])
                .with_printer(0)
                .with_paper(0)
                .with_colour(1)
            )
            with mock.patch.object(
                telegram_print, "_run_script", return_value="printed"
            ) as runner:
                telegram_print.print_job(job)
        arguments = runner.call_args[0][0]
        pairs = dict(zip(arguments[::2], arguments[1::2]))
        self.assertEqual(pairs["-Printer"], "HP Photo")
        self.assertEqual(pairs["-Paper"], "A4")
        self.assertEqual(pairs["-Strategy"], STRATEGY_VERB)
        # Index 1 is the colour choice.
        self.assertEqual(pairs["-Ink"], "colour")

    def test_black_and_white_is_passed_as_a_powershell_false(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "notes.txt"
            target.write_text("hello", encoding="utf-8")
            job = (
                PrintJob(path=target)
                .with_printers([Printer("HP")])
                .with_printer(0)
                .with_paper(1)
                .with_colour(0)
            )
            with mock.patch.object(
                telegram_print, "_run_script", return_value="printed"
            ) as runner:
                telegram_print.print_job(job)
        pairs = dict(zip(runner.call_args[0][0][::2], runner.call_args[0][0][1::2]))
        self.assertEqual(pairs["-Ink"], "mono")
        self.assertEqual(pairs["-Paper"], "Letter")
        self.assertEqual(pairs["-Strategy"], STRATEGY_TEXT)

    def test_warnings_are_collected_and_read_as_sentences(self):
        output = (
            "warning: changing the paper size for this file type needs "
            "administrator rights, so the printer's own setting was used.\nprinted"
        )
        notes = warnings_from(output)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Changing the paper size"))

    def test_a_clean_run_has_nothing_to_report(self):
        self.assertEqual(warnings_from("printed"), [])


@unittest.skipUnless(sys.platform == "win32", "the print helper is Windows-only")
class ScriptBindingTests(unittest.TestCase):
    """Run the real script, because arguments that look right can still not bind.

    Every argument reaches a -File script as text. Passing "$true" to a [bool]
    parameter fails before the script runs, and no test that only inspected the
    argument list could see it - which is exactly how printing shipped broken
    while its unit tests passed. These call PowerShell for real and stop short of
    printing: once at a file that is not there, and once at a printer that does
    not exist. Reaching either message proves the arguments bound.
    """

    def build(self, path: Path, printer: str, colour: bool) -> PrintJob:
        return (
            PrintJob(path=path)
            .with_printers([Printer(printer, True, False)])
            .with_printer(0)
            .with_paper(0)
            .with_colour(1 if colour else 0)
        )

    def test_the_arguments_bind_for_both_colour_choices(self):
        missing = Path(tempfile.gettempdir()) / "mind-not-here-9d4f2.pdf"
        for colour in (True, False):
            with self.assertRaises(PrintError) as caught:
                telegram_print.print_job(self.build(missing, "Some Printer", colour), timeout=90)
            message = str(caught.exception)
            self.assertNotIn("argument transformation", message)
            self.assertNotIn("Cannot convert", message)
            self.assertIn("no longer there", message)

    def test_an_image_reaches_the_printer_stage(self):
        # Past binding, past the strategy switch, past loading the picture: the
        # only thing left is the printer, which is why a bogus one is used.
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "probe.png"
            image.write_bytes(one_pixel_png())
            with self.assertRaises(PrintError) as caught:
                telegram_print.print_job(
                    self.build(image, "Mind No Such Printer", True), timeout=90
                )
        message = str(caught.exception)
        self.assertNotIn("argument transformation", message)
        self.assertIn("printer", message.lower())

    def test_listing_printers_works_against_the_real_script(self):
        # The same script, the other entry point; proves it is present and runs.
        self.assertIsInstance(telegram_print.printers(timeout=60), list)


class PrintKeyboardTests(unittest.TestCase):
    def buttons(self, markup):
        return [b for row in markup["inline_keyboard"] for b in row]

    def test_the_offer_is_one_button(self):
        markup = build_print_offer("PDF")
        self.assertEqual(len(self.buttons(markup)), 1)
        self.assertIn("Print", self.buttons(markup)[0]["text"])

    def test_the_default_printer_is_marked_and_comes_first(self):
        markup = build_printer_keyboard([Printer("HP", True), Printer("Canon")])
        self.assertIn("★", self.buttons(markup)[0]["text"])
        self.assertNotIn("★", self.buttons(markup)[1]["text"])

    def test_a_long_printer_name_still_fits_a_button(self):
        markup = build_printer_keyboard([Printer("X" * 120)])
        self.assertLess(len(self.buttons(markup)[0]["text"]), 40)

    def test_only_as_many_printers_as_fit_are_offered(self):
        markup = build_printer_keyboard([Printer(f"printer {i}") for i in range(20)])
        printer_buttons = [
            b for b in self.buttons(markup) if b["callback_data"].endswith(tuple("01234567"))
        ]
        self.assertLessEqual(len(printer_buttons), 8)

    def test_every_step_can_be_cancelled(self):
        for markup in (
            build_printer_keyboard([Printer("HP")]),
            build_paper_keyboard(PAPERS),
            build_colour_keyboard(COLOUR_MODES),
        ):
            self.assertTrue(
                any("Cancel" in b["text"] for b in self.buttons(markup)),
                markup,
            )

    def test_paper_sizes_share_rows_and_types_do_not(self):
        paper_rows = build_paper_keyboard(PAPERS)["inline_keyboard"]
        self.assertTrue(any(len(row) > 1 for row in paper_rows))
        type_rows = build_colour_keyboard(COLOUR_MODES)["inline_keyboard"][:-1]
        self.assertTrue(all(len(row) == 1 for row in type_rows))

    def test_payloads_round_trip_and_stay_tiny(self):
        for step in (PRINT_START, PRINT_PRINTER, PRINT_PAPER, PRINT_COLOUR, PRINT_CANCEL):
            self.assertEqual(parse_print_callback(print_callback(step, 3)), (step, 3))
            self.assertEqual(parse_print_callback(print_callback(step)), (step, None))
            self.assertLessEqual(len(print_callback(step, 7).encode("utf-8")), 64)

    def test_malformed_payloads_do_not_raise(self):
        self.assertEqual(parse_print_callback(f"{CB_PRINT}:rX"), ("r", None))
        self.assertEqual(parse_print_callback(""), ("", None))
        self.assertEqual(parse_print_callback(CB_PRINT), ("", None))


class PrintFlowTests(unittest.TestCase):
    """The three questions, as the chat walks through them."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = ConfigStore(root=root / "config")
        self.bridge = TelegramBridge(self.store)
        self.client = FakeClient()
        self.file = root / "report.pdf"
        self.file.write_bytes(b"%PDF-1.4")
        self.config = {"telegram_files_enabled": True, "telegram_print_enabled": True}
        self.printed: list[PrintJob] = []

    def offer(self) -> int:
        self.bridge._offer_printing(
            self.client, 7, self.file, "Saved report.pdf", self.config
        )
        return self.client.sent[-1]["id"]

    def record(self, job: PrintJob) -> list[str]:
        """Stand in for the printer, keeping what it was asked to print."""
        self.printed.append(job)
        return []

    def tap(self, message_id: int, step: str, value: int | None = None) -> None:
        with mock.patch(
            "mind.telegram_bridge.printers",
            return_value=[Printer("HP", True, True), Printer("Canon")],
        ), mock.patch("mind.telegram_bridge.print_job", side_effect=self.record):
            self.bridge._handle_print_tap(
                self.client,
                7,
                "cb",
                message_id,
                print_callback(step, value),
                self.config,
            )

    def walk(self) -> int:
        message = self.offer()
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_PRINTER, 1)
        self.tap(message, PRINT_PAPER, 0)
        self.tap(message, PRINT_COLOUR, 0)
        self.tap(message, PRINT_GO)
        return message

    def test_a_printable_file_is_offered_with_a_button(self):
        self.offer()
        self.assertIsNotNone(self.client.sent[-1]["markup"])

    def test_a_file_windows_cannot_print_gets_no_button(self):
        # Better than a button whose only purpose is to explain itself.
        video = Path(self.temp.name) / "clip.mp4"
        video.write_bytes(b"x")
        self.bridge._offer_printing(self.client, 7, video, "Saved clip.mp4", self.config)
        self.assertIsNone(self.client.sent[-1]["markup"])

    def test_nothing_is_offered_when_printing_is_switched_off(self):
        self.bridge._offer_printing(
            self.client, 7, self.file, "Saved report.pdf", {"telegram_files_enabled": True}
        )
        self.assertIsNone(self.client.sent[-1]["markup"])

    def test_three_taps_print_the_file_with_what_was_chosen(self):
        self.walk()
        self.assertEqual(len(self.printed), 1)
        job = self.printed[0]
        self.assertEqual(job.printer, "Canon")
        self.assertEqual(job.paper, PAPERS[0].key)
        self.assertEqual(job.colour, COLOUR_MODES[0].key)
        self.assertEqual(job.path, self.file)

    def test_the_whole_flow_stays_in_one_message(self):
        message = self.walk()
        self.assertEqual(len(self.client.sent), 1)
        self.assertTrue(all(edit["id"] == message for edit in self.client.edited))

    def test_nothing_prints_before_the_last_tap(self):
        message = self.offer()
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_PRINTER, 0)
        self.tap(message, PRINT_PAPER, 0)
        self.assertEqual(self.printed, [])

    def test_each_question_is_asked_in_turn(self):
        message = self.offer()
        self.tap(message, PRINT_START)
        self.assertIn("Which printer", self.client.edited[-1]["text"])
        self.tap(message, PRINT_PRINTER, 0)
        self.assertIn("Which paper", self.client.edited[-1]["text"])
        self.tap(message, PRINT_PAPER, 0)
        self.assertIn("Colour or black and white", self.client.edited[-1]["text"])

    def test_the_last_question_is_colour_and_either_answer_prints(self):
        for index, expected in ((0, "mono"), (1, "colour")):
            self.printed.clear()
            self.client = FakeClient()
            message = self.offer()
            self.tap(message, PRINT_START)
            self.tap(message, PRINT_PRINTER, 0)
            self.tap(message, PRINT_PAPER, 0)
            self.assertIn("Colour or black and white", self.client.edited[-1]["text"])
            self.tap(message, PRINT_COLOUR, index)
            self.tap(message, PRINT_GO)
            self.assertEqual(self.printed[0].colour, expected)

    def test_the_result_says_which_colour_was_used(self):
        self.walk()
        self.assertIn("black and white", self.client.edited[-1]["text"])

    def test_a_pdf_is_warned_that_colour_may_not_stick(self):
        # Windows only lets an administrator apply it for these formats, and
        # learning that at the printer is worse than reading it here.
        message = self.offer()
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_PRINTER, 0)
        self.tap(message, PRINT_PAPER, 0)
        self.assertIn("administrator", self.client.edited[-1]["text"])

    def test_an_image_is_not_warned_because_it_is_applied_per_job(self):
        image = Path(self.temp.name) / "scan.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.bridge._offer_printing(self.client, 7, image, "Saved scan.png", self.config)
        message = self.client.sent[-1]["id"]
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_PRINTER, 0)
        self.tap(message, PRINT_PAPER, 0)
        self.assertNotIn("administrator", self.client.edited[-1]["text"])

    def summary(self, message: int) -> None:
        """Walk as far as the last panel, where sides and copies live."""
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_PRINTER, 0)
        self.tap(message, PRINT_PAPER, 0)
        self.tap(message, PRINT_COLOUR, 0)

    def test_the_panel_defaults_to_one_side_and_one_copy(self):
        # The common print, so it needs no taps of its own.
        message = self.offer()
        self.summary(message)
        self.tap(message, PRINT_GO)
        self.assertEqual(self.printed[0].sides, SIDES_ONE)
        self.assertEqual(self.printed[0].copies, 1)

    def test_copies_go_up_and_down_from_the_panel(self):
        message = self.offer()
        self.summary(message)
        self.tap(message, PRINT_COPIES, 3)
        self.assertIn("3 copies", str(self.client.edited[-1]["markup"]))
        self.tap(message, PRINT_COPIES, 2)
        self.tap(message, PRINT_GO)
        self.assertEqual(self.printed[0].copies, 2)

    def test_copies_stop_at_the_ends_rather_than_erroring(self):
        message = self.offer()
        self.summary(message)
        self.tap(message, PRINT_COPIES, 0)
        self.tap(message, PRINT_GO)
        self.assertEqual(self.printed[0].copies, 1)
        self.printed.clear()
        message = self.offer()
        self.summary(message)
        self.tap(message, PRINT_COPIES, MAX_COPIES + 5)
        self.tap(message, PRINT_GO)
        self.assertEqual(self.printed[0].copies, MAX_COPIES)

    def test_both_sides_can_be_chosen_on_a_printer_that_does_it(self):
        message = self.offer()
        self.summary(message)
        self.assertIn("Both sides", str(self.client.edited[-1]["markup"]))
        self.tap(message, PRINT_SIDES, 1)
        self.tap(message, PRINT_GO)
        self.assertEqual(self.printed[0].sides, SIDES_BOTH)

    def test_a_printer_that_cannot_duplex_is_not_offered_both_sides(self):
        # A button that quietly does nothing is worse than no button, and the
        # panel says why instead of leaving the user hunting for the option.
        message = self.offer()
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_PRINTER, 1)  # Canon: duplex=False in the fixture
        self.tap(message, PRINT_PAPER, 0)
        self.tap(message, PRINT_COLOUR, 0)
        self.assertNotIn("Both sides", str(self.client.edited[-1]["markup"]))
        self.assertIn("one side only", self.client.edited[-1]["text"])

    def test_both_sides_is_dropped_when_the_printer_changes_to_one_that_cannot(self):
        message = self.offer()
        self.summary(message)
        self.tap(message, PRINT_SIDES, 1)
        self.tap(message, PRINT_PRINTER, 1)
        self.tap(message, PRINT_PAPER, 0)
        self.tap(message, PRINT_COLOUR, 0)
        self.tap(message, PRINT_GO)
        self.assertEqual(self.printed[0].sides, SIDES_ONE)

    def test_nothing_prints_from_the_panel_until_print_is_tapped(self):
        message = self.offer()
        self.summary(message)
        self.tap(message, PRINT_COPIES, 4)
        self.tap(message, PRINT_SIDES, 1)
        self.assertEqual(self.printed, [])

    def test_the_panel_shows_the_whole_job_before_any_paper_is_spent(self):
        message = self.offer()
        self.summary(message)
        text = self.client.edited[-1]["text"]
        for expected in ("report.pdf", "A4", "black and white", "one side", "1 copy"):
            self.assertIn(expected, text)

    def test_cancelling_prints_nothing_and_says_so(self):
        message = self.offer()
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_CANCEL)
        self.assertEqual(self.printed, [])
        self.assertIn("not printed", self.client.edited[-1]["text"])

    def test_a_cancelled_job_cannot_be_resumed(self):
        message = self.offer()
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_CANCEL)
        self.tap(message, PRINT_COLOUR, 0)
        self.assertEqual(self.printed, [])
        self.assertTrue(any("again" in text for text in self.client.answered))

    def test_a_tap_on_a_message_from_before_a_restart_asks_for_the_file_again(self):
        # The job is remembered in memory, so it cannot survive one.
        self.tap(4321, PRINT_START)
        self.assertEqual(self.printed, [])
        self.assertTrue(any("again" in text for text in self.client.answered))

    def test_printing_switched_off_midway_stops_the_flow(self):
        message = self.offer()
        self.tap(message, PRINT_START)
        self.config["telegram_print_enabled"] = False
        self.tap(message, PRINT_PRINTER, 0)
        self.assertEqual(self.printed, [])
        self.assertTrue(any("switched off" in text for text in self.client.answered))

    def test_two_files_are_arranged_independently(self):
        other = Path(self.temp.name) / "second.pdf"
        other.write_bytes(b"%PDF-1.4")
        first = self.offer()
        self.bridge._offer_printing(self.client, 7, other, "Saved second.pdf", self.config)
        second = self.client.sent[-1]["id"]
        self.assertNotEqual(first, second)
        for message in (first, second):
            self.tap(message, PRINT_START)
            self.tap(message, PRINT_PRINTER, 0)
            self.tap(message, PRINT_PAPER, 0)
            self.tap(message, PRINT_COLOUR, 0)
            self.tap(message, PRINT_GO)
        self.assertEqual([job.path.name for job in self.printed], ["report.pdf", "second.pdf"])

    def test_old_offers_are_forgotten_rather_than_kept_for_ever(self):
        for index in range(20):
            target = Path(self.temp.name) / f"file{index}.pdf"
            target.write_bytes(b"%PDF-1.4")
            self.bridge._offer_printing(self.client, 7, target, "Saved", self.config)
        self.assertLessEqual(len(self.bridge._print_jobs), 12)

    def test_a_failed_print_says_why_and_offers_a_way_on(self):
        message = self.offer()
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_PRINTER, 0)
        self.tap(message, PRINT_PAPER, 0)
        self.tap(message, PRINT_COLOUR, 0)
        with mock.patch(
            "mind.telegram_bridge.print_job", side_effect=PrintError("The printer is offline.")
        ):
            self.bridge._handle_print_tap(
                self.client, 7, "cb", message, print_callback(PRINT_GO), self.config
            )
        self.assertIn("offline", self.client.edited[-1]["text"])
        self.assertIsNotNone(self.client.edited[-1]["markup"])

    def test_an_image_sent_as_a_file_is_kept_and_offered(self):
        # Sending a picture as a file rather than a photo is deliberate: it is a
        # file, so it is saved where the others go and can be printed. It is
        # written from the bytes already downloaded, not fetched a second time.
        self.config["telegram_inbox"] = str(Path(self.temp.name) / "inbox")
        self.bridge._keep_for_printing(
            self.client,
            7,
            {"file_name": "scan.png"},
            b"\x89PNG\r\n\x1a\n",
            self.config,
        )
        saved = Path(self.config["telegram_inbox"]) / "scan.png"
        self.assertTrue(saved.is_file())
        self.assertIsNotNone(self.client.sent[-1]["markup"])

    def test_nothing_is_kept_when_file_access_is_off(self):
        # The inbox belongs to file access; printing must not create one behind it.
        self.config["telegram_files_enabled"] = False
        self.config["telegram_inbox"] = str(Path(self.temp.name) / "inbox2")
        self.bridge._keep_for_printing(
            self.client, 7, {"file_name": "scan.png"}, b"x", self.config
        )
        self.assertFalse(Path(self.config["telegram_inbox"]).exists())
        self.assertEqual(self.client.sent, [])

    def test_a_warning_from_windows_is_shown_with_the_result(self):
        message = self.offer()
        self.tap(message, PRINT_START)
        self.tap(message, PRINT_PRINTER, 0)
        self.tap(message, PRINT_PAPER, 0)
        self.tap(message, PRINT_COLOUR, 0)
        with mock.patch(
            "mind.telegram_bridge.print_job",
            return_value=["Changing the paper size needs administrator rights."],
        ):
            self.bridge._handle_print_tap(
                self.client, 7, "cb", message, print_callback(PRINT_GO), self.config
            )
        text = self.client.edited[-1]["text"]
        self.assertIn("Sent report.pdf", text)
        self.assertIn("administrator rights", text)


if __name__ == "__main__":
    unittest.main()
