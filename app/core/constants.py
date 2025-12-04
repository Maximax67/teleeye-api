USERNAME_REGEX = r"^[A-Za-z][A-Za-z0-9_]{3,15}$"
PASSWORD_REGEX = r"^(?=.*?[A-Z])(?=.*?[a-z])(?=.*?[0-9]).{8,32}$"
BOT_TOKEN_REGEX = r"^[0-9]{8,10}:[A-Za-z0-9_-]{35}$"
WEBHOOK_SECRET_REGEX = r"^[A-Za-z0-9_-]{1,256}$"

MESSAGE_RETURNED_METHODS = {
    "sendMessage",
    "forwardMessage",
    "sendPhoto",
    "sendAudio",
    "sendDocument",
    "sendVideo",
    "sendAnimation",
    "sendVoice",
    "sendVideoNote",
    "sendPaidMedia",
    "sendLocation",
    "sendVenue",
    "sendContact",
    "sendPoll",
    "sendChecklist",
    "sendDice",
    "sendSticker",
    "sendInvoice",
    "sendGame",
}

EDITED_MESSAGE_RETURNED_METHODS = {
    "editMessageText",
    "editMessageCaption",
    "editMessageMedia",
    "editMessageLiveLocation",
    "stopMessageLiveLocation",
    "editMessageChecklist",
    "editMessageReplyMarkup",
    "setGameScore",
}
