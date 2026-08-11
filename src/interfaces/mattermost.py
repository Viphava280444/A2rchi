"""Mattermost integration for Archi.

Archi answers the way an assistant tagged in Slack does: someone writes
``@archi <question>`` in a channel Archi has joined, or sends it a direct
message, and Archi replies inside that thread using the thread as
conversation history.

The trigger names default to the bot account's own username (whatever the
personal access token belongs to), so a deployment does not have to be told
its own name; ``services.mattermost.mention_aliases`` adds extra tags.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from src.utils.config_access import get_full_config, get_global_config
from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MATTERMOST_URL = "https://mattermost.web.cern.ch/"
# Mattermost rejects posts above 16383 characters; stay well below that so a
# long answer is split rather than dropped.
MAX_POST_LENGTH = 15000
DEFAULT_THREAD_HISTORY_LIMIT = 20
# Bound the answered-id ledger so a long-lived deployment does not grow a
# state file without limit.
MAX_ANSWERED_IDS = 2000
DIRECT_CHANNEL_TYPES = ("D", "G")

USER_ROLE = "User"
ARCHI_ROLE = "archi"


def build_mention_pattern(names: Iterable[str]) -> Optional[re.Pattern]:
    """Compile a pattern matching ``@name`` for any of ``names``.

    Returns ``None`` when there is nothing to match, which callers read as
    "this deployment has no tag to listen for".
    """
    cleaned = {
        name.strip().lstrip("@").lower()
        for name in names or []
        if name and name.strip().lstrip("@")
    }
    if not cleaned:
        return None

    alternatives = "|".join(
        re.escape(name) for name in sorted(cleaned, key=len, reverse=True)
    )
    # The lookbehind keeps an e-mail address (``ask@archi``) from reading as a
    # tag; the lookahead keeps ``@archi-bot``/``@archi.dev`` — different
    # accounts — from matching the alias ``archi``, while still allowing the
    # sentence-final ``@archi.``
    return re.compile(rf"(?<![\w@.-])@({alternatives})(?![\w-]|\.\w)", re.IGNORECASE)


def strip_mentions(message: str, pattern: Optional[re.Pattern]) -> str:
    """Drop the tag itself so the model sees the question, not the address."""
    if not message:
        return ""
    text = pattern.sub(" ", message) if pattern else message
    return re.sub(r"\s+", " ", text).strip()


def split_message(message: str, limit: int = MAX_POST_LENGTH) -> List[str]:
    """Split an answer into postable chunks, preferring line boundaries."""
    text = (message or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n")
        if split_at < limit // 2:
            split_at = window.rfind(" ")
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return [chunk for chunk in chunks if chunk]


def is_system_post(post: Dict[str, Any]) -> bool:
    return str(post.get("type") or "").startswith("system_")


def sorted_thread_posts(thread: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a thread's posts oldest-first.

    ``order`` is authoritative when present, but the API omits it for some
    responses, so fall back to the posts map and sort by creation time.
    """
    posts = thread.get("posts") or {}
    ordered = [
        posts[post_id] for post_id in (thread.get("order") or []) if post_id in posts
    ]
    if not ordered:
        ordered = list(posts.values())
    return sorted(ordered, key=lambda post: post.get("create_at", 0))


