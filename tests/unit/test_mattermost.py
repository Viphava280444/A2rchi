"""Unit tests for the Mattermost mention ("@archi tag") integration."""

from unittest.mock import patch

import pytest

from src.interfaces import mattermost as mattermost_interface

BOT_ID = "bot-user-id"
BOT_NAME = "archi"
CHANNEL = "channel-1"


def _post(
    post_id, message, user_id="human-1", create_at=1000, root_id="", post_type=""
):
    return {
        "id": post_id,
        "message": message,
        "user_id": user_id,
        "create_at": create_at,
        "root_id": root_id,
        "type": post_type,
    }


class _FakeClient:
    """Stands in for the Mattermost REST API."""

    def __init__(self, posts=None, channel_type="O", threads=None, channels=None):
        self.posts_by_channel = posts if posts is not None else {}
        self.channel_type = channel_type
        self.threads = threads or {}
        self.channels = channels if channels is not None else []
        self.created = []
        self.since_calls = []

    def me(self):
        return {"id": BOT_ID, "username": BOT_NAME}

    def get_channel(self, channel_id):
        return {"id": channel_id, "type": self.channel_type}

    def get_posts_since(self, channel_id, since_ms):
        self.since_calls.append((channel_id, since_ms))
        posts = self.posts_by_channel.get(channel_id, [])
        if since_ms:
            posts = [post for post in posts if post["create_at"] > since_ms]
        return {
            "order": [post["id"] for post in posts],
            "posts": {p["id"]: p for p in posts},
        }

    def get_thread(self, post_id):
        posts = self.threads.get(post_id, [])
        return {
            "order": [post["id"] for post in posts],
            "posts": {p["id"]: p for p in posts},
        }

    def create_post(self, channel_id, message, root_id=None):
        created = {
            "id": f"reply-{len(self.created)}",
            "channel_id": channel_id,
            "message": message,
            "root_id": root_id,
        }
        self.created.append(created)
        return created

    def my_channels(self):
        return self.channels


class _FakeWrapper:
    def __init__(self, answer="Here is the answer."):
        self.answer = answer
        self.calls = []

    def __call__(self, history):
        self.calls.append(history)
        return self.answer, history[-1][1] if history else ""


def _prime(service, channel=CHANNEL, watermark=1000):
    """Skip the first-cycle catch-up so a test starts from a known watermark."""
    service.state["last_seen"][channel] = watermark


def _build(tmp_path, client, config=None, wrapper=None):
    """Construct a Mattermost service with the model/vectorstore stack stubbed out."""
    full_config = {
        "services": {
            "mattermost": {"state_file": str(tmp_path / "state.json"), **(config or {})}
        }
    }
    secrets = {
        "MATTERMOST_PAK": "token",
        "MATTERMOST_WEBHOOK": "https://hooks.example/webhook",
        "MATTERMOST_CHANNEL_ID_READ": "",
        "MATTERMOST_CHANNEL_ID_WRITE": "",
    }
    with patch.object(
        mattermost_interface, "get_full_config", return_value=full_config
    ), patch.object(
        mattermost_interface,
        "read_secret",
        side_effect=lambda name, default="": secrets.get(name, default),
    ), patch.object(
        mattermost_interface,
        "MattermostAIWrapper",
        return_value=wrapper or _FakeWrapper(),
    ):
        service = mattermost_interface.Mattermost(client=client)
    return service


# --- mention matching -------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "@archi what is the trigger rate?",
        "hey @archi, can you help?",
        "ask @Archi please",
        "question for @archi.",
    ],
)
def test_mention_pattern_matches_tags(message):
    pattern = mattermost_interface.build_mention_pattern(["archi"])
    assert pattern.search(message)


@pytest.mark.parametrize(
    "message",
    [
        "archi is great",  # no tag
        "mail me at ask@archi",  # e-mail-ish, not a tag
        "@archi-bot handles that",  # a different account
        "@archi.dev owns this",  # a different account
    ],
)
def test_mention_pattern_ignores_non_tags(message):
    pattern = mattermost_interface.build_mention_pattern(["archi"])
    assert pattern.search(message) is None


def test_mention_pattern_empty_names_returns_none():
    assert mattermost_interface.build_mention_pattern([]) is None
    assert mattermost_interface.build_mention_pattern(["", "@"]) is None


