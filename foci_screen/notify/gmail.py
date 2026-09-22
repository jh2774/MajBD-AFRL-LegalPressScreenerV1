"""Gmail delivery — STEP 3, DELIBERATELY NOT ACTIVE.

Per the build request this is wired but not switched on. What exists here:

  * a complete OAuth + draft-creation path against the Gmail API;
  * three independent guards that must ALL be cleared before anything leaves
    the machine.

The guards, outermost first:

  1. GMAIL_ENABLED must be true            -> otherwise render to .eml only
  2. GMAIL_SEND must be true               -> otherwise create a Gmail *draft*
  3. FOCI_EMAIL_REDIRECT_TO, if set, overrides the recipient entirely

Default posture with no configuration: notices are written to disk as .eml
files and nothing touches the network.

Why drafts rather than sends, even once enabled: these notices go to federal
contracting officers about named companies. A false positive mailed
automatically is a real harm to a real contractor. A human should read each one.
Flipping GMAIL_SEND to true is a deliberate act with that trade-off understood.

Scopes needed (narrowest first):
  https://www.googleapis.com/auth/gmail.compose   - create drafts
  https://www.googleapis.com/auth/gmail.send      - only if GMAIL_SEND is used

Setup:
  1. console.cloud.google.com -> new project -> enable the Gmail API
  2. OAuth consent screen -> External or Internal -> add your address as a tester
  3. Credentials -> OAuth client ID -> Desktop app -> download credentials.json
  4. pip install google-api-python-client google-auth-oauthlib
  5. set GMAIL_ENABLED=true and GMAIL_SENDER=you@yourdomain.gov in .env
  6. first run opens a browser once and writes token.json
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from ..models import Finding
from . import render

log = logging.getLogger("foci.gmail")

SCOPES_DRAFT = ["https://www.googleapis.com/auth/gmail.compose"]
SCOPES_SEND = ["https://www.googleapis.com/auth/gmail.send"]


@dataclass
class DeliveryResult:
    status: str          # rendered | drafted | sent | suppressed | error
    recipient: str
    subject: str
    detail: str = ""
    path: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("rendered", "drafted", "sent")


class GmailNotifier:
    def __init__(self, config) -> None:
        self.cfg = config
        self._service = None

    # ------------------------------------------------------------- guards
    def _resolve_recipient(self, finding: Finding) -> tuple[str, str]:
        """Return (recipient, note). Honours the redirect safety valve."""
        officer = finding.top_officer()
        if self.cfg.email_redirect_to:
            note = (f"redirected from {officer.email or 'unresolved KO'} "
                    f"by FOCI_EMAIL_REDIRECT_TO")
            return self.cfg.email_redirect_to, note
        if not officer.is_addressable:
            return "", "no contracting officer email could be resolved"
        return officer.email, f"resolved from {officer.source} ({officer.confidence})"

    # ------------------------------------------------------------ delivery
    def deliver(self, finding: Finding, run_id: str = "") -> DeliveryResult:
        recipient, note = self._resolve_recipient(finding)
        subject = render.subject_for(finding)

        if not recipient:
            path = render.write_eml(finding, self.cfg.out_dir,
                                    sender=self.cfg.gmail_sender or "foci-screen@localhost",
                                    recipient="UNRESOLVED-KO@example.invalid",
                                    run_id=run_id)
            return DeliveryResult("suppressed", "", subject,
                                  detail=note, path=str(path))

        # Guard 1 — the whole Gmail path is off by default.
        if not self.cfg.gmail_enabled:
            path = render.write_eml(finding, self.cfg.out_dir,
                                    sender=self.cfg.gmail_sender or "foci-screen@localhost",
                                    recipient=recipient, run_id=run_id)
            return DeliveryResult("rendered", recipient, subject,
                                  detail=f"GMAIL_ENABLED is false; wrote .eml. {note}",
                                  path=str(path))

        service = self._connect()
        if service is None:
            path = render.write_eml(finding, self.cfg.out_dir,
                                    sender=self.cfg.gmail_sender or "foci-screen@localhost",
                                    recipient=recipient, run_id=run_id)
            return DeliveryResult("error", recipient, subject,
                                  detail="Gmail client unavailable; wrote .eml instead",
                                  path=str(path))

        msg = render.build_message(finding, sender=self.cfg.gmail_sender,
                                   recipient=recipient, run_id=run_id)
        raw = base64.urlsafe_b64encode(bytes(msg)).decode()

        try:
            # Guard 2 — sending requires an explicit second opt-in.
            if self.cfg.gmail_send:
                sent = service.users().messages().send(
                    userId="me", body={"raw": raw}).execute()
                return DeliveryResult("sent", recipient, subject,
                                      detail=f"gmail message id {sent.get('id')}. {note}")
            draft = service.users().drafts().create(
                userId="me", body={"message": {"raw": raw}}).execute()
            return DeliveryResult("drafted", recipient, subject,
                                  detail=f"gmail draft id {draft.get('id')}. {note}")
        except Exception as exc:  # pragma: no cover - needs live creds
            log.warning("gmail delivery failed: %s", exc)
            return DeliveryResult("error", recipient, subject, detail=str(exc))

    # ------------------------------------------------------------- client
    def _connect(self):
        if self._service is not None:
            return self._service
        try:
            from google.auth.transport.requests import Request  # type: ignore
            from google.oauth2.credentials import Credentials  # type: ignore
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except ImportError:
            log.warning("Gmail libraries not installed. "
                        "pip install google-api-python-client google-auth-oauthlib")
            return None

        scopes = SCOPES_DRAFT + (SCOPES_SEND if self.cfg.gmail_send else [])
        token_path = Path(self.cfg.gmail_token)
        creds = None
        if token_path.is_file():
            creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                cred_path = Path(self.cfg.gmail_credentials)
                if not cred_path.is_file():
                    log.warning("Gmail credentials not found at %s", cred_path)
                    return None
                flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), scopes)
                creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        self._service = build("gmail", "v1", credentials=creds,
                              cache_discovery=False)
        return self._service


def status_banner(config) -> str:
    """One line the CLI prints so the operating posture is never ambiguous."""
    if not config.gmail_enabled:
        return "email: OFF (rendering .eml files only — set GMAIL_ENABLED=true to draft)"
    if config.gmail_send:
        return ("email: SEND MODE — messages will be delivered to contracting officers"
                + (f" [redirected to {config.email_redirect_to}]"
                   if config.email_redirect_to else ""))
    return ("email: DRAFT MODE — Gmail drafts created, nothing sent"
            + (f" [redirected to {config.email_redirect_to}]"
               if config.email_redirect_to else ""))