class MattermostClient:
    """Minimal Mattermost v4 REST client backed by a personal access token."""

    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base_url = (base_url or DEFAULT_MATTERMOST_URL).rstrip("/")
        self.token = token
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v4/{path.lstrip('/')}"

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = requests.get(
            self._url(path), headers=self.headers, params=params, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        response = requests.post(
            self._url(path),
            headers=self.headers,
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def me(self) -> Dict[str, Any]:
        return self._get("users/me")

    def get_channel(self, channel_id: str) -> Dict[str, Any]:
        return self._get(f"channels/{channel_id}")

    def get_posts_since(
        self, channel_id: str, since_ms: Optional[int]
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = (
            {"since": int(since_ms)} if since_ms else {"per_page": 30}
        )
        return self._get(f"channels/{channel_id}/posts", params=params)

    def get_thread(self, post_id: str) -> Dict[str, Any]:
        return self._get(f"posts/{post_id}/thread")

    def create_post(
        self, channel_id: str, message: str, root_id: Optional[str] = None
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"channel_id": channel_id, "message": message}
        if root_id:
            payload["root_id"] = root_id
        return self._post("posts", payload)

    def my_channels(self) -> List[Dict[str, Any]]:
        """Every channel the token's account belongs to, across all its teams."""
        channels: List[Dict[str, Any]] = []
        for team in self._get("users/me/teams") or []:
            team_id = team.get("id")
            if not team_id:
                continue
            channels.extend(self._get(f"users/me/teams/{team_id}/channels") or [])
        return channels


class MattermostAIWrapper:
    """Turn a Mattermost thread into an Archi answer."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        # Imported here rather than at module scope so that the polling and
        # mention-matching logic can be exercised without pulling in the whole
        # model/vectorstore stack.
        from src.archi.archi import archi
        from src.data_manager.data_manager import DataManager

        self.config = config or {}

        # initialize and update vector store
        self.data_manager = DataManager(run_ingestion=False)

        kwargs: Dict[str, Any] = {}
        agent_spec = self._load_agent_spec()
        if agent_spec is not None:
            kwargs["agent_spec"] = agent_spec
        provider = self.config.get("default_provider") or self.config.get("provider")
        if provider:
            kwargs["default_provider"] = provider
        model = self.config.get("default_model") or self.config.get("model")
        if model:
            kwargs["default_model"] = model
        prompt_overrides = self.config.get("prompts")
        if prompt_overrides:
            kwargs["prompt_overrides"] = prompt_overrides

        self.archi = archi(self.config.get("agent_class") or "QAPipeline", **kwargs)

    def _load_agent_spec(self):
        from src.archi.pipelines.agents.agent_spec import (
            AgentSpecError,
            select_agent_spec,
        )

        agents_dir = self.config.get("agents_dir")
        if not agents_dir:
            agents_dir = (
                get_full_config()
                .get("services", {})
                .get("chat_app", {})
                .get("agents_dir")
            )
        if not agents_dir:
            return None
        try:
            return select_agent_spec(Path(agents_dir))
        except AgentSpecError as exc:
            logger.warning(f"Mattermost: failed to load agent spec: {exc}")
            return None

    def __call__(self, history: List[Tuple[str, str]]):
        """Answer the last message of ``history``."""
        result = self.archi(history=history)
        answer = result["answer"] if result is not None else ""
        question = history[-1][1] if history else ""
        return answer, question


class Mattermost:
    """Watch Mattermost for posts that tag Archi and answer them in-thread.

    Every polling cycle asks each watched channel for the posts created since
    the previous cycle, keeps the ones addressed to Archi (an ``@archi`` tag, a
    direct message, or a follow-up in a thread Archi is already part of), and
    replies as a threaded post.
    """

    def __init__(self, client: Optional[MattermostClient] = None):
        logger.info("Mattermost::INIT")

        config = get_full_config()
        services = config.get("services") or {}
        # ``utils.mattermost`` is where older configs put these settings.
        self.mattermost_config = (
            services.get("mattermost")
            or (config.get("utils") or {}).get("mattermost")
            or {}
        )

        self.update_time = int(self.mattermost_config.get("update_time", 60))
        self.thread_history_limit = int(
            self.mattermost_config.get(
                "thread_history_limit", DEFAULT_THREAD_HISTORY_LIMIT
            )
        )
        self.respond_to_dms = bool(self.mattermost_config.get("respond_to_dms", True))
        self.respond_to_thread_followups = bool(
            self.mattermost_config.get("respond_to_thread_followups", True)
        )

        self.mattermost_url = (
            self.mattermost_config.get("url") or DEFAULT_MATTERMOST_URL
        )
        self.mattermost_webhook = read_secret("MATTERMOST_WEBHOOK")
        self.mattermost_channel_id_read = read_secret("MATTERMOST_CHANNEL_ID_READ")
        self.mattermost_channel_id_write = read_secret("MATTERMOST_CHANNEL_ID_WRITE")
        self.PAK = read_secret("MATTERMOST_PAK")
        self.mattermost_headers = {
            "Authorization": f"Bearer {self.PAK}",
            "Content-Type": "application/json",
        }

        self.client = client or MattermostClient(self.mattermost_url, self.PAK)

        self.bot_user_id, self.bot_username = self._identify_bot()
        self.mention_pattern = build_mention_pattern(
            list(self.mattermost_config.get("mention_aliases") or [])
            + ([self.bot_username] if self.bot_username else [])
        )
        if self.mention_pattern is None:
            logger.warning(
                "Mattermost: no mention name resolved; only direct messages will be answered."
            )
        else:
            logger.info(f"Mattermost: answering posts that tag @{self.bot_username}")

        self.state_file = (
            self.mattermost_config.get("state_file") or self._default_state_file()
        )
        self.state = self._read_state()
        self._channel_cache: Dict[str, Dict[str, Any]] = {}

        # initialize MattermostAIWrapper
        self.ai_wrapper = MattermostAIWrapper(self.mattermost_config)

    # ----- setup helpers -------------------------------------------------

    def _identify_bot(self) -> Tuple[Optional[str], Optional[str]]:
        """Resolve the token's own account so Archi never answers itself."""
        configured_id = self.mattermost_config.get("bot_user_id")
        configured_name = self.mattermost_config.get("bot_username")
        try:
            me = self.client.me() or {}
            return (
                configured_id or me.get("id"),
                configured_name or me.get("username"),
            )
        except Exception as exc:
            logger.error(f"Mattermost: failed to resolve bot identity: {exc}")
            return configured_id, configured_name

    def _default_state_file(self) -> str:
        try:
            data_path = get_global_config().get("DATA_PATH") or "/root/data/"
        except Exception:
            data_path = "/root/data/"
        return os.path.join(data_path, "mattermost", "state.json")

    def _read_state(self) -> Dict[str, Any]:
        state = {"last_seen": {}, "answered_ids": []}
        if not os.path.exists(self.state_file):
            return state
        try:
            with open(self.state_file, "r") as handle:
                stored = json.load(handle) or {}
        except (OSError, ValueError) as exc:
            logger.error(
                f"Mattermost: failed to read state file, starting fresh: {exc}"
            )
            return state

        last_seen = stored.get("last_seen")
        if isinstance(last_seen, dict):
            state["last_seen"] = {k: int(v) for k, v in last_seen.items()}
        # Pre-mention deployments stored a flat "answered_id" list.
        answered = stored.get("answered_ids", stored.get("answered_id", []))
        if isinstance(answered, str):
            answered = [answered]
        if isinstance(answered, list):
            state["answered_ids"] = [str(post_id) for post_id in answered]
        return state

    def _write_state(self) -> None:
        answered = self.state.get("answered_ids", [])
        if len(answered) > MAX_ANSWERED_IDS:
            self.state["answered_ids"] = answered[-MAX_ANSWERED_IDS:]
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w") as handle:
                json.dump(self.state, handle)
        except OSError as exc:
            logger.error(f"Mattermost: failed to write state file: {exc}")

    # ----- channel selection --------------------------------------------

    def channels_to_watch(self) -> List[str]:
        """Channel ids to poll: configured ones, else every channel Archi joined."""
        configured = self.mattermost_config.get("channels") or []
        if configured:
            return [str(channel_id) for channel_id in configured]
        if self.mattermost_channel_id_read:
            return [self.mattermost_channel_id_read]
        try:
            return [
                channel["id"]
                for channel in self.client.my_channels()
                if channel.get("id") and channel.get("delete_at", 0) == 0
            ]
        except Exception as exc:
            logger.error(f"Mattermost: failed to list channels: {exc}")
            return []

    def _channel_type(self, channel_id: str) -> str:
        if channel_id not in self._channel_cache:
            try:
                self._channel_cache[channel_id] = (
                    self.client.get_channel(channel_id) or {}
                )
            except Exception as exc:
                logger.error(f"Mattermost: failed to fetch channel {channel_id}: {exc}")
                self._channel_cache[channel_id] = {}
        return str(self._channel_cache[channel_id].get("type") or "")

    # ----- deciding what to answer ---------------------------------------

    def is_tagged(self, post: Dict[str, Any]) -> bool:
        if self.mention_pattern is None:
            return False
        return bool(self.mention_pattern.search(post.get("message") or ""))

    def _bot_is_in_thread(self, post: Dict[str, Any]) -> bool:
        root_id = post.get("root_id")
        if not root_id or not self.bot_user_id:
            return False
        try:
            thread = self.client.get_thread(root_id)
        except Exception as exc:
            logger.error(f"Mattermost: failed to fetch thread {root_id}: {exc}")
            return False
        return any(
            entry.get("user_id") == self.bot_user_id
            for entry in sorted_thread_posts(thread)
        )

    def should_respond(self, post: Dict[str, Any], channel_type: str) -> bool:
        if post.get("user_id") == self.bot_user_id:
            return False
        if is_system_post(post):
            return False
        if not (post.get("message") or "").strip():
            return False
        if self.is_tagged(post):
            return True
        if self.respond_to_dms and channel_type in DIRECT_CHANNEL_TYPES:
            return True
        # A follow-up in a thread Archi already answered continues the
        # conversation without needing the tag again, like a Slack thread.
        if self.respond_to_thread_followups and self._bot_is_in_thread(post):
            return True
        return False

    # ----- building the conversation --------------------------------------

    def build_history(self, post: Dict[str, Any]) -> List[Tuple[str, str]]:
        """Use the whole thread as history, ending with the post to answer."""
        root_id = post.get("root_id") or post.get("id")
        thread_posts: List[Dict[str, Any]] = []
        if root_id:
            try:
                thread_posts = sorted_thread_posts(self.client.get_thread(root_id))
            except Exception as exc:
                logger.error(f"Mattermost: failed to fetch thread {root_id}: {exc}")

        # Only keep what precedes the post being answered: a thread fetched
        # after other people replied would otherwise put later messages after
        # the question.
        cutoff = post.get("create_at", 0)
        thread_posts = [
            entry
            for entry in thread_posts
            if not is_system_post(entry)
            and (entry.get("message") or "").strip()
            and entry.get("create_at", 0) < cutoff
        ]
        if self.thread_history_limit > 0:
            thread_posts = thread_posts[-self.thread_history_limit :]

        history: List[Tuple[str, str]] = []
        for entry in thread_posts:
            role = ARCHI_ROLE if entry.get("user_id") == self.bot_user_id else USER_ROLE
            history.append(
                (role, strip_mentions(entry.get("message"), self.mention_pattern))
            )

        question = strip_mentions(post.get("message"), self.mention_pattern)
        history.append((USER_ROLE, question))
        return history

    # ----- posting ---------------------------------------------------------

    def post_response(
        self,
        response: str,
        channel_id: Optional[str] = None,
        root_id: Optional[str] = None,
    ):
        """Reply in-thread through the API, falling back to the webhook."""
        chunks = split_message(response)
        if not chunks:
            logger.info("Mattermost: empty answer, nothing to post")
            return None

        if channel_id and self.PAK:
            try:
                created = None
                for chunk in chunks:
                    created = self.client.create_post(
                        channel_id, chunk, root_id=root_id
                    )
                return created
            except Exception as exc:
                logger.error(f"Mattermost: failed to post reply via API: {exc}")

        if not self.mattermost_webhook:
            logger.error("Mattermost: no webhook configured, dropping answer")
            return None
        for chunk in chunks:
            requests.post(
                self.mattermost_webhook,
                data=json.dumps({"text": chunk, "channel": "town-square"}),
                headers=self.mattermost_headers,
            )
        return None

    # ----- polling ---------------------------------------------------------

    def process_posts(self):
        """One polling cycle across every watched channel."""
        channels = self.channels_to_watch()
        if not channels:
            logger.info("Mattermost: no channels to watch")
            return
        for channel_id in channels:
            try:
                self.process_channel(channel_id)
            except Exception as exc:
                logger.error(
                    f"Mattermost: failed to process channel {channel_id}: {exc}"
                )
        self._write_state()

    def process_channel(self, channel_id: str):
        last_seen = self.state["last_seen"].get(channel_id)
        data = self.client.get_posts_since(channel_id, last_seen) or {}
        posts = sorted_thread_posts(data)

        if last_seen is None:
            # First sighting of a channel: remember where we are instead of
            # answering the entire backlog on startup.
            newest = max((post.get("create_at", 0) for post in posts), default=0)
            self.state["last_seen"][channel_id] = newest or int(time.time() * 1000)
            logger.info(f"Mattermost: now watching channel {channel_id}")
            return

        channel_type = self._channel_type(channel_id)
        newest_seen = last_seen
        for post in posts:
            newest_seen = max(newest_seen, post.get("create_at", 0))
            post_id = post.get("id")
            if not post_id or post_id in self.state["answered_ids"]:
                continue
            if not self.should_respond(post, channel_type):
                continue
            self.answer_post(post, channel_id)

        self.state["last_seen"][channel_id] = newest_seen

    def answer_post(self, post: Dict[str, Any], channel_id: str):
        post_id = post["id"]
        # Record the id before answering so a crash mid-answer cannot turn into
        # a reply loop on the next cycle.
        self.state["answered_ids"].append(post_id)
        self._write_state()

        try:
            history = self.build_history(post)
            answer, question = self.ai_wrapper(history)
            logger.info(f"Mattermost: answering post {post_id}: {question[:120]}")
            self.post_response(
                answer,
                channel_id=channel_id,
                root_id=post.get("root_id") or post_id,
            )
        except Exception as exc:
            logger.error(f"Mattermost: failed to answer post {post_id}: {exc}")
