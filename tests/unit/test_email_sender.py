"""EmailSender — mocked smtplib, no network."""
from email import message_from_string

import pytest


@pytest.fixture
def sender(monkeypatch):
    secrets = {
        "SENDER_SERVER": "smtp.example.ch", "SENDER_PORT": "587",
        "SENDER_USER": "archi@example.ch", "SENDER_PW": "pw",
        "SENDER_REPLYTO": "noreply@example.ch",
    }
    monkeypatch.setattr("src.utils.email_sender.read_secret", lambda k: secrets.get(k))
    from src.utils.email_sender import EmailSender
    return EmailSender()


class FakeSMTP:
    instances = []

    def __init__(self, server, port):
        self.server, self.port = server, port
        self.sent = []
        self.fail_times = FakeSMTP.fail_times
        self.quit_called = False
        FakeSMTP.instances.append(self)

    fail_times = 0

    def starttls(self):
        pass

    def login(self, user, pw):
        pass

    def sendmail(self, from_addr, to_addrs, msg):
        if FakeSMTP.fail_times > 0:
            FakeSMTP.fail_times -= 1
            raise OSError("boom")
        self.sent.append((from_addr, to_addrs, msg))

    def quit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.quit_called = True
        return False


@pytest.fixture(autouse=True)
def _smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.fail_times = 0
    monkeypatch.setattr("src.utils.email_sender.smtplib.SMTP", FakeSMTP)


def test_sends_multipart_with_plain_and_html(sender):
    sender.send(["ops@cern.ch"], "daily digest", "# Transfers\n\n| a | b |\n|---|---|\n| 1 | 2 |",
                footer="Automated by archi")
    (_, to_addrs, raw) = FakeSMTP.instances[-1].sent[0]
    assert to_addrs == ["ops@cern.ch"]
    msg = message_from_string(raw)
    parts = {p.get_content_type() for p in msg.walk()}
    assert "text/plain" in parts and "text/html" in parts
    assert "Automated by archi" in raw


def test_banner_prepended_to_plain_text(sender):
    sender.send(["a@cern.ch"], "s", "body", banner="verdict unparseable")
    raw = FakeSMTP.instances[-1].sent[0][2]
    assert "verdict unparseable" in raw


def test_retries_once_then_succeeds(sender):
    FakeSMTP.fail_times = 1
    sender.send(["a@cern.ch"], "s", "body")
    assert sum(len(i.sent) for i in FakeSMTP.instances) == 1


def test_two_failures_raise_email_send_error(sender):
    from src.utils.email_sender import EmailSendError
    FakeSMTP.fail_times = 2
    with pytest.raises(EmailSendError):
        sender.send(["a@cern.ch"], "s", "body")


def test_html_render_failure_still_sends_plain(sender, monkeypatch):
    monkeypatch.setattr("src.utils.email_sender._render_html",
                        lambda text: (_ for _ in ()).throw(RuntimeError("no md")))
    sender.send(["a@cern.ch"], "s", "body")
    raw = FakeSMTP.instances[-1].sent[0][2]
    msg = message_from_string(raw)
    parts = {p.get_content_type() for p in msg.walk()}
    assert "text/plain" in parts


def test_failed_attempt_still_closes_connection(sender):
    FakeSMTP.fail_times = 1
    sender.send(["a@cern.ch"], "s", "body")
    assert len(FakeSMTP.instances) == 2
    assert all(instance.quit_called is True for instance in FakeSMTP.instances)


def test_subject_crlf_is_collapsed(sender):
    sender.send(["a@cern.ch"], "hi\r\nBcc: evil@x.com", "body")
    raw = FakeSMTP.instances[-1].sent[0][2]
    msg = message_from_string(raw)
    assert "\r" not in msg["Subject"]
    assert "\n" not in msg["Subject"]
    assert msg["Bcc"] is None


def test_recipient_with_crlf_is_rejected(sender):
    from src.utils.email_sender import EmailSendError
    with pytest.raises(EmailSendError):
        sender.send(["a@cern.ch\r\nBcc: evil@x.com"], "s", "body")
    assert FakeSMTP.instances == []


def test_empty_recipients_raises_before_smtp(sender):
    from src.utils.email_sender import EmailSendError
    with pytest.raises(EmailSendError):
        sender.send([], "s", "body")
    assert FakeSMTP.instances == []
