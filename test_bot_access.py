import importlib.util
import os
import sys
import types


def load_bot_with_chat_ids(chat_ids):
    for name in ["telegram", "telegram.ext", "dotenv", "openai", "requests", "bot_mod"]:
        sys.modules.pop(name, None)

    telegram = types.ModuleType("telegram")
    telegram.Update = object
    telegram.InlineKeyboardButton = object
    telegram.InlineKeyboardMarkup = object
    sys.modules["telegram"] = telegram

    telegram_ext = types.ModuleType("telegram.ext")
    telegram_ext.Application = object
    telegram_ext.CommandHandler = object
    telegram_ext.MessageHandler = object
    telegram_ext.CallbackQueryHandler = object
    telegram_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    telegram_ext.filters = types.SimpleNamespace(TEXT=object, COMMAND=object)
    sys.modules["telegram.ext"] = telegram_ext

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv

    requests = types.ModuleType("requests")
    requests.get = lambda *args, **kwargs: None
    requests.post = lambda *args, **kwargs: None
    sys.modules["requests"] = requests

    openai = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, *args, **kwargs):
            pass

    openai.OpenAI = OpenAI
    sys.modules["openai"] = openai

    os.environ["TELEGRAM_CHAT_ID"] = chat_ids

    spec = importlib.util.spec_from_file_location(
        "bot_mod",
        os.path.join(os.path.dirname(__file__), "bot.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_multiple_chat_ids_allowed():
    bot = load_bot_with_chat_ids("931180738,98058079")
    update = types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=98058079))

    assert bot.is_director(update) is True
