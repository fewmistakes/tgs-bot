"""Callback data constants."""

from __future__ import annotations


class CB:
    # main menu
    MAIN = "m:home"
    WALLET = "m:wallet"
    BG_COLOR = "m:bg"
    RESOLUTION = "m:res"
    FORMAT = "m:fmt"
    MEDIA = "m:media"
    EMOJI_COLOR = "m:emoji"
    CONFIGS = "m:cfg"
    WATERMARK = "m:wm"
    PREVIEW = "m:prev"
    REFERRAL = "m:ref"
    REF_COPY = "m:ref:copy"
    INFO = "m:info"
    GIF_HOME = "m:gif"
    OTHER_HOME = "m:other"
    PROMO_INPUT = "m:promo"

    # bg color
    BG_PRESET = "bg:p:"  # bg:p:{hex_no_hash}
    BG_CUSTOM = "bg:custom"

    # resolution
    RES_PRESET = "res:p:"  # res:p:{idx}
    RES_CUSTOM = "res:custom"

    # format
    FMT_SET = "fmt:s:"  # fmt:s:mp4 | fmt:s:gif | fmt:s:video

    # media
    MEDIA_UPLOAD = "media:up"
    MEDIA_DELETE = "media:del"

    # emoji color
    EMC_PRESET = "emc:p:"
    EMC_CUSTOM = "emc:custom"
    EMC_OFF = "emc:off"

    # configs
    CFG_LIST = "cfg:list"
    CFG_LOAD = "cfg:load:"  # cfg:load:{id}
    CFG_DELETE = "cfg:del:"  # cfg:del:{id}
    CFG_SAVE_NEW = "cfg:savenew"
    CFG_FIND_ID = "cfg:findid"  # prompt user for an ID to import

    # watermark
    WM_TEXT = "wm:text"
    WM_COLOR = "wm:color"
    WM_POS = "wm:pos:"  # wm:pos:tl etc
    WM_FONT = "wm:font:"  # wm:font:bold etc
    WM_TOGGLE = "wm:toggle"
    WM_CLEAR = "wm:clear"

    # wallet / payment
    PAY_YOOMONEY = "pay:ym"
    PAY_SBP = "pay:sbp"
    PAY_SBP_ENTER = "pay:sbp:enter"
    PAY_CRYPTO = "pay:crypto"
    PAY_AMOUNT = "pay:amt:"  # pay:amt:{provider}:{rub}
    PAY_CONFIRM = "pay:conf:"  # pay:conf:{payment_id} (user marks paid)
    PAY_RECHECK = "pay:recheck:"  # pay:recheck:{payment_id} (cryptobot)
    PAY_ADMIN_OK = "pay:adm:ok:"  # admin approves yoomoney
    PAY_ADMIN_NO = "pay:adm:no:"

    # preview & convert
    PREVIEW_START = "prev:start"
    CONVERT_NOW = "conv:start"

    # pack creator
    PACK_HOME = "pack:home"
    PACK_NICK = "pack:nick"
    PACK_FONT = "pack:font"
    PACK_FONT_SET = "pack:font:s:"  # pack:font:s:{font_key}
    PACK_FORMAT = "pack:fmt"
    PACK_FORMAT_SET = "pack:fmt:s:"  # pack:fmt:s:sticker | pack:fmt:s:emoji
    PACK_PAINT = "pack:paint"  # legacy, kept for old inline keyboards
    PACK_PAINT_SET = "pack:paint:s:"  # legacy
    PACK_LOGO = "pack:logo"
    PACK_LOGO_DEL = "pack:logo:del"
    PACK_PREVIEW = "pack:preview"  # 3×3 watermarked preview
    PACK_APPLY = "pack:apply"
    PACK_CONFIRM = "pack:confirm"
    PACK_EDITOR = "pack:editor"

    # admin
    ADM_HOME = "a:home"
    ADM_STATS = "a:stats"
    ADM_USERS = "a:users"
    ADM_PRICES = "a:prices"
    ADM_GIFS = "a:gifs"
    ADM_BROADCAST = "a:bcast"
    ADM_GRANT = "a:grant"
    ADM_USER_FIND = "a:userfind"
    ADM_PRICE_SET = "a:priceset"
    ADM_PACK_PRICE_SET = "a:pack:priceset"
    ADM_PACK_LOGO_PRICE_SET = "a:pack:logopriceset"
    ADM_PACK_PASSPORT_PRICE_SET = "a:pack:passportpriceset"
    ADM_FREE_SET = "a:freeset"
    ADM_GRANT_FREE = "a:grant:free"
    ADM_GRANT_RUB = "a:grant:rub"
    ADM_GIF_SET = "a:gif:set:"  # a:gif:set:{screen_key}
    ADM_GIF_DEL = "a:gif:del:"  # a:gif:del:{screen_key}
    ADM_NOTIFY = "a:nm"  # list of notify events with media
    ADM_NOTIFY_SET = "a:nm:set:"  # a:nm:set:{event_key}
    ADM_NOTIFY_DEL = "a:nm:del:"  # a:nm:del:{event_key}
    ADM_YM_URL = "a:ym:url"  # set yoomoney payment link
    ADM_PROMO = "a:promo"  # promocode list / management
    ADM_PROMO_NEW = "a:promo:new"  # create new promo flow
    ADM_PROMO_DEL = "a:promo:del:"  # a:promo:del:{id}
    ADM_EXAMPLES = "a:examples"  # example pack links editor

    # generic
    NOOP = "noop"
    CANCEL = "cancel"
