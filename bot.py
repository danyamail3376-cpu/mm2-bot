"""
=====================================================================================
 MM2 / Adopt Me Giveaway Telegram Bot  —  SINGLE FILE, PRODUCTION-READY EDITION
=====================================================================================

Stack:
    - Python 3.11+
    - aiogram 3.x            (async Telegram Bot framework)
    - asyncpg                (async PostgreSQL driver, connection pool)
    - pydantic-settings      (typed configuration from environment variables)
    - aiohttp                (webhook web-server, works on Render / Koyeb free tier)

Everything (config, database layer, middlewares, keyboards, FSM, handlers,
webhook server) lives in this ONE file on purpose, so it can be uploaded and
run from a phone (e.g. via GitHub -> Render/Koyeb) without dealing with a
folder structure.

Run modes:
    - USE_WEBHOOK=true   -> aiohttp webserver + Telegram webhook (Render/Koyeb)
    - USE_WEBHOOK=false  -> long polling (local testing)

Author: Senior Python Developer (generated for production deployment)
=====================================================================================
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import asyncpg
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    Update,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# =====================================================================================
# LOGGING
# =====================================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mm2_giveaway_bot")
logging.getLogger("aiogram.event").setLevel(logging.WARNING)


# =====================================================================================
# CONFIGURATION  (Pydantic)
# =====================================================================================

class Settings(BaseSettings):
    """
    All configuration is read from environment variables (or a local .env file).
    See .env.example for the full list.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram ---
    bot_token: str = Field(..., alias="BOT_TOKEN")
    admin_ids: str = Field(..., alias="ADMIN_IDS")  # comma separated telegram user ids

    # --- Database ---
    database_url: str = Field(..., alias="DATABASE_URL")  # postgres://user:pass@host:port/db

    # --- Webhook / server ---
    use_webhook: bool = Field(True, alias="USE_WEBHOOK")
    webhook_host: str = Field("", alias="WEBHOOK_HOST")       # e.g. https://my-app.onrender.com
    webhook_path: str = Field("/webhook", alias="WEBHOOK_PATH")
    webhook_secret: str = Field("change-me-secret-token", alias="WEBHOOK_SECRET")
    web_server_host: str = Field("0.0.0.0", alias="WEB_SERVER_HOST")
    web_server_port: int = Field(default_factory=lambda: int(os.environ.get("PORT", 8080)))

    # --- Gameplay defaults (seeded into DB on first run, editable later from /admin) ---
    default_channel_url: str = Field("https://t.me/", alias="DEFAULT_CHANNEL_URL")
    default_claim_url: str = Field("https://t.me/", alias="DEFAULT_CLAIM_URL")
    default_win_chance: float = Field(35.0, alias="DEFAULT_WIN_CHANCE")
    default_cooldown_hours: int = Field(24, alias="DEFAULT_COOLDOWN_HOURS")

    # --- Anti-spam ---
    throttle_seconds: float = Field(0.8, alias="THROTTLE_SECONDS")

    @field_validator("admin_ids")
    @classmethod
    def _validate_admin_ids(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ADMIN_IDS must contain at least one Telegram user id")
        return v

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_ids.split(",") if x.strip()}


try:
    settings = Settings()
except Exception as exc:  # noqa: BLE001
    logger.critical("Failed to load configuration: %s", exc)
    raise SystemExit(1) from exc


# =====================================================================================
# PRIZE SEED DATA — exact MM2 inventory list (55 items) with emojis attached
# =====================================================================================

PRIZE_SEED: list[tuple[str, str]] = [
    ("🔫", "Traveler's Gun"),
    ("🎄", "Evergun"),
    ("✨", "Constellation"),
    ("🌲", "Evergreen"),
    ("🟢", "Alienbeam"),
    ("🔪", "Vampire's Gun"),
    ("🦃", "Turkey"),
    ("🔫", "Raygun"),
    ("🖤", "Darkshot"),
    ("🗡️", "Darksword"),
    ("🌸", "Blossom"),
    ("🌸", "Sakura"),
    ("🌅", "Sunrise"),
    ("🌆", "Sunset"),
    ("🔴", "Bauble"),
    ("🧊", "Snowcannon"),
    ("🔫", "Soul"),
    ("🗡️", "Spirit"),
    ("🌈", "Rainbow Gun"),
    ("🌿", "Flora"),
    ("🌈", "Rainbow"),
    ("💜", "Xenoknife"),
    ("💜", "Xenoshot"),
    ("🌸", "Bloom"),
    ("🪄", "Heart Wand"),
    ("🌊", "Ocean"),
    ("🌊", "Waves"),
    ("🔷", "Flowerwood Gun"),
    ("❄️", "Blizzard"),
    ("❄️", "Flowerwood"),
    ("❄️", "Snowstorm"),
    ("🗡️", "Snow Dagger"),
    ("🔫", "Watergun"),
    ("🍦", "Icecream"),
    ("🍬", "Treat"),
    ("🍬", "Sweet"),
    ("🌌", "Borealis"),
    ("🦇", "Bat"),
    ("🎄", "Ornament"),
    ("🔭", "Gingerscope"),
    ("🪓", "Traveler's Axe"),
    ("🌙", "Celestial"),
    ("🪓", "Vampire's Axe"),
    ("🏹", "Harvester"),
    ("🏹", "Icepiercer"),
    ("✨", "Chroma Raygun"),
    ("✨", "Chroma Sunrise"),
    ("✨", "Chroma Snowcannon"),
    ("✨", "Chroma Sunset"),
    ("✨", "Chroma Blizzard"),
    ("✨", "Chroma Snowstorm"),
    ("✨", "Chroma Heart Wand"),
    ("✨", "Chroma Watergun"),
    ("✨", "Chroma Snow Dagger"),
    ("✨", "Chroma Icecream"),
    ("📍", "Batwing"),
]

assert len(PRIZE_SEED) == 56, "Prize seed must contain exactly 56 items"


# --- Default editable texts (RU is the primary/strict UI language; EN kept for the
#     admin's own content-management convenience, not shown to end-users by default) ---

