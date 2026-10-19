"""
Premium emoji IDs and helper functions.
All HTML messages must use <tg-emoji emoji-id="..."> wrappers.
Inline keyboard buttons must use icon_custom_emoji_id (NOT plain emoji in text).
"""

from __future__ import annotations


class EmojiID:
    SETTINGS = "5870982283724328568"  # ⚙
    PROFILE = "5870994129244131212"  # 👤
    PEOPLE = "5870772616305839506"  # 👥
    PERSON_CHECK = "5891207662678317861"  # 👤✓
    PERSON_X = "5893192487324880883"  # 👤✗
    FILE = "5870528606328852614"  # 📁
    SMILE = "5870764288364252592"  # 🙂
    GROWTH = "5870930636742595124"  # 📊
    STATS = "5870921681735781843"  # 📊
    HOME = "5873147866364514353"  # 🏘
    LOCK_CLOSED = "6037249452824072506"  # 🔒
    LOCK_OPEN = "6037496202990194718"  # 🔓
    MEGAPHONE = "6039422865189638057"  # 📣
    CHECK = "5870633910337015697"  # ✅
    CROSS = "5870657884844462243"  # ❌
    PENCIL = "5870676941614354370"  # 🖋
    TRASH = "5870875489362513438"  # 🗑
    DOWN_ARROW = "5893057118545646106"  # 📰⬇
    PAPERCLIP = "6039451237743595514"  # 📎
    LINK = "5769289093221454192"  # 🔗
    INFO = "6028435952299413210"  # ℹ
    BOT = "6030400221232501136"  # 🤖
    EYE = "6037397706505195857"  # 👁
    EYE_HIDDEN = "6037243349675544634"  # 👁‍🗨
    UPLOAD = "5963103826075456248"  # ⬆
    DOWNLOAD = "6039802767931871481"  # ⬇
    BELL = "6039486778597970865"  # 🔔
    GIFT = "6032644646587338669"  # 🎁
    CLOCK = "5983150113483134607"  # ⏰
    PARTY = "6041731551845159060"  # 🎉
    FONT = "5870801517140775623"  # 🔗 (font)
    WRITE = "5870753782874246579"  # ✍
    MEDIA_PHOTO = "6035128606563241721"  # 🖼
    GEO = "6042011682497106307"  # 📍
    WALLET = "5769126056262898415"  # 👛
    BOX = "5884479287171485878"  # 📦
    CRYPTOBOT = "5260752406890711732"  # 👾
    CALENDAR = "5890937706803894250"  # 📅
    TAG = "5886285355279193209"  # 🏷
    TIME_PASSED = "5775896410780079073"  # 🕓
    APPS = "5778672437122045013"  # 📦
    BRUSH = "6050679691004612757"  # 🖌
    ADD_TEXT = "5771851822897566479"  # 🔡
    RESOLUTION = "5778479949572738874"  # ↔
    MONEY = "5904462880941545555"  # 🪙
    SEND_MONEY = "5890848474563352982"  # 🪙↗
    RECEIVE_MONEY = "5879814368572478751"  # 🏧
    CODE = "5940433880585605708"  # 🔨</>
    LOADING = "5345906554510012647"  # 🔄
    BACK = "5256999125634000597"  # ◁ (back arrow — premium variant)


def e(emoji_id: str, fallback: str) -> str:
    """Render <tg-emoji> tag for HTML messages.

    Example:
        e(EmojiID.SETTINGS, "⚙️") -> '<tg-emoji emoji-id="...">⚙️</tg-emoji>'
    """
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


# Most-used pre-rendered tags (saves typing in handlers)
class E:
    SETTINGS = e(EmojiID.SETTINGS, "⚙️")
    FILE = e(EmojiID.FILE, "📁")
    BOT = e(EmojiID.BOT, "🤖")
    BRUSH = e(EmojiID.BRUSH, "🖌")
    MEDIA = e(EmojiID.MEDIA_PHOTO, "🖼")
    CHECK = e(EmojiID.CHECK, "✅")
    CROSS = e(EmojiID.CROSS, "❌")
    LOADING = e(EmojiID.LOADING, "🔄")
    PARTY = e(EmojiID.PARTY, "🎉")
    INFO = e(EmojiID.INFO, "ℹ️")
    UPLOAD = e(EmojiID.UPLOAD, "⬆️")
    DOWNLOAD = e(EmojiID.DOWNLOAD, "⬇️")
    RESOLUTION = e(EmojiID.RESOLUTION, "↔️")
    PAPERCLIP = e(EmojiID.PAPERCLIP, "📎")
    PENCIL = e(EmojiID.PENCIL, "🖋")
    TRASH = e(EmojiID.TRASH, "🗑")
    GIFT = e(EmojiID.GIFT, "🎁")
    CODE = e(EmojiID.CODE, "🔨")
    EYE = e(EmojiID.EYE, "👁")
    EYE_HIDDEN = e(EmojiID.EYE_HIDDEN, "👁‍🗨")
    BACK = e(EmojiID.BACK, "◁")
    WARN = e(EmojiID.MEGAPHONE, "📣")
    TAG = e(EmojiID.TAG, "🏷")
    TIME = e(EmojiID.CLOCK, "⏰")
    WRITE = e(EmojiID.WRITE, "✍")
    WALLET = e(EmojiID.WALLET, "👛")
    MONEY = e(EmojiID.MONEY, "🪙")
    SEND_MONEY = e(EmojiID.SEND_MONEY, "🪙")
    RECEIVE = e(EmojiID.RECEIVE_MONEY, "🏧")
    CRYPTO = e(EmojiID.CRYPTOBOT, "👾")
    BOX = e(EmojiID.BOX, "📦")
    APPS = e(EmojiID.APPS, "📦")
    STATS = e(EmojiID.STATS, "📊")
    GROWTH = e(EmojiID.GROWTH, "📊")
    PEOPLE = e(EmojiID.PEOPLE, "👥")
    PERSON_OK = e(EmojiID.PERSON_CHECK, "👤")
    PERSON_X = e(EmojiID.PERSON_X, "👤")
    LOCK = e(EmojiID.LOCK_CLOSED, "🔒")
    UNLOCK = e(EmojiID.LOCK_OPEN, "🔓")
    BELL = e(EmojiID.BELL, "🔔")
    GEO = e(EmojiID.GEO, "📍")
    SMILE = e(EmojiID.SMILE, "🙂")
    HOME = e(EmojiID.HOME, "🏘")
    CALENDAR = e(EmojiID.CALENDAR, "📅")
    FONT = e(EmojiID.FONT, "🔗")
    ADD_TEXT = e(EmojiID.ADD_TEXT, "🔡")
