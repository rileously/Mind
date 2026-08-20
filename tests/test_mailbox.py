"""Getting the ticket out of a mailbox, without a mailbox.

What is tested is the part that decides what to open and what to write down,
because both are ways this could go wrong in somebody's inbox rather than on
screen. A filename arrives from a stranger and is about to be joined onto a
directory; a sender list decides which messages are downloaded at all.

The rules with teeth are the last two groups: an empty sender list must open
nothing rather than everything, and a filename must never be able to point
outside the folder it is written into.
"""

import unittest
from email.message import EmailMessage

from mind.mail_watch import caption_for, parse_senders, save_attachment
from mind.mailbox import (
    Attachment,
    Credentials,
    attachments_in,
    attachments_of,
    decoded,
    safe_name,
    wanted_file,
    wanted_sender,
)


def ticket_mail(
    sender: str = "RTL Ferry <noreply@rtl.mv>",
    subject: str = "Your ferry ticket",
    filename: str = "ticket.pdf",
    body: bytes = b"%PDF-1.4 pretend",
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message.set_content("Your ticket is attached.")
    message.add_attachment(
        body, maintype="application", subtype="pdf", filename=filename
    )
    return message


class Headers(unittest.TestCase):
    def test_plain_header_survives(self):
        self.assertEqual(decoded("Your ferry ticket"), "Your ferry ticket")

    def test_encoded_header_is_read(self):
        # What Gmail sends when the subject is not plain ASCII.
        self.assertEqual(decoded("=?utf-8?b?VGlja2V0IOKchQ==?="), "Ticket ✅")

    def test_nothing_is_empty_rather_than_none(self):
        self.assertEqual(decoded(None), "")


class WhoIsOpened(unittest.TestCase):
    def test_subdomain_of_a_listed_sender_counts(self):
        self.assertTrue(wanted_sender("tickets@bo.rtl.mv", ("rtl.mv",)))

    def test_display_name_does_not_have_to_be_stripped(self):
        self.assertTrue(wanted_sender("RTL Ferry <noreply@rtl.mv>", ("rtl.mv",)))

    def test_case_is_not_a_way_past_it(self):
        self.assertTrue(wanted_sender("NOREPLY@RTL.MV", ("rtl.mv",)))

    def test_a_stranger_is_not_opened(self):
        self.assertFalse(wanted_sender("someone@example.com", ("rtl.mv",)))

    def test_an_empty_list_opens_nobody(self):
        # The dangerous default. Watching everything is not a sane fallback for
        # having been told to watch nothing.
        self.assertFalse(wanted_sender("noreply@rtl.mv", ()))
        self.assertFalse(wanted_sender("noreply@rtl.mv", ("", "  ")))

    def test_a_message_with_no_sender_is_not_opened(self):
        self.assertFalse(wanted_sender("", ("rtl.mv",)))


class WhichFiles(unittest.TestCase):
    def test_a_pdf_is_wanted_whatever_its_case(self):
        self.assertTrue(wanted_file("Ticket.PDF", (".pdf",)))

    def test_other_attachments_are_left_alone(self):
        self.assertFalse(wanted_file("logo.png", (".pdf",)))
        self.assertFalse(wanted_file("", (".pdf",)))


class Names(unittest.TestCase):
    def test_a_traversing_name_cannot_leave_the_folder(self):
        cleaned = safe_name("../../Windows/System32/evil.pdf")
        self.assertNotIn("/", cleaned)
        self.assertNotIn("..", cleaned)

    def test_a_windows_path_is_flattened_too(self):
        cleaned = safe_name(chr(92).join(["C:", "Users", "evil.pdf"]))
        self.assertNotIn(chr(92), cleaned)
        self.assertNotIn(":", cleaned)

    def test_a_nameless_attachment_still_gets_a_name(self):
        self.assertEqual(safe_name(""), "ticket.pdf")
        self.assertEqual(safe_name("..."), "ticket.pdf")

    def test_an_ordinary_name_is_left_readable(self):
        self.assertEqual(safe_name("RTL Ticket (1).pdf"), "RTL Ticket (1).pdf")


class Attachments(unittest.TestCase):
    def test_the_pdf_is_found_and_the_body_is_not(self):
        found = attachments_of(ticket_mail(), uid=41)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].filename, "ticket.pdf")
        self.assertEqual(found[0].data, b"%PDF-1.4 pretend")
        self.assertEqual(found[0].uid, 41)
        self.assertEqual(found[0].subject, "Your ferry ticket")

    def test_a_message_with_no_pdf_yields_nothing(self):
        found = attachments_of(ticket_mail(filename="logo.png"))
        self.assertEqual(found, [])

    def test_a_forwarded_ticket_is_still_found(self):
        # The PDF is a leaf of a message inside a message, so a walk of only the
        # top level would miss it.
        outer = EmailMessage()
        outer["From"] = "me@example.com"
        outer["Subject"] = "Fwd: your ticket"
        outer.set_content("See below.")
        outer.make_mixed()
        outer.attach(ticket_mail())
        found = attachments_of(outer)
        self.assertEqual([a.filename for a in found], ["ticket.pdf"])

    def test_raw_bytes_parse_the_same_way(self):
        found = attachments_in(ticket_mail().as_bytes(), uid=7)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].uid, 7)

    def test_an_encoded_filename_is_decoded_and_cleaned(self):
        message = ticket_mail(filename="=?utf-8?q?ferry=20ticket.pdf?=")
        found = attachments_of(message)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].filename.endswith(".pdf"))


