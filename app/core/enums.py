from enum import Enum


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"
    GOD = "god"


class UserBotRole(str, Enum):
    OWNER = "owner"
    VIEWER = "viewer"


class OtpCodeType(str, Enum):
    VERIFY_EMAIL = "verify_email"
    PASSWORD_RESET = "password_reset"


class CryptoInfo(bytes, Enum):
    BOT_TOKEN = b"bot-token"
    WEBHOOK_TOKEN = b"webhook-token"
    WEBHOOK_URL = b"webhook-url"
    WEBHOOK_REDIRECT_TOKEN = b"webhook-redirect-token"


class ChatType(str, Enum):
    PRIVATE = "private"
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"


class MessageType(str, Enum):
    TEXT = "text"
    PHOTO = "photo"
    AUDIO = "audio"
    DOCUMENT = "document"
    VIDEO = "video"
    ANIMATION = "animation"
    VOICE = "voice"
    VIDEO_NOTE = "video_note"
    PAID_MEDIA = "paid_media"
    LOCATION = "location"
    VENUE = "venue"
    CONTACT = "contact"
    POLL = "poll"
    CHECKLIST = "checklist"
    DICE = "dice"
    STICKER = "sticker"
    STORY = "story"
    INVOICE = "invoice"
    GAME = "game"
    GIVEAWAY = "giveaway"
    PASSPORT = "passport"
    SERVICE = "service"


class FileType(str, Enum):
    CHAT_PHOTO = "chat_photo"
    PHOTO = "photo"
    ANIMATION = "animation"
    AUDIO = "audio"
    DOCUMENT = "document"
    VIDEO = "video"
    VIDEO_NOTE = "video_note"
    VOICE = "voice"
    STICKER = "sticker"
    PASSPORT = "passport"


class EntityCheckResultType(int, Enum):
    CHAT = 1
    USER = 2
    MESSAGE = 3
    FILE = 4