DEFAULT_TEXTS_RU: dict[str, str] = {
    "welcome": (
        "👋 Привет, {name}!\n\n"
        "🎁 Добро пожаловать в MM2 Giveaway Bot!\n"
        "Крути рулетку и получай крутые предметы из Murder Mystery 2 совершенно бесплатно! 🔫🗡️✨\n\n"
        "Выбери действие ниже 👇"
    ),
    "rules": (
        "📜 <b>Правила розыгрыша</b>\n\n"
        "1️⃣ Один спин рулетки — раз в {cooldown} ч.\n"
        "2️⃣ Шанс на победу — честный и фиксированный.\n"
        "3️⃣ При выигрыше ты получаешь на выбор 3 предмета 🎁\n"
        "4️⃣ Забирай приз по ссылке, которая появится под предметом.\n"
        "5️⃣ Накрутка / читерство — бан 🚫\n\n"
        "Удачи! 🍀"
    ),
    "win_caption": (
        "🎉 <b>Поздравляем, {name}!</b>\n\n"
        "🍀 Твоя удача принесла тебе крутые призы!\n"
        "✨ Выбери один из них ниже, чтобы забрать 👇"
    ),
    "lose_message": (
        "😔 Почти повезло, {name}!\n\n"
        "🎲 В этот раз удача не на твоей стороне.\n"
        "⏳ Попробуй снова через {cooldown} ч. Не сдавайся! 💪"
    ),
    "cooldown_message": (
        "⏳ <b>Рулетка ещё не готова!</b>\n\n"
        "🕐 Следующий спин будет доступен через: <b>{remaining}</b>\n"
        "Загляни попозже! 😉"
    ),
}

DEFAULT_TEXTS_EN: dict[str, str] = {
    "welcome": "👋 Hi, {name}!\n\n🎁 Welcome to the MM2 Giveaway Bot!",
    "rules": "📜 <b>Giveaway rules</b>\n\nSpin once every {cooldown}h. Good luck! 🍀",
    "win_caption": "🎉 Congrats, {name}! Pick one of your prizes below 👇",
    "lose_message": "😔 No luck this time, {name}. Try again in {cooldown}h.",
    "cooldown_message": "⏳ Next spin available in: <b>{remaining}</b>",
}

TEXT_KEYS: tuple[str, ...] = tuple(DEFAULT_TEXTS_RU.keys())

SETTINGS_DEFAULTS: dict[str, str] = {
    "channel_url": settings.default_channel_url,
    "default_claim_url": settings.default_claim_url,
    "win_chance": str(settings.default_win_chance),
    "cooldown_hours": str(settings.default_cooldown_hours),
}


# =====================================================================================
# DATABASE LAYER (asyncpg pool + all queries)
# =====================================================================================