class Senders(unittest.TestCase):
    def test_commas_and_spaces_both_separate(self):
        self.assertEqual(parse_senders("rtl.mv, mtcc.com.mv"), ("rtl.mv", "mtcc.com.mv"))
        self.assertEqual(parse_senders("rtl.mv;mtcc.com.mv"), ("rtl.mv", "mtcc.com.mv"))

    def test_nothing_typed_is_nobody_watched(self):
        self.assertEqual(parse_senders(""), ())
        self.assertEqual(parse_senders(None), ())
        self.assertEqual(parse_senders(" , "), ())


class Saving(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        from pathlib import Path

        self.folder = Path(self._temp.name)

    def attachment(self, uid=5, filename="ticket.pdf", data=b"one"):
        return Attachment(uid=uid, filename=filename, data=data)

    def test_the_file_lands_in_the_folder(self):
        path = save_attachment(self.attachment(), self.folder)
        self.assertEqual(path.parent, self.folder)
        self.assertEqual(path.read_bytes(), b"one")

    def test_two_tickets_called_the_same_thing_both_survive(self):
        first = save_attachment(self.attachment(uid=5, data=b"one"), self.folder)
        second = save_attachment(self.attachment(uid=6, data=b"two"), self.folder)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), b"one")
        self.assertEqual(second.read_bytes(), b"two")

    def test_the_same_uid_twice_does_not_overwrite(self):
        first = save_attachment(self.attachment(uid=5, data=b"one"), self.folder)
        second = save_attachment(self.attachment(uid=5, data=b"two"), self.folder)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), b"one")

    def test_a_traversing_attachment_stays_in_the_folder(self):
        nasty = Attachment(
            uid=9,
            filename=safe_name("../../../evil.pdf"),
            data=b"x",
        )
        path = save_attachment(nasty, self.folder)
        self.assertEqual(path.parent.resolve(), self.folder.resolve())


class Captions(unittest.TestCase):
    def test_the_subject_is_used(self):
        caption = caption_for(Attachment(uid=1, filename="t.pdf", data=b"", subject="Ticket 14B8A0"))
        self.assertIn("Ticket 14B8A0", caption)

    def test_a_missing_subject_still_says_something(self):
        caption = caption_for(Attachment(uid=1, filename="t.pdf", data=b""))
        self.assertTrue(caption.strip())


class Signing_in(unittest.TestCase):
    def test_credentials_without_a_password_are_not_usable(self):
        self.assertFalse(Credentials(user="a@b.com", password="").usable)
        self.assertFalse(Credentials(user="", password="secret").usable)
        self.assertTrue(Credentials(user="a@b.com", password="secret").usable)


class Storing_the_password(unittest.TestCase):
    """The app password is the most valuable thing here: it opens an inbox."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from mind.config_store import ConfigStore

        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = ConfigStore(root=Path(self._temp.name) / "config")

    def test_it_comes_back_out_the_way_it_went_in(self):
        config = self.store.set_mail_password(self.store.load(), "abcdefghijklmnop")
        self.assertEqual(self.store.get_mail_password(config), "abcdefghijklmnop")

    def test_it_is_not_readable_in_the_settings(self):
        config = self.store.set_mail_password(self.store.load(), "abcdefghijklmnop")
        self.assertNotIn("abcdefghijklmnop", str(config))

    def test_the_spaces_gmail_shows_it_with_are_ignored(self):
        # Gmail prints an app password in groups of four and accepts it either
        # way; copying it with the spaces must not save a wrong password.
        config = self.store.set_mail_password(self.store.load(), "abcd efgh ijkl mnop")
        self.assertEqual(self.store.get_mail_password(config), "abcdefghijklmnop")

    def test_clearing_it_removes_it(self):
        config = self.store.set_mail_password(self.store.load(), "abcdefghijklmnop")
        config = self.store.set_mail_password(config, "")
        self.assertEqual(self.store.get_mail_password(config), "")


if __name__ == "__main__":
    unittest.main()
