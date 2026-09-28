"""Investor-relations alert emails as a source of releases.

The route that reaches the hosts refusing an identified crawler. A folder of
saved messages is not a curated feed: it holds other people's mail, marketing,
and the platform's own furniture, so most of this is about what must *not*
become a document.
"""
from __future__ import annotations

import pytest

from foci_screen.connectors.alerts import AlertConnector, release_link


def write(tmp_path, name, subject, body, frm="alerts@q4inc.example",
          date="Tue, 15 Sep 2026 12:00:00 +0000", html=False):
    """A saved message. Headers must start at column zero — an indented one is
    not a header, and the message then parses as a body with no subject."""
    content_type = "text/html" if html else "text/plain"
    path = tmp_path / f"{name}.eml"
    headers = [
        f"From: {frm}",
        "To: analyst@example.gov",
        f"Subject: {subject}",
        f"Date: {date}",
        f'Content-Type: {content_type}; charset="utf-8"',
        "",
    ]
    path.write_text("\n".join(headers) + body + "\n", encoding="utf-8")
    return path


AWARD_BODY = """\
Meridian Photonics Inc announced today that it has been awarded a $340 million
contract to supply photonic sensing subsystems to the Department of the Navy.
Read the full release: https://meridian.example/news/navy-award
Unsubscribe: https://q4inc.example/unsubscribe?id=99
"""

SETTLEMENT_BODY = """\
Meridian Photonics Inc today announced a settlement resolving False Claims Act
allegations relating to contract pricing.
https://meridian.example/news/fca-settlement
Manage your alerts: https://q4inc.example/preferences
"""


# ------------------------------------------------------------- link picking

def test_the_release_link_is_preferred_over_the_platform_links():
    link = release_link(AWARD_BODY, "", {"meridian.example"})
    assert link == "https://meridian.example/news/navy-award"


def test_unsubscribe_and_preferences_links_are_never_the_release():
    body = ("Manage: https://q4inc.example/preferences\n"
            "Unsubscribe: https://q4inc.example/unsubscribe?id=1\n")
    assert release_link(body, "", set()) == ""


def test_an_html_alert_link_is_found():
    html = ('<a href="https://q4inc.example/track?u=1">view</a>'
            '<a href="https://meridian.example/news/q3">Q3 results</a>')
    assert release_link("", html, {"meridian.example"}) == \
        "https://meridian.example/news/q3"


def test_without_a_known_domain_the_first_real_link_wins():
    assert release_link(AWARD_BODY, "", set()) == \
        "https://meridian.example/news/navy-award"


# ----------------------------------------------------------------- reading

def test_an_alert_becomes_a_release_document(tmp_path):
    write(tmp_path, "award", "Meridian Photonics announces $340M Navy award",
          AWARD_BODY)
    docs = AlertConnector(str(tmp_path)).collect(
        "MERIDIAN PHOTONICS INC", ["meridian.example"])

    assert len(docs) == 1
    doc = docs[0]
    assert doc.meta["via"] == "email alert"
    assert doc.url == "https://meridian.example/news/navy-award"
    assert doc.published.startswith("2026-09-15")
    assert "Department of the Navy" in doc.text


def test_the_key_matches_what_the_feed_connector_would_produce(tmp_path):
    """An announcement that arrives by feed and by email is one document with
    two sightings, not two that each read as new."""
    write(tmp_path, "award", "Meridian announces award", AWARD_BODY)
    docs = AlertConnector(str(tmp_path)).collect("MERIDIAN PHOTONICS INC",
                                                 ["meridian.example"])
    assert docs[0].key == "feed:meridian.example/news/navy-award"


def test_releases_are_sorted_the_same_way_as_feed_items(tmp_path):
    write(tmp_path, "fca", "Meridian announces settlement of FCA matter",
          SETTLEMENT_BODY)
    docs = AlertConnector(str(tmp_path)).collect("MERIDIAN PHOTONICS INC",
                                                 ["meridian.example"])
    assert docs[0].meta["release_kind"] == "legal"
    assert docs[0].doc_type == "legal_release"


def test_the_same_release_twice_is_one_document(tmp_path):
    """Platforms resend, and a folder accumulates duplicates."""
    write(tmp_path, "award1", "Meridian announces award", AWARD_BODY)
    write(tmp_path, "award2", "REMINDER: Meridian announces award", AWARD_BODY)
    docs = AlertConnector(str(tmp_path)).collect("MERIDIAN PHOTONICS INC",
                                                 ["meridian.example"])
    assert len(docs) == 1


# ----------------------------------------------------- what must be ignored

def test_mail_about_another_company_is_left_alone(tmp_path):
    write(tmp_path, "other", "Acme Widgets announces quarterly results",
          "Acme Widgets Inc reported results.\nhttps://acme.example/news/q3")
    docs = AlertConnector(str(tmp_path)).collect("MERIDIAN PHOTONICS INC",
                                                 ["meridian.example"])
    assert docs == []


def test_ordinary_mail_in_the_folder_is_not_a_release(tmp_path):
    write(tmp_path, "lunch", "Lunch tomorrow?",
          "Are you free at one? I can come to your building if that is easier.")
    conn = AlertConnector(str(tmp_path))
    assert conn.collect("MERIDIAN PHOTONICS INC", ["meridian.example"]) == []
    assert conn.messages_skipped == 1


def test_a_message_too_short_to_match_on_is_skipped(tmp_path):
    write(tmp_path, "thin", "Meridian press release",
          "https://meridian.example/news/x")
    assert AlertConnector(str(tmp_path)).collect(
        "MERIDIAN PHOTONICS INC", ["meridian.example"]) == []


def test_an_unreadable_file_does_not_stop_the_rest(tmp_path):
    (tmp_path / "broken.eml").write_bytes(b"\xff\xfe not a message at all")
    write(tmp_path, "award", "Meridian announces award", AWARD_BODY)
    docs = AlertConnector(str(tmp_path)).collect("MERIDIAN PHOTONICS INC",
                                                 ["meridian.example"])
    assert len(docs) == 1


def test_no_directory_means_no_documents_and_no_error():
    conn = AlertConnector("")
    assert conn.available() is False
    assert conn.collect("ANY COMPANY") == []


def test_a_missing_directory_is_simply_unavailable(tmp_path):
    conn = AlertConnector(str(tmp_path / "nope"))
    assert conn.available() is False
    assert conn.collect("ANY COMPANY") == []


# --------------------------------------------------------------- wiring in

def test_availability_is_reported_in_the_config(tmp_path, monkeypatch):
    from foci_screen.config import Config

    monkeypatch.setenv("FOCI_ALERTS_DIR", str(tmp_path))
    assert Config().availability()["ir_email_alerts"] is True

    monkeypatch.setenv("FOCI_ALERTS_DIR", str(tmp_path / "missing"))
    assert Config().availability()["ir_email_alerts"] is False


@pytest.mark.parametrize("subject,expected_kind", [
    ("Meridian reports fourth quarter results", "financial"),
    ("Meridian settles litigation with DOJ", "legal"),
    ("Meridian opens Huntsville facility", "press"),
])
def test_subject_drives_the_classification(tmp_path, subject, expected_kind):
    write(tmp_path, "m", subject,
          "Meridian Photonics Inc issued the following release today. "
          "https://meridian.example/news/item and further detail besides.")
    docs = AlertConnector(str(tmp_path)).collect("MERIDIAN PHOTONICS INC",
                                                 ["meridian.example"])
    assert docs and docs[0].meta["release_kind"] == expected_kind
