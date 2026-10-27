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

One way in: `deliver_notice`, which takes a row from the review queue once a
person has approved it, and sends the approved text — which may have been
edited — rather than a re-render. There is deliberately no path from a raw
finding to Gmail; `render_finding` (the CLI's `--notify`) only writes .eml
files. `deliver_notice` runs inside the web service, where there is nobody to
complete a browser sign-in, so it never starts one: without a usable token.json
it reports why and the notice stays approved, to be delivered once
`foci-screen gmail-auth` has been run.

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
  6. `foci-screen gmail-auth` opens a browser once and writes token.json
     (on a server, run it locally and copy token.json to GMAIL_TOKEN)
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
        self.connect_error = ""

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
    def render_finding(self, finding: Finding, run_id: str = "") -> DeliveryResult:
        """Write a finding's notice to disk as an .eml. Never touches Gmail.

        This is what the CLI's `--notify` and `notify --run` produce. It used to
        be a delivery path in its own right, drafting or sending straight from
        a finding once GMAIL_ENABLED was set — past the review queue, so a
        notice could reach Gmail without anyone having approved it. Gmail is
        now reachable only through `deliver_notice`, which takes a notice a
        person has approved. Whatever the settings, this writes a file.
        """
        recipient, note = self._resolve_recipient(finding)
        subject = render.subject_for(finding)
        sender = self.cfg.gmail_sender or "foci-screen@localhost"
        if not recipient:
            path = render.write_eml(finding, self.cfg.out_dir, sender=sender,
                                    recipient="UNRESOLVED-KO@example.invalid",
                                    run_id=run_id)
            return DeliveryResult("suppressed", "", subject,
                                  detail=note, path=str(path))
        path = render.write_eml(finding, self.cfg.out_dir, sender=sender,
                                recipient=recipient, run_id=run_id)
        return DeliveryResult("rendered", recipient, subject,
                              detail=f"wrote .eml for reading; {note}", path=str(path))

    def deliver_notice(self, notice: dict, *, interactive: bool = False,
                       recipient: str = "") -> DeliveryResult:
        """Hand one approved notice to Gmail, under the same three guards.

        The caller is responsible for the notice being approved; this only
        decides where it goes and how. `recipient` addresses it to someone
        other than the officer on the notice — a copy for the reviewer, say;
        the redirect still applies. Outcomes:

          * rendered — GMAIL_ENABLED is false. Nothing left; the .eml can be
            downloaded from the review queue and routed by hand.
          * drafted / sent — Gmail accepted it.
          * error — Gmail was meant to take it and did not. The reason is kept
            and the notice can be tried again.
        """
        intended = (recipient or notice.get("recipient") or "").strip()
        subject = notice.get("subject") or "FOCI/IP screening notice"
        if not intended:
            return DeliveryResult("error", "", subject,
                                  detail="no contracting officer address on the notice")

        recipient, note = intended, f"addressed to {intended}"
        if self.cfg.email_redirect_to:
            # The intended officer goes in the subject, as the alert mailer
            # does, so whoever reads the pilot inbox can see who it was for.
            recipient = self.cfg.email_redirect_to
            subject = f"[for {intended}] {subject}"
            note = f"redirected from {intended} by FOCI_EMAIL_REDIRECT_TO"

        if not self.cfg.gmail_enabled:
            return DeliveryResult("rendered", recipient, subject,
                                  detail="GMAIL_ENABLED is false, so nothing was sent; "
                                         "download the .eml to route it by hand.")

        service = self._connect(interactive=interactive)
        if service is None:
            return DeliveryResult("error", recipient, subject,
                                  detail=self.connect_error or "Gmail client unavailable")

        msg = render.message_from_notice({**notice, "subject": subject},
                                         sender=self.cfg.gmail_sender,
                                         recipient=recipient)
        raw = base64.urlsafe_b64encode(bytes(msg)).decode()
        try:
            if self.cfg.gmail_send:
                sent = service.users().messages().send(
                    userId="me", body={"raw": raw}).execute()
                return DeliveryResult("sent", recipient, subject,
                                      detail=f"gmail message id {sent.get('id')}; {note}")
            draft = service.users().drafts().create(
                userId="me", body={"message": {"raw": raw}}).execute()
            return DeliveryResult("drafted", recipient, subject,
                                  detail=f"gmail draft id {draft.get('id')}; {note}")
        except Exception as exc:  # noqa: BLE001 - any API failure is a retryable outcome
            # The first line only, as the alert mailer does: an auth error can
            # echo what was sent.
            first = str(exc).splitlines()[0][:300] if str(exc) else ""
            log.warning("gmail delivery of notice %s failed: %s",
                        notice.get("notice_id"), type(exc).__name__)
            return DeliveryResult("error", recipient, subject,
                                  detail=f"Gmail refused it: {type(exc).__name__}"
                                         + (f": {first}" if first else ""))

    # ------------------------------------------------------------- client
    def _connect(self, interactive: bool = True):
        """The Gmail service, or None with `connect_error` saying why.

        `interactive=False` never opens a browser: inside the web service there
        is nobody to sign in, and `run_local_server` would wait forever.
        """
        if self._service is not None:
            return self._service
        try:
            from google.auth.transport.requests import Request  # type: ignore
            from google.oauth2.credentials import Credentials  # type: ignore
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except ImportError:
            self.connect_error = ('the Gmail libraries are not installed '
                                  '(pip install -e ".[gmail]")')
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
                try:
                    creds.refresh(Request())
                except Exception as exc:  # noqa: BLE001 - revoked or expired grant
                    self.connect_error = (f"the saved Gmail sign-in could not be renewed "
                                          f"({type(exc).__name__}); run "
                                          f"`foci-screen gmail-auth` again")
                    return None
            elif not interactive:
                self.connect_error = (f"Gmail has not been signed in to on this machine "
                                      f"(no usable {token_path}); run "
                                      f"`foci-screen gmail-auth` once")
                return None
            else:
                cred_path = Path(self.cfg.gmail_credentials)
                if not cred_path.is_file():
                    self.connect_error = f"Gmail credentials not found at {cred_path}"
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