class Database:
    """Thin async wrapper around an asyncpg connection pool."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    # ---------------------------------------------------------------- lifecycle ----

    async def connect(self) -> None:
        for attempt in range(1, 6):
            try:
                self.pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=1,
                    max_size=10,
                    command_timeout=30,
                )
                logger.info("PostgreSQL pool created successfully.")
                return
            except (OSError, asyncpg.PostgresError) as exc:
                logger.error("DB connection attempt %s/5 failed: %s", attempt, exc)
                await asyncio.sleep(min(2 * attempt, 10))
        raise RuntimeError("Could not connect to PostgreSQL after 5 attempts")

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            logger.info("PostgreSQL pool closed.")

    async def init_db(self) -> None:
        """Create tables if they do not exist and seed default data."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id      BIGINT PRIMARY KEY,
                        username     TEXT,
                        full_name    TEXT,
                        language     TEXT NOT NULL DEFAULT 'ru',
                        last_spin    TIMESTAMPTZ,
                        spins_count  INTEGER NOT NULL DEFAULT 0,
                        wins_count   INTEGER NOT NULL DEFAULT 0,
                        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                    );

                    CREATE TABLE IF NOT EXISTS prizes (
                        id          SERIAL PRIMARY KEY,
                        emoji       TEXT NOT NULL,
                        name        TEXT NOT NULL,
                        claim_url   TEXT,
                        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                    );

                    CREATE TABLE IF NOT EXISTS bot_settings (
                        key    TEXT PRIMARY KEY,
                        value  TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS bot_texts (
                        lang   TEXT NOT NULL,
                        key    TEXT NOT NULL,
                        value  TEXT NOT NULL,
                        PRIMARY KEY (lang, key)
                    );

                    CREATE TABLE IF NOT EXISTS win_log (
                        id          SERIAL PRIMARY KEY,
                        user_id     BIGINT NOT NULL,
                        prizes      TEXT NOT NULL,
                        won_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    """
                )

                # Seed prizes only if table is empty (keeps admin edits across restarts)
                count = await conn.fetchval("SELECT COUNT(*) FROM prizes;")
                if count == 0:
                    await conn.executemany(
                        "INSERT INTO prizes (emoji, name, claim_url, is_active) VALUES ($1, $2, NULL, TRUE);",
                        PRIZE_SEED,
                    )
                    logger.info("Seeded %s prizes into the database.", len(PRIZE_SEED))

                # Seed settings
                for key, value in SETTINGS_DEFAULTS.items():
                    await conn.execute(
                        "INSERT INTO bot_settings (key, value) VALUES ($1, $2) "
                        "ON CONFLICT (key) DO NOTHING;",
                        key,
                        value,
                    )

                # Seed texts
                for key, value in DEFAULT_TEXTS_RU.items():
                    await conn.execute(
                        "INSERT INTO bot_texts (lang, key, value) VALUES ('ru', $1, $2) "
                        "ON CONFLICT (lang, key) DO NOTHING;",
                        key,
                        value,
                    )
                for key, value in DEFAULT_TEXTS_EN.items():
                    await conn.execute(
                        "INSERT INTO bot_texts (lang, key, value) VALUES ('en', $1, $2) "
                        "ON CONFLICT (lang, key) DO NOTHING;",
                        key,
                        value,
                    )
        logger.info("Database schema is ready.")

    async def wipe_all(self) -> None:
        """Full factory reset: drop everything and re-seed from scratch."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DROP TABLE IF EXISTS users, prizes, bot_settings, bot_texts, win_log CASCADE;"
                )
        await self.init_db()
        logger.warning("FULL DATABASE WIPE performed by admin action.")

    # -------------------------------------------------------------------- users ----

    async def upsert_user(self, user_id: int, username: Optional[str], full_name: str) -> None:
        assert self.pool is not None
        try:
            await self.pool.execute(
                """
                INSERT INTO users (user_id, username, full_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE
                    SET username = EXCLUDED.username,
                        full_name = EXCLUDED.full_name;
                """,
                user_id,
                username,
                full_name,
            )
        except asyncpg.PostgresError as exc:
            logger.error("upsert_user failed for %s: %s", user_id, exc)

    async def try_start_spin(self, user_id: int, cooldown_hours: int) -> bool:
        """
        Atomically checks the cooldown AND registers the spin in a single UPDATE,
        eliminating race conditions from double-clicks / concurrent requests.
        Returns True if the spin is allowed (and was just registered), False if the
        user is still on cooldown.
        """
        assert self.pool is not None
        try:
            row = await self.pool.fetchrow(
                """
                UPDATE users
                SET last_spin = now(),
                    spins_count = spins_count + 1
                WHERE user_id = $1
                  AND (last_spin IS NULL OR last_spin <= now() - ($2 * INTERVAL '1 hour'))
                RETURNING user_id;
                """,
                user_id,
                cooldown_hours,
            )
            return row is not None
        except asyncpg.PostgresError as exc:
            logger.error("try_start_spin failed for %s: %s", user_id, exc)
            return False

    async def get_remaining_cooldown(self, user_id: int, cooldown_hours: int) -> Optional[str]:
        """Returns a human-readable remaining time string, or None if user can spin."""
        assert self.pool is not None
        try:
            row = await self.pool.fetchrow("SELECT last_spin FROM users WHERE user_id = $1;", user_id)
        except asyncpg.PostgresError as exc:
            logger.error("get_remaining_cooldown failed for %s: %s", user_id, exc)
            return None
        if row is None or row["last_spin"] is None:
            return None
        elapsed = datetime.now(timezone.utc) - row["last_spin"]
        remaining_seconds = cooldown_hours * 3600 - elapsed.total_seconds()
        if remaining_seconds <= 0:
            return None
        hours, rem = divmod(int(remaining_seconds), 3600)
        minutes, _ = divmod(rem, 60)
        return f"{hours} ч {minutes} мин"

    async def increment_wins(self, user_id: int) -> None:
        assert self.pool is not None
        try:
            await self.pool.execute(
                "UPDATE users SET wins_count = wins_count + 1 WHERE user_id = $1;", user_id
            )
        except asyncpg.PostgresError as exc:
            logger.error("increment_wins failed for %s: %s", user_id, exc)

    async def reset_all_timers(self) -> int:
        assert self.pool is not None
        try:
            result = await self.pool.execute("UPDATE users SET last_spin = NULL;")
            return int(result.split()[-1]) if result else 0
        except asyncpg.PostgresError as exc:
            logger.error("reset_all_timers failed: %s", exc)
            return 0

    async def get_all_user_ids(self) -> list[int]:
        assert self.pool is not None
        try:
            rows = await self.pool.fetch("SELECT user_id FROM users;")
            return [r["user_id"] for r in rows]
        except asyncpg.PostgresError as exc:
            logger.error("get_all_user_ids failed: %s", exc)
            return []

    async def get_stats(self) -> dict[str, Any]:
        assert self.pool is not None
        try:
            row = await self.pool.fetchrow(
                """
                SELECT
                    COUNT(*)                                            AS total_users,
                    COALESCE(SUM(spins_count), 0)                       AS total_spins,
                    COALESCE(SUM(wins_count), 0)                        AS total_wins,
                    COUNT(*) FILTER (WHERE last_spin > now() - INTERVAL '24 hours') AS active_24h
                FROM users;
                """
            )
            return dict(row) if row else {}
        except asyncpg.PostgresError as exc:
            logger.error("get_stats failed: %s", exc)
            return {}

    # ------------------------------------------------------------------- prizes ----

    async def get_active_prizes(self) -> list[asyncpg.Record]:
        assert self.pool is not None
        try:
            return await self.pool.fetch(
                "SELECT * FROM prizes WHERE is_active = TRUE ORDER BY id;"
            )
        except asyncpg.PostgresError as exc:
            logger.error("get_active_prizes failed: %s", exc)
            return []

    async def get_all_prizes(self) -> list[asyncpg.Record]:
        assert self.pool is not None
        try:
            return await self.pool.fetch("SELECT * FROM prizes ORDER BY id;")
        except asyncpg.PostgresError as exc:
            logger.error("get_all_prizes failed: %s", exc)
            return []

    async def get_random_prizes(self, count: int = 3) -> list[asyncpg.Record]:
        assert self.pool is not None
        try:
            return await self.pool.fetch(
                "SELECT * FROM prizes WHERE is_active = TRUE ORDER BY random() LIMIT $1;", count
            )
        except asyncpg.PostgresError as exc:
            logger.error("get_random_prizes failed: %s", exc)
            return []

    async def get_prize(self, prize_id: int) -> Optional[asyncpg.Record]:
        assert self.pool is not None
        try:
            return await self.pool.fetchrow("SELECT * FROM prizes WHERE id = $1;", prize_id)
        except asyncpg.PostgresError as exc:
            logger.error("get_prize failed for %s: %s", prize_id, exc)
            return None

    async def set_prize_url(self, prize_id: int, url: str) -> bool:
        assert self.pool is not None
        try:
            result = await self.pool.execute(
                "UPDATE prizes SET claim_url = $1 WHERE id = $2;", url, prize_id
            )
            return result.endswith("1")
        except asyncpg.PostgresError as exc:
            logger.error("set_prize_url failed for %s: %s", prize_id, exc)
            return False

    async def toggle_prize(self, prize_id: int) -> Optional[bool]:
        assert self.pool is not None
        try:
            row = await self.pool.fetchrow(
                "UPDATE prizes SET is_active = NOT is_active WHERE id = $1 RETURNING is_active;",
                prize_id,
            )
            return row["is_active"] if row else None
        except asyncpg.PostgresError as exc:
            logger.error("toggle_prize failed for %s: %s", prize_id, exc)
            return None

    async def log_win(self, user_id: int, prize_names: str) -> None:
        assert self.pool is not None
        try:
            await self.pool.execute(
                "INSERT INTO win_log (user_id, prizes) VALUES ($1, $2);", user_id, prize_names
            )
        except asyncpg.PostgresError as exc:
            logger.error("log_win failed for %s: %s", user_id, exc)

    # ----------------------------------------------------------------- settings ----

    async def get_setting(self, key: str, default: str = "") -> str:
        assert self.pool is not None
        try:
            row = await self.pool.fetchrow("SELECT value FROM bot_settings WHERE key = $1;", key)
            return row["value"] if row else default
        except asyncpg.PostgresError as exc:
            logger.error("get_setting failed for %s: %s", key, exc)
            return default

    async def set_setting(self, key: str, value: str) -> None:
        assert self.pool is not None
        try:
            await self.pool.execute(
                "INSERT INTO bot_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;",
                key,
                value,
            )
        except asyncpg.PostgresError as exc:
            logger.error("set_setting failed for %s: %s", key, exc)

    # -------------------------------------------------------------------- texts ----

    async def get_text(self, lang: str, key: str, **fmt: Any) -> str:
        assert self.pool is not None
        fallback = (DEFAULT_TEXTS_RU if lang == "ru" else DEFAULT_TEXTS_EN).get(key, key)
        try:
            row = await self.pool.fetchrow(
                "SELECT value FROM bot_texts WHERE lang = $1 AND key = $2;", lang, key
            )
            template = row["value"] if row else fallback
        except asyncpg.PostgresError as exc:
            logger.error("get_text failed for %s/%s: %s", lang, key, exc)
            template = fallback
        try:
            return template.format(**fmt)
        except (KeyError, IndexError):
            return template

    async def set_text(self, lang: str, key: str, value: str) -> None:
        assert self.pool is not None
        try:
            await self.pool.execute(
                "INSERT INTO bot_texts (lang, key, value) VALUES ($1, $2, $3) "
                "ON CONFLICT (lang, key) DO UPDATE SET value = EXCLUDED.value;",
                lang,
                key,
                value,
            )
        except asyncpg.PostgresError as exc:
            logger.error("set_text failed for %s/%s: %s", lang, key, exc)


db = Database(settings.database_url)


# =====================================================================================
# MIDDLEWARES
# =====================================================================================

class ThrottlingMiddleware:
    """
    Global anti-spam / anti-flood middleware.
    Blocks a user from triggering more than one update per `rate` seconds,
    across both messages and callback queries.
    """

    def __init__(self, rate: float = 0.8) -> None:
        self.rate = rate
        self._last_seen: dict[int, float] = {}

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            loop_time = asyncio.get_event_loop().time()
            last = self._last_seen.get(user.id)
            if last is not None and (loop_time - last) < self.rate:
                # Silently drop / soft warn without crashing the bot.
                if isinstance(event, Update) and event.callback_query:
                    try:
                        await event.callback_query.answer(
                            "⏳ Слишком быстро! Подожди немного.", show_alert=False
                        )
                    except TelegramAPIError:
                        pass
                return None
            self._last_seen[user.id] = loop_time
        return await handler(event, data)


# =====================================================================================
# FSM STATES (Admin panel inputs)
# =====================================================================================

class AdminStates(StatesGroup):
    editing_text = State()          # waiting for new text value (lang+key stored in data)
    editing_channel_url = State()
    editing_default_claim_url = State()
    editing_win_chance = State()
    editing_cooldown = State()
    editing_prize_url = State()     # waiting for new claim url for a specific prize
    broadcasting = State()


# =====================================================================================
# HELPERS
# =====================================================================================

def is_admin(user_id: int) -> bool:
    """Central admin check — reused by every single admin handler, every time."""
    return user_id in settings.admin_id_set


def fmt_prize(row: asyncpg.Record) -> str:
    return f"{row['emoji']} {row['name']}"


async def safe_answer_callback(callback: CallbackQuery, text: str = "", show_alert: bool = False) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramAPIError as exc:
        logger.debug("callback.answer failed (ignored): %s", exc)


async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    """Edits a message, tolerating 'message is not modified' and other benign API errors."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramAPIError as exc:
        if "message is not modified" in str(exc).lower():
            return
        logger.warning("edit_text failed, falling back to send: %s", exc)
        try:
            await callback.message.answer(text, reply_markup=reply_markup)
        except TelegramAPIError as exc2:
            logger.error("Fallback send also failed: %s", exc2)


# =====================================================================================
# KEYBOARDS
# =====================================================================================

def kb_main_menu(channel_url: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🎁 Крутить рулетку", callback_data="menu:spin")
    b.button(text="ℹ️ Правила игры", callback_data="menu:rules")
    b.button(text="↗️ Наш канал", url=channel_url)
    b.adjust(1)
    return b.as_markup()


def kb_back_to_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 В меню", callback_data="menu:back")
    return b.as_markup()


def kb_win_prizes(prizes: Iterable[asyncpg.Record], default_claim_url: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in prizes:
        url = p["claim_url"] or default_claim_url
        b.button(text=f"{p['emoji']} {p['name']} 🎁", url=url)
    b.adjust(1)
    return b.as_markup()


def kb_admin_main() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 Изменить тексты (RU)", callback_data="admin:texts:ru")
    b.button(text="📝 Изменить тексты (EN)", callback_data="admin:texts:en")
    b.button(text="🔗 Ссылка на канал", callback_data="admin:channel_url")
    b.button(text="🔗 Ссылка для призов", callback_data="admin:default_claim_url")
    b.button(text="🎲 Шанс выигрыша", callback_data="admin:win_chance")
    b.button(text="⏳ Время между спинами", callback_data="admin:cooldown")
    b.button(text="🎁 Управление призами", callback_data="admin:prizes:0")
    b.button(text="📊 Статистика", callback_data="admin:stats")
    b.button(text="🔄 Сбросить таймеры", callback_data="admin:reset_timers")
    b.button(text="📢 Рассылка", callback_data="admin:broadcast")
    b.button(text="❌ Сбросить FULL БД", callback_data="admin:wipe_db")
    b.button(text="🔙 Выйти из админки", callback_data="admin:exit")
    b.adjust(1)
    return b.as_markup()


def kb_admin_back() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад в админ-панель", callback_data="admin:home")
    return b.as_markup()


def kb_admin_texts(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    labels = {
        "welcome": "👋 Приветствие",
        "rules": "📜 Правила",
        "win_caption": "🎉 Текст победы",
        "lose_message": "😔 Текст проигрыша",
        "cooldown_message": "⏳ Текст кулдауна",
    }
    for key in TEXT_KEYS:
        b.button(text=labels.get(key, key), callback_data=f"admin:text_edit:{lang}:{key}")
    b.button(text="🔙 Назад", callback_data="admin:home")
    b.adjust(1)
    return b.as_markup()


PRIZES_PER_PAGE = 8


def kb_admin_prizes(prizes: list[asyncpg.Record], page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    start = page * PRIZES_PER_PAGE
    chunk = prizes[start:start + PRIZES_PER_PAGE]
    for p in chunk:
        status = "✅" if p["is_active"] else "🚫"
        b.button(text=f"{status} {p['emoji']} {p['name']}", callback_data=f"admin:prize:{p['id']}:{page}")
    b.adjust(1)

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:prizes:{page - 1}"))
    if start + PRIZES_PER_PAGE < len(prizes):
        nav_row.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"admin:prizes:{page + 1}"))
    if nav_row:
        b.row(*nav_row)

    b.row(InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin:home"))
    return b.as_markup()


def kb_admin_prize_detail(prize: asyncpg.Record, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle_label = "🚫 Деактивировать" if prize["is_active"] else "✅ Активировать"
    b.button(text="🔗 Изменить ссылку", callback_data=f"admin:prize_url:{prize['id']}:{page}")
    b.button(text=toggle_label, callback_data=f"admin:prize_toggle:{prize['id']}:{page}")
    b.button(text="🔙 К списку призов", callback_data=f"admin:prizes:{page}")
    b.adjust(1)
    return b.as_markup()


def kb_confirm_wipe() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Да, стереть всё", callback_data="admin:wipe_confirm")
    b.button(text="🔙 Отмена", callback_data="admin:home")
    b.adjust(1)
    return b.as_markup()


def kb_cancel_fsm() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Отмена", callback_data="admin:home")
    return b.as_markup()


# =====================================================================================
# ROUTERS
# =====================================================================================

user_router = Router(name="user")
admin_router = Router(name="admin")

# Defense in depth: filter the admin router at the router level too.
admin_router.message.filter(F.from_user.id.in_(settings.admin_id_set))
admin_router.callback_query.filter(F.from_user.id.in_(settings.admin_id_set))


# ------------------------------------------------------------------------- USER ----

@user_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        await db.upsert_user(user.id, user.username, user.full_name)
        channel_url = await db.get_setting("channel_url", settings.default_channel_url)
        text = await db.get_text("ru", "welcome", name=user.full_name)
        await message.answer(text, reply_markup=kb_main_menu(channel_url))
    except Exception as exc:  # noqa: BLE001
        logger.exception("cmd_start failed: %s", exc)
        await message.answer("⚠️ Произошла ошибка. Попробуйте ещё раз позже.")


@user_router.callback_query(F.data == "menu:back")
async def cb_menu_back(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return
    try:
        channel_url = await db.get_setting("channel_url", settings.default_channel_url)
        text = await db.get_text("ru", "welcome", name=callback.from_user.full_name)
        await safe_edit_message(callback, text, kb_main_menu(channel_url))
    finally:
        await safe_answer_callback(callback)


@user_router.callback_query(F.data == "menu:rules")
async def cb_menu_rules(callback: CallbackQuery) -> None:
    try:
        cooldown = await db.get_setting("cooldown_hours", str(settings.default_cooldown_hours))
        text = await db.get_text("ru", "rules", cooldown=cooldown)
        await safe_edit_message(callback, text, kb_back_to_menu())
    except Exception as exc:  # noqa: BLE001
        logger.exception("cb_menu_rules failed: %s", exc)
    finally:
        await safe_answer_callback(callback)


@user_router.callback_query(F.data == "menu:spin")
async def cb_menu_spin(callback: CallbackQuery) -> None:
    user = callback.from_user
    if user is None:
        return
    try:
        cooldown_hours_raw = await db.get_setting("cooldown_hours", str(settings.default_cooldown_hours))
        try:
            cooldown_hours = int(float(cooldown_hours_raw))
        except ValueError:
            cooldown_hours = settings.default_cooldown_hours

        allowed = await db.try_start_spin(user.id, cooldown_hours)

        if not allowed:
            remaining = await db.get_remaining_cooldown(user.id, cooldown_hours)
            remaining_str = remaining or "скоро"
            text = await db.get_text("ru", "cooldown_message", remaining=remaining_str)
            await safe_answer_callback(callback, "⏳ Рулетка ещё не готова!", show_alert=True)
            await safe_edit_message(callback, text, kb_back_to_menu())
            return

        win_chance_raw = await db.get_setting("win_chance", str(settings.default_win_chance))
        try:
            win_chance = float(win_chance_raw)
        except ValueError:
            win_chance = settings.default_win_chance
        win_chance = max(0.0, min(100.0, win_chance))

        await safe_answer_callback(callback, "🎰 Крутим рулетку...")

        roll = random.uniform(0, 100)
        if roll < win_chance:
            prizes = await db.get_random_prizes(3)
            if not prizes:
                # Gracefully handle an empty/misconfigured prize table.
                await safe_edit_message(
                    callback,
                    "⚠️ В данный момент призы недоступны. Обратитесь к администратору.",
                    kb_back_to_menu(),
                )
                return
            await db.increment_wins(user.id)
            names = ", ".join(fmt_prize(p) for p in prizes)
            await db.log_win(user.id, names)

            default_claim_url = await db.get_setting("default_claim_url", settings.default_claim_url)
            text = await db.get_text("ru", "win_caption", name=user.full_name)
            await safe_edit_message(callback, text, kb_win_prizes(prizes, default_claim_url))
        else:
            text = await db.get_text("ru", "lose_message", name=user.full_name, cooldown=cooldown_hours)
            await safe_edit_message(callback, text, kb_back_to_menu())

    except Exception as exc:  # noqa: BLE001
        logger.exception("cb_menu_spin failed for user %s: %s", user.id if user else "?", exc)
        try:
            await safe_answer_callback(callback, "⚠️ Произошла ошибка. Попробуйте позже.", show_alert=True)
        except TelegramAPIError:
            pass


# ------------------------------------------------------------------------ ADMIN ----

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return  # router filter already blocks this, kept as defense in depth
    await message.answer("🛠 <b>Админ-панель</b>\n\nВыберите действие:", reply_markup=kb_admin_main())
    logger.info("Admin %s opened the admin panel.", message.from_user.id)


@admin_router.callback_query(F.data == "admin:home")
async def cb_admin_home(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    await safe_edit_message(callback, "🛠 <b>Админ-панель</b>\n\nВыберите действие:", kb_admin_main())
    await safe_answer_callback(callback)


@admin_router.callback_query(F.data == "admin:exit")
async def cb_admin_exit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    await safe_edit_message(callback, "🔙 Вы вышли из админ-панели.")
    await safe_answer_callback(callback)


# --- Texts management -------------------------------------------------------------

@admin_router.callback_query(F.data.startswith("admin:texts:"))
async def cb_admin_texts(callback: CallbackQuery) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    lang = callback.data.split(":")[2]
    await safe_edit_message(
        callback,
        f"📝 Редактирование текстов ({lang.upper()}). Выберите текст:",
        kb_admin_texts(lang),
    )
    await safe_answer_callback(callback)


@admin_router.callback_query(F.data.startswith("admin:text_edit:"))
async def cb_admin_text_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    _, _, lang, key = callback.data.split(":")
    current = await db.get_text(lang, key)
    await state.update_data(lang=lang, key=key)
    await state.set_state(AdminStates.editing_text)
    await safe_edit_message(
        callback,
        f"✏️ Текущий текст (<code>{key}</code>, {lang.upper()}):\n\n{current}\n\n"
        f"Отправьте новый текст сообщением. Можно использовать плейсхолдеры "
        f"вида <code>{{name}}</code>, <code>{{cooldown}}</code>, <code>{{remaining}}</code>.",
        kb_cancel_fsm(),
    )
    await safe_answer_callback(callback)


@admin_router.message(AdminStates.editing_text)
async def process_text_edit(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    lang, key = data.get("lang"), data.get("key")
    if not lang or not key or not message.text:
        await message.answer("⚠️ Ошибка ввода. Отмена.", reply_markup=kb_admin_main())
        await state.clear()
        return
    await db.set_text(lang, key, message.text)
    await state.clear()
    logger.info("Admin %s updated text %s/%s", message.from_user.id, lang, key)
    await message.answer("✅ Текст успешно обновлён!", reply_markup=kb_admin_main())


# --- Channel URL / default claim URL ----------------------------------------------

@admin_router.callback_query(F.data == "admin:channel_url")
async def cb_admin_channel_url(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    current = await db.get_setting("channel_url", settings.default_channel_url)
    await state.set_state(AdminStates.editing_channel_url)
    await safe_edit_message(
        callback, f"🔗 Текущая ссылка на канал:\n{current}\n\nОтправьте новую ссылку.", kb_cancel_fsm()
    )
    await safe_answer_callback(callback)


@admin_router.message(AdminStates.editing_channel_url)
async def process_channel_url(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    url = (message.text or "").strip()
    if not url.startswith(("http://", "https://", "tg://")):
        await message.answer("⚠️ Ссылка должна начинаться с http(s):// или tg://. Попробуйте снова.")
        return
    await db.set_setting("channel_url", url)
    await state.clear()
    logger.info("Admin %s updated channel_url", message.from_user.id)
    await message.answer("✅ Ссылка на канал обновлена!", reply_markup=kb_admin_main())


@admin_router.callback_query(F.data == "admin:default_claim_url")
async def cb_admin_default_claim_url(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    current = await db.get_setting("default_claim_url", settings.default_claim_url)
    await state.set_state(AdminStates.editing_default_claim_url)
    await safe_edit_message(
        callback,
        f"🔗 Текущая ссылка для призов по умолчанию:\n{current}\n\n"
        f"Она используется, если у конкретного приза не указана своя ссылка.\n"
        f"Отправьте новую ссылку.",
        kb_cancel_fsm(),
    )
    await safe_answer_callback(callback)


@admin_router.message(AdminStates.editing_default_claim_url)
async def process_default_claim_url(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    url = (message.text or "").strip()
    if not url.startswith(("http://", "https://", "tg://")):
        await message.answer("⚠️ Ссылка должна начинаться с http(s):// или tg://. Попробуйте снова.")
        return
    await db.set_setting("default_claim_url", url)
    await state.clear()
    logger.info("Admin %s updated default_claim_url", message.from_user.id)
    await message.answer("✅ Ссылка для призов обновлена!", reply_markup=kb_admin_main())


# --- Win chance / cooldown ----------------------------------------------------------

@admin_router.callback_query(F.data == "admin:win_chance")
async def cb_admin_win_chance(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    current = await db.get_setting("win_chance", str(settings.default_win_chance))
    await state.set_state(AdminStates.editing_win_chance)
    await safe_edit_message(
        callback,
        f"🎲 Текущий шанс выигрыша: <b>{current}%</b>\n\nОтправьте новое значение (0–100).",
        kb_cancel_fsm(),
    )
    await safe_answer_callback(callback)


@admin_router.message(AdminStates.editing_win_chance)
async def process_win_chance(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
        if not (0 <= value <= 100):
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число от 0 до 100. Попробуйте снова.")
        return
    await db.set_setting("win_chance", str(value))
    await state.clear()
    logger.info("Admin %s updated win_chance to %s", message.from_user.id, value)
    await message.answer(f"✅ Шанс выигрыша установлен: {value}%", reply_markup=kb_admin_main())


@admin_router.callback_query(F.data == "admin:cooldown")
async def cb_admin_cooldown(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    current = await db.get_setting("cooldown_hours", str(settings.default_cooldown_hours))
    await state.set_state(AdminStates.editing_cooldown)
    await safe_edit_message(
        callback,
        f"⏳ Текущий кулдаун: <b>{current} ч.</b>\n\nОтправьте новое значение в часах (целое число).",
        kb_cancel_fsm(),
    )
    await safe_answer_callback(callback)


@admin_router.message(AdminStates.editing_cooldown)
async def process_cooldown(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    try:
        value = int(raw)
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите целое неотрицательное число часов. Попробуйте снова.")
        return
    await db.set_setting("cooldown_hours", str(value))
    await state.clear()
    logger.info("Admin %s updated cooldown_hours to %s", message.from_user.id, value)
    await message.answer(f"✅ Кулдаун установлен: {value} ч.", reply_markup=kb_admin_main())


# --- Prize management ----------------------------------------------------------------

@admin_router.callback_query(F.data.startswith("admin:prizes:"))
async def cb_admin_prizes_list(callback: CallbackQuery) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    page = int(callback.data.split(":")[2])
    prizes = await db.get_all_prizes()
    if not prizes:
        await safe_edit_message(callback, "⚠️ Список призов пуст.", kb_admin_back())
        await safe_answer_callback(callback)
        return
    await safe_edit_message(
        callback,
        f"🎁 <b>Управление призами</b> (всего: {len(prizes)})\n"
        f"✅ — активен, 🚫 — отключён. Нажмите на приз для настройки:",
        kb_admin_prizes(prizes, page),
    )
    await safe_answer_callback(callback)


@admin_router.callback_query(F.data.startswith("admin:prize:"))
async def cb_admin_prize_detail(callback: CallbackQuery) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    _, _, prize_id_str, page_str = callback.data.split(":")
    prize = await db.get_prize(int(prize_id_str))
    if prize is None:
        await safe_answer_callback(callback, "⚠️ Этот приз уже удалён.", show_alert=True)
        await cb_admin_prizes_list(callback)  # re-render the (now consistent) list
        return
    status = "✅ активен" if prize["is_active"] else "🚫 отключён"
    url = prize["claim_url"] or "не задана (используется ссылка по умолчанию)"
    await safe_edit_message(
        callback,
        f"{prize['emoji']} <b>{prize['name']}</b>\n\n"
        f"Статус: {status}\n"
        f"Ссылка: {url}",
        kb_admin_prize_detail(prize, int(page_str)),
    )
    await safe_answer_callback(callback)


@admin_router.callback_query(F.data.startswith("admin:prize_toggle:"))
async def cb_admin_prize_toggle(callback: CallbackQuery) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    _, _, prize_id_str, page_str = callback.data.split(":")
    new_state = await db.toggle_prize(int(prize_id_str))
    if new_state is None:
        await safe_answer_callback(callback, "⚠️ Приз не найден (возможно, уже удалён).", show_alert=True)
        return
    await safe_answer_callback(callback, "✅ Статус обновлён.")
    logger.info("Admin %s toggled prize %s -> %s", callback.from_user.id, prize_id_str, new_state)
    # Re-render detail view
    callback.data = f"admin:prize:{prize_id_str}:{page_str}"
    await cb_admin_prize_detail(callback)


@admin_router.callback_query(F.data.startswith("admin:prize_url:"))
async def cb_admin_prize_url(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    _, _, prize_id_str, page_str = callback.data.split(":")
    prize = await db.get_prize(int(prize_id_str))
    if prize is None:
        await safe_answer_callback(callback, "⚠️ Приз не найден.", show_alert=True)
        return
    await state.update_data(prize_id=int(prize_id_str), page=int(page_str))
    await state.set_state(AdminStates.editing_prize_url)
    await safe_edit_message(
        callback,
        f"🔗 Введите новую ссылку для {prize['emoji']} <b>{prize['name']}</b>:",
        kb_cancel_fsm(),
    )
    await safe_answer_callback(callback)


@admin_router.message(AdminStates.editing_prize_url)
async def process_prize_url(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    prize_id = data.get("prize_id")
    url = (message.text or "").strip()
    if prize_id is None:
        await message.answer("⚠️ Ошибка сессии. Начните заново из /admin.")
        await state.clear()
        return
    if not url.startswith(("http://", "https://", "tg://")):
        await message.answer("⚠️ Ссылка должна начинаться с http(s):// или tg://. Попробуйте снова.")
        return
    ok = await db.set_prize_url(prize_id, url)
    await state.clear()
    if ok:
        logger.info("Admin %s set claim_url for prize %s", message.from_user.id, prize_id)
        await message.answer("✅ Ссылка для приза обновлена!", reply_markup=kb_admin_main())
    else:
        await message.answer("⚠️ Не удалось обновить (возможно, приз был удалён).", reply_markup=kb_admin_main())


# --- Stats ---------------------------------------------------------------------------

@admin_router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    stats = await db.get_stats()
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{stats.get('total_users', 0)}</b>\n"
        f"🎰 Всего спинов: <b>{stats.get('total_spins', 0)}</b>\n"
        f"🏆 Всего побед: <b>{stats.get('total_wins', 0)}</b>\n"
        f"🟢 Активны за 24ч: <b>{stats.get('active_24h', 0)}</b>"
    )
    await safe_edit_message(callback, text, kb_admin_back())
    await safe_answer_callback(callback)


# --- Reset timers ----------------------------------------------------------------------

@admin_router.callback_query(F.data == "admin:reset_timers")
async def cb_admin_reset_timers(callback: CallbackQuery) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    count = await db.reset_all_timers()
    logger.warning("Admin %s reset spin timers for all users (%s affected)", callback.from_user.id, count)
    await safe_edit_message(callback, f"🔄 Таймеры сброшены для {count} пользователей.", kb_admin_back())
    await safe_answer_callback(callback, "✅ Готово")


# --- Broadcast ---------------------------------------------------------------------------

@admin_router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminStates.broadcasting)
    await safe_edit_message(
        callback,
        "📢 Отправьте сообщение, которое будет разослано всем пользователям бота.",
        kb_cancel_fsm(),
    )
    await safe_answer_callback(callback)


@admin_router.message(AdminStates.broadcasting)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    await state.clear()
    user_ids = await db.get_all_user_ids()
    status_msg = await message.answer(f"📢 Рассылка запущена для {len(user_ids)} пользователей...")

    sent, failed, blocked = 0, 0, 0
    for uid in user_ids:
        try:
            await message.copy_to(chat_id=uid)
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await message.copy_to(chat_id=uid)
                sent += 1
            except TelegramAPIError:
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
        except TelegramAPIError as exc:
            logger.warning("Broadcast failed for %s: %s", uid, exc)
            failed += 1
        await asyncio.sleep(0.05)  # gentle throttling to respect Telegram rate limits

    logger.info("Admin %s broadcast finished: sent=%s blocked=%s failed=%s", message.from_user.id, sent, blocked, failed)
    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📨 Доставлено: {sent}\n"
        f"🚫 Заблокировали бота: {blocked}\n"
        f"⚠️ Ошибок: {failed}"
    )
    await message.answer("Готово.", reply_markup=kb_admin_main())


# --- Full DB wipe ----------------------------------------------------------------------

@admin_router.callback_query(F.data == "admin:wipe_db")
async def cb_admin_wipe_db(callback: CallbackQuery) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    await safe_edit_message(
        callback,
        "❌ <b>ВНИМАНИЕ!</b>\n\nЭто действие удалит ВСЕ данные: пользователей, статистику, "
        "настройки и список призов (после чего призы будут пересозданы по умолчанию).\n\n"
        "Это необратимо. Продолжить?",
        kb_confirm_wipe(),
    )
    await safe_answer_callback(callback)


@admin_router.callback_query(F.data == "admin:wipe_confirm")
async def cb_admin_wipe_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or not is_admin(callback.from_user.id):
        await safe_answer_callback(callback, "⛔ Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    try:
        await db.wipe_all()
        logger.critical("FULL DB WIPE executed by admin %s", callback.from_user.id)
        await safe_edit_message(callback, "✅ База данных полностью сброшена и пересоздана.", kb_admin_back())
    except Exception as exc:  # noqa: BLE001
        logger.exception("wipe_all failed: %s", exc)
        await safe_edit_message(callback, "⚠️ Ошибка при сбросе базы данных. Проверьте логи.", kb_admin_back())
    await safe_answer_callback(callback)


# --- Fallback: unauthorized access attempts on the admin router (extra logging) -------

@admin_router.callback_query()
async def cb_admin_unknown(callback: CallbackQuery) -> None:
    # Reached only if a callback matched the router filter (i.e. IS an admin) but no
    # specific handler above matched the callback_data — respond gracefully.
    await safe_answer_callback(callback, "⚠️ Неизвестное действие.", show_alert=False)


# =====================================================================================
# GLOBAL ERROR HANDLER
# =====================================================================================

async def global_error_handler(event: TelegramObject, exception: Exception) -> bool:  # noqa: ARG001
    logger.exception("Unhandled exception while processing update: %s", exception)
    return True  # mark as handled so aiogram does not re-raise


# =====================================================================================
# APPLICATION BOOTSTRAP
# =====================================================================================

async def on_startup(bot: Bot) -> None:
    logger.info("Starting up MM2 Giveaway Bot...")
    await db.connect()
    await db.init_db()

    if settings.use_webhook:
        if not settings.webhook_host:
            raise RuntimeError("USE_WEBHOOK=true requires WEBHOOK_HOST to be set")
        webhook_url = f"{settings.webhook_host.rstrip('/')}{settings.webhook_path}"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret,
            drop_pending_updates=True,
        )
        logger.info("Webhook set: %s", webhook_url)
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Running in long-polling mode.")

    me = await bot.get_me()
    logger.info("Bot started as @%s (id=%s)", me.username, me.id)


async def on_shutdown(bot: Bot) -> None:
    logger.info("Shutting down MM2 Giveaway Bot...")
    try:
        if settings.use_webhook:
            await bot.delete_webhook()
    except TelegramAPIError as exc:
        logger.warning("delete_webhook failed during shutdown: %s", exc)
    await db.close()
    await bot.session.close()


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    throttler = ThrottlingMiddleware(rate=settings.throttle_seconds)
    dp.message.middleware(throttler)
    dp.callback_query.middleware(throttler)

    # Admin router registered BEFORE user router so admin-only callbacks/commands
    # (which are filtered to admin ids) take precedence for admin users.
    dp.include_router(admin_router)
    dp.include_router(user_router)

    dp.errors.register(global_error_handler)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    return dp


def main() -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    if settings.use_webhook:
        app = web.Application()
        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=settings.webhook_secret,
        ).register(app, path=settings.webhook_path)
        setup_application(app, dp, bot=bot)

        async def health_check(request: web.Request) -> web.Response:  # noqa: ARG001
            return web.Response(text="OK")

        app.router.add_get("/", health_check)
        app.router.add_get("/health", health_check)

        logger.info(
            "Starting aiohttp webhook server on %s:%s%s",
            settings.web_server_host,
            settings.web_server_port,
            settings.webhook_path,
        )
        web.run_app(app, host=settings.web_server_host, port=settings.web_server_port)
    else:
        asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()