def test_strip_mentions_removes_tag_and_collapses_space():
    pattern = mattermost_interface.build_mention_pattern(["archi"])
    assert (
        mattermost_interface.strip_mentions("@archi  what is  RAG?", pattern)
        == "what is RAG?"
    )


# --- message splitting ------------------------------------------------------


def test_split_message_keeps_short_answers_whole():
    assert mattermost_interface.split_message("short") == ["short"]


def test_split_message_chunks_long_answers_under_limit():
    text = "\n".join(["line " + str(i) for i in range(5000)])
    chunks = mattermost_interface.split_message(text, limit=1000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunk.replace("\n", "") for chunk in chunks) == text.replace(
        "\n", ""
    )


def test_split_message_drops_empty_answer():
    assert mattermost_interface.split_message("   ") == []


# --- polling behaviour ------------------------------------------------------


def test_first_cycle_records_position_without_answering_backlog(tmp_path):
    client = _FakeClient(
        posts={CHANNEL: [_post("p1", "@archi old question", create_at=500)]},
        channels=[{"id": CHANNEL, "delete_at": 0}],
    )
    service = _build(tmp_path, client)

    service.process_posts()

    assert client.created == []
    assert service.state["last_seen"][CHANNEL] == 500


def test_tagged_post_is_answered_in_thread(tmp_path):
    client = _FakeClient(
        posts={CHANNEL: []}, channels=[{"id": CHANNEL, "delete_at": 0}]
    )
    wrapper = _FakeWrapper("42")
    service = _build(tmp_path, client, wrapper=wrapper)
    _prime(service)

    client.posts_by_channel[CHANNEL] = [
        _post("p2", "@archi what is the answer?", create_at=2000)
    ]
    service.process_posts()

    assert len(client.created) == 1
    assert client.created[0]["message"] == "42"
    # A root-level post is its own thread root, so the reply threads under it.
    assert client.created[0]["root_id"] == "p2"
    assert wrapper.calls[0][-1] == ("User", "what is the answer?")


def test_untagged_channel_post_is_ignored(tmp_path):
    client = _FakeClient(
        posts={CHANNEL: []}, channels=[{"id": CHANNEL, "delete_at": 0}]
    )
    service = _build(tmp_path, client)
    _prime(service)

    client.posts_by_channel[CHANNEL] = [_post("p3", "just chatting", create_at=2000)]
    service.process_posts()

    assert client.created == []


def test_direct_message_is_answered_without_a_tag(tmp_path):
    client = _FakeClient(
        posts={CHANNEL: []},
        channel_type="D",
        channels=[{"id": CHANNEL, "delete_at": 0}],
    )
    service = _build(tmp_path, client)
    _prime(service)

    client.posts_by_channel[CHANNEL] = [
        _post("p4", "no tag needed here", create_at=2000)
    ]
    service.process_posts()

    assert len(client.created) == 1


def test_archis_own_posts_are_never_answered(tmp_path):
    client = _FakeClient(
        posts={CHANNEL: []}, channels=[{"id": CHANNEL, "delete_at": 0}]
    )
    service = _build(tmp_path, client)
    _prime(service)

    client.posts_by_channel[CHANNEL] = [
        _post("p5", "@archi self reference", user_id=BOT_ID, create_at=2000)
    ]
    service.process_posts()

    assert client.created == []


def test_system_posts_are_ignored(tmp_path):
    client = _FakeClient(
        posts={CHANNEL: []}, channels=[{"id": CHANNEL, "delete_at": 0}]
    )
    service = _build(tmp_path, client)
    _prime(service)

    client.posts_by_channel[CHANNEL] = [
        _post(
            "p6",
            "@archi joined the channel",
            create_at=2000,
            post_type="system_join_channel",
        )
    ]
    service.process_posts()

    assert client.created == []


def test_thread_followup_continues_without_a_new_tag(tmp_path):
    root = _post("root", "@archi first question", create_at=1000)
    reply = _post(
        "bot-reply", "first answer", user_id=BOT_ID, create_at=1100, root_id="root"
    )
    followup = _post(
        "p7", "and what about the second part?", create_at=2000, root_id="root"
    )
    client = _FakeClient(
        posts={CHANNEL: []},
        channels=[{"id": CHANNEL, "delete_at": 0}],
        threads={"root": [root, reply, followup]},
    )
    wrapper = _FakeWrapper("second answer")
    service = _build(tmp_path, client, wrapper=wrapper)
    _prime(service)

    client.posts_by_channel[CHANNEL] = [followup]
    service.process_posts()

    assert len(client.created) == 1
    assert client.created[0]["root_id"] == "root"
    # History is the thread so far, with Archi's own reply attributed to Archi.
    assert wrapper.calls[0] == [
        ("User", "first question"),
        ("archi", "first answer"),
        ("User", "and what about the second part?"),
    ]


def test_thread_followup_ignored_when_archi_never_joined_the_thread(tmp_path):
    root = _post("root2", "a human question", create_at=1000)
    followup = _post(
        "p8", "a human answer", user_id="human-2", create_at=2000, root_id="root2"
    )
    client = _FakeClient(
        posts={CHANNEL: []},
        channels=[{"id": CHANNEL, "delete_at": 0}],
        threads={"root2": [root, followup]},
    )
    service = _build(tmp_path, client)
    _prime(service)

    client.posts_by_channel[CHANNEL] = [followup]
    service.process_posts()

    assert client.created == []


def test_history_excludes_posts_created_after_the_question(tmp_path):
    root = _post("root3", "@archi first question", create_at=1000)
    later = _post(
        "later", "unrelated chatter", user_id="human-2", create_at=3000, root_id="root3"
    )
    client = _FakeClient(threads={"root3": [root, later]})
    service = _build(tmp_path, client)

    history = service.build_history(root)

    assert history == [("User", "first question")]


def test_answered_posts_are_not_answered_twice_across_restarts(tmp_path):
    post = _post("p9", "@archi repeated?", create_at=2000)
    client = _FakeClient(
        posts={CHANNEL: []}, channels=[{"id": CHANNEL, "delete_at": 0}]
    )
    service = _build(tmp_path, client)
    _prime(service)
    client.posts_by_channel[CHANNEL] = [post]
    service.process_posts()
    assert len(client.created) == 1

    # A restart re-reads the state file, and the channel replays the same post.
    restarted = _build(tmp_path, client)
    _prime(restarted)
    restarted.process_posts()

    assert len(client.created) == 1


def test_long_answer_is_posted_as_multiple_threaded_chunks(tmp_path):
    client = _FakeClient(
        posts={CHANNEL: []}, channels=[{"id": CHANNEL, "delete_at": 0}]
    )
    wrapper = _FakeWrapper("x" * (mattermost_interface.MAX_POST_LENGTH + 500))
    service = _build(tmp_path, client, wrapper=wrapper)
    _prime(service)

    client.posts_by_channel[CHANNEL] = [
        _post("p10", "@archi long please", create_at=2000)
    ]
    service.process_posts()

    assert len(client.created) == 2
    assert all(reply["root_id"] == "p10" for reply in client.created)


def test_configured_channels_take_precedence_over_membership(tmp_path):
    client = _FakeClient(posts={}, channels=[{"id": "other", "delete_at": 0}])
    service = _build(
        tmp_path, client, config={"channels": ["configured-1", "configured-2"]}
    )

    assert service.channels_to_watch() == ["configured-1", "configured-2"]


def test_state_file_from_older_deployments_is_still_honoured(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text('{"answered_id": ["legacy-post"]}')
    client = _FakeClient()
    service = _build(tmp_path, client)

    assert service.state["answered_ids"] == ["legacy-post"]


def test_answer_failure_does_not_stop_the_polling_cycle(tmp_path):
    class _ExplodingWrapper:
        def __call__(self, history):
            raise RuntimeError("model unavailable")

    client = _FakeClient(
        posts={CHANNEL: []}, channels=[{"id": CHANNEL, "delete_at": 0}]
    )
    service = _build(tmp_path, client, wrapper=_ExplodingWrapper())
    _prime(service)

    client.posts_by_channel[CHANNEL] = [_post("p11", "@archi boom", create_at=2000)]
    service.process_posts()  # must not raise

    assert client.created == []
    assert "p11" in service.state["answered_ids"]
