import os
import re
import asyncio
import logging

from datetime import datetime, timedelta
from collections import defaultdict

import requests
from dotenv import load_dotenv
from openai import OpenAI

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ============================================================
# НАСТРОЙКИ
# ============================================================

load_dotenv()

BITCOP_BASE_URL = os.getenv(
    "BITCOP_BASE_URL",
    "https://orbis.bitcop.ru:4443/api/v1",
)

BITCOP_API_KEY = os.getenv("BITCOP_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-4.1-mini",
)

DIRECTOR_CHAT_IDS = {
    str(part).strip()
    for part in re.split(r"[,;\s]+", str(TELEGRAM_CHAT_ID or "").strip())
    if str(part).strip()
}
DIRECTOR_CHAT_ID = ", ".join(sorted(DIRECTOR_CHAT_IDS))

openai_client = (
    OpenAI(api_key=OPENAI_API_KEY)
    if OPENAI_API_KEY
    else None
)


# ============================================================
# ДОСТУП
# ============================================================

def is_director(update: Update) -> bool:
    if not update.effective_chat:
        return False

    chat_id = str(update.effective_chat.id)
    return bool(DIRECTOR_CHAT_IDS) and chat_id in DIRECTOR_CHAT_IDS


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

def main_menu():
    keyboard = [
        [
            InlineKeyboardButton(
                "📅 Сегодня",
                callback_data="today",
            ),
            InlineKeyboardButton(
                "📊 Неделя",
                callback_data="week",
            ),
        ],
        [
            InlineKeyboardButton(
                "⚠️ Опоздания",
                callback_data="lateness",
            ),
            InlineKeyboardButton(
                "💻 Активность",
                callback_data="activity",
            ),
        ],
        [
            InlineKeyboardButton(
                "🤖 AI-анализ",
                callback_data="ai_analysis",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Обновить меню",
                callback_data="menu",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


async def send_menu(message):
    await message.reply_text(
        "📊 Система контроля рабочего времени\n\n"
        "Выберите нужный раздел:",
        reply_markup=main_menu(),
    )


# ============================================================
# BITCOP API
# ============================================================

def bitcop_request(endpoint, params=None):
    if not BITCOP_API_KEY:
        raise ValueError(
            "Не указан BITCOP_API_KEY в файле .env"
        )

    request_params = dict(params or {})
    request_params["apikey"] = BITCOP_API_KEY

    url = (
        f"{BITCOP_BASE_URL.rstrip('/')}/"
        f"{endpoint.lstrip('/')}"
    )

    try:
        response = requests.get(
            url,
            params=request_params,
            timeout=60,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Не удалось подключиться к Bitcop ({type(exc).__name__})"
        ) from None

    if not response.ok:
        raise RuntimeError(
            f"Bitcop вернул HTTP {response.status_code}"
        )

    result = response.json()

    if isinstance(result, dict):
        if result.get("status") in ("error", "failed"):
            raise RuntimeError(
                f"Bitcop вернул ошибку: {result}"
            )

        if "items" in result:
            result = result["items"]

        elif "data" in result:
            result = result["data"]

    if not isinstance(result, list):
        raise ValueError(
            "Bitcop вернул неожиданный формат данных: "
            f"{type(result).__name__}"
        )

    logger.info(
        "Bitcop: endpoint=%s, записей=%s",
        endpoint,
        len(result),
    )

    return result


# ============================================================
# ПОЛУЧЕНИЕ ДАННЫХ ЗА ОДИН ДЕНЬ
# ============================================================

def get_activity_for_day(day):
    """
    Получает данные строго за один календарный день.

    Важно:
    конечная дата не включается в период.
    Поэтому запрашиваем день от 00:00 до 00:00 следующего дня.
    """

    next_day = day + timedelta(days=1)

    begin = day.strftime("%Y%m%dT000000")
    end = next_day.strftime("%Y%m%dT000000")

    logger.info(
        "Запрос Bitcop за день: %s — %s",
        begin,
        end,
    )

    return bitcop_request(
        "/activity",
        {
            "begin": begin,
            "end": end,
        },
    )


# ============================================================
# БЕЗОПАСНОЕ ПРЕОБРАЗОВАНИЕ ЧИСЕЛ
# ============================================================

def to_int(value):
    if value is None or value == "":
        return 0

    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


# ============================================================
# ОБЪЕДИНЕНИЕ ЗАПИСЕЙ СОТРУДНИКОВ
# ============================================================

SUM_FIELDS = (
    "total_time",
    "active_time",
    "down_time",
    "late_count",
    "late_time",
    "early_end_count",
    "early_end_time",
    "schedule_days",
    "work_days",
    "absenteism",
)


def employee_key(item):
    """
    Предпочитаем ID сотрудника.
    Если ID отсутствует, используем имя.
    """

    employee_id = item.get("id")

    if employee_id is not None and str(employee_id).strip():
        return ("id", str(employee_id))

    return (
        "name",
        str(item.get("name") or "Неизвестный сотрудник")
        .strip()
        .casefold(),
    )


def prepare_daily_data(activity):
    """
    Преобразует исходные записи Bitcop.

    Если в ответе одного дня сотрудник присутствует несколько раз,
    объединяет записи по ID, не смешивая разных сотрудников
    только из-за одинаковых фамилий.
    """

    employees = {}

    if not isinstance(activity, list):
        return []

    for item in activity:
        if not isinstance(item, dict):
            continue

        key = employee_key(item)

        if key not in employees:
            employees[key] = {
                "id": item.get("id"),
                "name": item.get("name") or "Неизвестный сотрудник",
                "total_time": 0,
                "active_time": 0,
                "down_time": 0,
                "late_count": 0,
                "late_time": 0,
                "early_end_count": 0,
                "early_end_time": 0,
                "schedule_days": 0,
                "work_days": 0,
                "absenteism": 0,
            }

        employee = employees[key]

        for source, target in (
            ("totalTime", "total_time"),
            ("activeTime", "active_time"),
            ("downTime", "down_time"),
            ("lateCount", "late_count"),
            ("lateTime", "late_time"),
            ("earlyEndCount", "early_end_count"),
            ("earlyEndTime", "early_end_time"),
            ("scheduleDayCount", "schedule_days"),
            ("workDayCount", "work_days"),
            ("absenteism", "absenteism"),
        ):
            employee[target] += to_int(item.get(source))

    result = list(employees.values())

    for employee in result:
        employee["activity_percent"] = activity_percent(
            employee["active_time"],
            employee["total_time"],
        )

    return result


# ============================================================
# ПЕРИОДЫ
# ============================================================

def today_period():
    """
    Возвращает сегодняшний день и исключающую конечную дату.
    """

    today = datetime.now().date()

    return today, today + timedelta(days=1)


def previous_week_period():
    """
    Возвращает предыдущую календарную неделю:
    с понедельника по воскресенье включительно.
    """

    today = datetime.now().date()

    current_monday = today - timedelta(
        days=today.weekday()
    )

    previous_monday = current_monday - timedelta(days=7)

    return previous_monday, current_monday


# ============================================================
# АГРЕГАЦИЯ ДАННЫХ ЗА НЕСКОЛЬКО ДНЕЙ
# ============================================================

def get_activity_period(begin_date, end_date):
    """
    Получает данные отдельно за каждый день периода.

    end_date не включается.

    Это позволяет избежать зависимости от того,
    как Bitcop агрегирует длинные интервалы в одном запросе.
    """

    all_employees = {}

    current_date = begin_date

    while current_date < end_date:
        daily_activity = get_activity_for_day(current_date)

        daily_employees = prepare_daily_data(daily_activity)

        logger.info(
            "Дата %s: получено сотрудников: %s",
            current_date.strftime("%d.%m.%Y"),
            len(daily_employees),
        )

        for employee in daily_employees:
            key = (
                ("id", str(employee["id"]))
                if employee["id"] is not None
                and str(employee["id"]).strip()
                else (
                    "name",
                    employee["name"].strip().casefold(),
                )
            )

            if key not in all_employees:
                all_employees[key] = {
                    "id": employee["id"],
                    "name": employee["name"],
                    "total_time": 0,
                    "active_time": 0,
                    "down_time": 0,
                    "late_count": 0,
                    "late_time": 0,
                    "early_end_count": 0,
                    "early_end_time": 0,
                    "schedule_days": 0,
                    "work_days": 0,
                    "absenteism": 0,
                }

            target = all_employees[key]

            for field in SUM_FIELDS:
                target[field] += employee[field]

        current_date += timedelta(days=1)

    result = list(all_employees.values())

    # Процент считаем после суммирования времени за весь период.
    for employee in result:
        employee["activity_percent"] = activity_percent(
            employee["active_time"],
            employee["total_time"],
        )

    result.sort(
        key=lambda employee: employee["name"].casefold()
    )

    logger.info(
        "Итог периода %s — %s: сотрудников=%s",
        begin_date,
        end_date - timedelta(days=1),
        len(result),
    )

    return result


# ============================================================
# ФОРМАТИРОВАНИЕ ВРЕМЕНИ
# ============================================================

def seconds_to_time(seconds):
    seconds = max(0, to_int(seconds))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    return f"{hours} ч {minutes} мин"


def activity_percent(active_time, total_time):
    active_time = to_int(active_time)
    total_time = to_int(total_time)

    if total_time <= 0:
        return 0.0

    return round(
        active_time / total_time * 100,
        1,
    )


# ============================================================
# ПОЛУЧЕНИЕ ОТЧЁТА
# ============================================================

def get_report_data(period):
    if period == "today":
        begin_date, end_date = today_period()

    elif period == "week":
        begin_date, end_date = previous_week_period()

    else:
        raise ValueError(
            f"Неизвестный период отчёта: {period}"
        )

    data = get_activity_period(
        begin_date,
        end_date,
    )

    return data, begin_date, end_date


# ============================================================
# ФОРМАТ ОТЧЁТА
# ============================================================

def format_report(data, begin_date, end_date, title):
    report = (
        f"{title}\n\n"
        f"📅 Период: {begin_date.strftime('%d.%m.%Y')} — "
        f"{(end_date - timedelta(days=1)).strftime('%d.%m.%Y')}\n\n"
    )

    if not data:
        return report + (
            "❌ Данные отсутствуют. "
            "Проверьте ответ Bitcop и выбранный период."
        )

    for employee in data:
        report += (
            f"👤 {employee['name']}\n"
            f"⏱ Общее время: "
            f"{seconds_to_time(employee['total_time'])}\n"
            f"💻 Активность ПК: "
            f"{employee['activity_percent']}%\n"
        )

        if employee["late_count"] > 0:
            report += (
                f"⚠️ Опозданий: {employee['late_count']} "
                f"({seconds_to_time(employee['late_time'])})\n"
            )

        if employee["early_end_count"] > 0:
            report += (
                f"🔚 Ранних завершений: "
                f"{employee['early_end_count']}\n"
                f"Время: "
                f"{seconds_to_time(employee['early_end_time'])}\n"
            )

        report += "\n"

    return report


# ============================================================
# ОТПРАВКА ДЛИННЫХ ОТЧЁТОВ
# ============================================================

async def send_long_message(
    message,
    text,
    reply_markup=None,
):
    """
    Telegram ограничивает длину одного текстового сообщения.
    Разбиваем длинный отчёт на части по границам строк.
    """

    limit = 3800
    parts = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            parts.append(current)
            current = ""

        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""

            parts.append(line[:limit])
            line = line[limit:]

        current += line

    if current:
        parts.append(current)

    for index, part in enumerate(parts):
        markup = (
            reply_markup
            if index == len(parts) - 1
            else None
        )

        await message.reply_text(
            part,
            reply_markup=markup,
        )


# ============================================================
# СЕГОДНЯ
# ============================================================

async def show_today(message):
    try:
        data, begin_date, end_date = await asyncio.to_thread(
            get_report_data,
            "today",
        )

        report = format_report(
            data,
            begin_date,
            end_date,
            "📅 СТАТИСТИКА ЗА СЕГОДНЯ",
        )

        await send_long_message(
            message,
            report,
            main_menu(),
        )

    except Exception:
        logger.exception("Ошибка отчёта за сегодня")

        await message.reply_text(
            "❌ Не удалось получить данные из Bitcop. "
            "Проверьте подключение и журналы сервера.",
            reply_markup=main_menu(),
        )


# ============================================================
# НЕДЕЛЯ
# ============================================================

async def show_week(message):
    try:
        data, begin_date, end_date = await asyncio.to_thread(
            get_report_data,
            "week",
        )

        report = format_report(
            data,
            begin_date,
            end_date,
            "📊 ОТЧЁТ ЗА ПРОШЛУЮ НЕДЕЛЮ",
        )

        await send_long_message(
            message,
            report,
            main_menu(),
        )

    except Exception:
        logger.exception("Ошибка недельного отчёта")

        await message.reply_text(
            "❌ Не удалось получить данные из Bitcop. "
            "Проверьте подключение и журналы сервера.",
            reply_markup=main_menu(),
        )


# ============================================================
# ОПОЗДАНИЯ
# ============================================================

async def show_lateness(message):
    try:
        data, begin_date, end_date = await asyncio.to_thread(
            get_report_data,
            "week",
        )

        late_employees = [
            employee
            for employee in data
            if employee["late_count"] > 0
        ]

        report = (
            "⚠️ ОПОЗДАНИЯ\n\n"
            f"📅 {begin_date.strftime('%d.%m.%Y')} — "
            f"{(end_date - timedelta(days=1)).strftime('%d.%m.%Y')}\n\n"
        )

        if not late_employees:
            report += "✅ Опозданий не обнаружено."

        else:
            late_employees.sort(
                key=lambda employee: employee["late_count"],
                reverse=True,
            )

            for employee in late_employees:
                report += (
                    f"👤 {employee['name']}\n"
                    f"Количество: {employee['late_count']}\n"
                    f"Время: "
                    f"{seconds_to_time(employee['late_time'])}\n\n"
                )

        await send_long_message(
            message,
            report,
            main_menu(),
        )

    except Exception:
        logger.exception("Ошибка отчёта по опозданиям")

        await message.reply_text(
            "❌ Не удалось получить данные из Bitcop.",
            reply_markup=main_menu(),
        )


# ============================================================
# АКТИВНОСТЬ
# ============================================================

async def show_activity(message):
    try:
        data, begin_date, end_date = await asyncio.to_thread(
            get_report_data,
            "week",
        )

        data.sort(
            key=lambda employee: employee["activity_percent"]
        )

        report = (
            "💻 АКТИВНОСТЬ СОТРУДНИКОВ\n\n"
            f"📅 {begin_date.strftime('%d.%m.%Y')} — "
            f"{(end_date - timedelta(days=1)).strftime('%d.%m.%Y')}\n\n"
        )

        if not data:
            report += "❌ Данные отсутствуют."

        else:
            for employee in data:
                report += (
                    f"👤 {employee['name']}\n"
                    f"💻 Активность ПК: "
                    f"{employee['activity_percent']}%\n"
                    f"⏱ Общее время: "
                    f"{seconds_to_time(employee['total_time'])}\n"
                    f"🟢 Активное время: "
                    f"{seconds_to_time(employee['active_time'])}\n"
                    f"⚪ Неактивное время: "
                    f"{seconds_to_time(employee['down_time'])}\n\n"
                )

        await send_long_message(
            message,
            report,
            main_menu(),
        )

    except Exception:
        logger.exception("Ошибка отчёта по активности")

        await message.reply_text(
            "❌ Не удалось получить данные из Bitcop.",
            reply_markup=main_menu(),
        )


# ============================================================
# ДАННЫЕ ДЛЯ AI
# ============================================================

def make_data_text(data):
    if not data:
        return "Данные о сотрудниках отсутствуют."

    lines = []

    for employee in data:
        lines.append(
            f"""
Сотрудник: {employee['name']}
Общее время по данным системы: {seconds_to_time(employee['total_time'])}
Активное время за компьютером: {seconds_to_time(employee['active_time'])}
Неактивное время: {seconds_to_time(employee['down_time'])}
Уровень компьютерной активности: {employee['activity_percent']} процентов
Количество опозданий: {employee['late_count']}
Время опозданий: {seconds_to_time(employee['late_time'])}
Количество ранних завершений: {employee['early_end_count']}
Время ранних завершений: {seconds_to_time(employee['early_end_time'])}
"""
        )

    return "\n".join(lines)


def contains_digits(text):
    return bool(re.search(r"\d", text or ""))


# ============================================================
# OPENAI: АНАЛИЗ
# ============================================================

async def ask_openai(question, data, begin_date, end_date):
    if not openai_client:
        return (
            "❌ OpenAI не настроен.\n"
            "Проверьте OPENAI_API_KEY в файле .env."
        )

    if not data:
        return (
            "Выводы\n"
            "Недостаточно данных для оценки рабочей активности.\n\n"
            "Рекомендации\n"
            "Проверьте доступность данных в Bitcop "
            "и повторите анализ."
        )

    data_text = make_data_text(data)

    period_text = (
        f"{begin_date.strftime('%d.%m.%Y')} — "
        f"{(end_date - timedelta(days=1)).strftime('%d.%m.%Y')}"
    )

    prompt = f"""
Ты — помощник руководителя, анализирующий данные Bitcop.

Период анализа: {period_text}

Данные:
{data_text}

Запрос руководителя:
{question}

Подготовь краткий управленческий анализ на русском языке.

ФОРМАТ:

Выводы
- Краткая оценка ситуации.
- На каких сотрудников стоит обратить внимание и почему,
  если это подтверждается данными.

Рекомендации
- Конкретные действия руководителя.
- Если данных недостаточно, прямо укажи это.

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА:

1. Только два раздела: «Выводы» и «Рекомендации».
2. Не используй цифры, числа, проценты, даты, часы и минуты.
3. Не перечисляй исходную статистику.
4. Описывай ситуацию качественно и кратко.
5. Не придумывай отсутствующие в данных обстоятельства.
6. Не утверждай, что сотрудник отсутствовал, если это не подтверждено.
7. Низкая активность за компьютером сама по себе не доказывает
   низкую продуктивность.
8. Не делай выводов о характере, здоровье и мотивации.
9. Используй нейтральный деловой язык.
10. Не называй сотрудников проблемными и не обвиняй их.
11. Не добавляй вступление и заключение.
12. Учитывай запрос руководителя, но не делай выводов,
    не подтверждённых данными.
"""

    try:
        response = await asyncio.to_thread(
            openai_client.responses.create,
            model=OPENAI_MODEL,
            input=prompt,
        )

        answer = (response.output_text or "").strip()

        if not answer:
            raise ValueError(
                "OpenAI вернул пустой ответ"
            )

        # Повторная попытка, если появились числа.
        if contains_digits(answer):
            rewrite_prompt = f"""
Перепиши ответ, сохранив подтверждённый данными смысл.

Требования:
- Только разделы «Выводы» и «Рекомендации».
- Не используй цифры, числа, даты, проценты, часы и минуты.
- Не добавляй новых фактов.
- Краткий деловой русский язык.
- Оставь только качественные выводы и рекомендации.

Ответ:
{answer}
"""

            response = await asyncio.to_thread(
                openai_client.responses.create,
                model=OPENAI_MODEL,
                input=rewrite_prompt,
            )

            answer = (response.output_text or "").strip()

        if not answer or contains_digits(answer):
            logger.warning(
                "AI не смог сформировать ответ без чисел"
            )

            return (
                "Выводы\n"
                "Не удалось подготовить корректный текстовый анализ.\n\n"
                "Рекомендации\n"
                "Повторите запрос и проверьте исходные данные Bitcop."
            )

        return answer

    except Exception:
        logger.exception("Ошибка OpenAI")

        return (
            "❌ Не удалось выполнить AI-анализ.\n"
            "Проверьте подключение к OpenAI и настройки API."
        )


# ============================================================
# AI-АНАЛИЗ ЗА НЕДЕЛЮ
# ============================================================

async def show_ai_analysis(message):
    try:
        await message.reply_text(
            "🤖 Получаю данные Bitcop и анализирую ситуацию..."
        )

        data, begin_date, end_date = await asyncio.to_thread(
            get_report_data,
            "week",
        )

        answer = await ask_openai(
            question=(
                "Подготовь краткие выводы о рабочей активности "
                "сотрудников. Укажи, на что стоит обратить внимание, "
                "и дай практические рекомендации руководителю."
            ),
            data=data,
            begin_date=begin_date,
            end_date=end_date,
        )

        await send_long_message(
            message,
            "🤖 AI-АНАЛИЗ\n\n" + answer,
            main_menu(),
        )

    except Exception:
        logger.exception("Ошибка AI-анализа")

        await message.reply_text(
            "❌ Не удалось получить данные для AI-анализа.",
            reply_markup=main_menu(),
        )


# ============================================================
# /START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_director(update):
        await update.effective_message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    await update.effective_message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Я AI-помощник руководителя.\n\n"
        "Вы можете посмотреть статистику сотрудников "
        "или получить краткие выводы и рекомендации.",
        reply_markup=main_menu(),
    )


# ============================================================
# /HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_director(update):
        await update.effective_message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    await update.effective_message.reply_text(
        "📖 Помощь\n\n"
        "Используйте кнопки меню или напишите вопрос.\n\n"
        "Примеры:\n"
        "• Сделай анализ за неделю.\n"
        "• На что обратить внимание?\n"
        "• Какие рекомендации для руководителя?\n"
        "• Проанализируй ситуацию с опозданиями.\n"
        "• Покажи активность сотрудников.",
        reply_markup=main_menu(),
    )


# ============================================================
# КОМАНДЫ
# ============================================================

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_director(update):
        await update.effective_message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    await show_today(update.effective_message)


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_director(update):
        await update.effective_message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    await show_week(update.effective_message)


async def lateness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_director(update):
        await update.effective_message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    await show_lateness(update.effective_message)


async def activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_director(update):
        await update.effective_message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    await show_activity(update.effective_message)


# ============================================================
# ОБЫЧНЫЙ ТЕКСТ -> AI
# ============================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_director(update):
        await update.effective_message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    question = (
        update.effective_message.text or ""
    ).strip()

    if not question:
        return

    if question.casefold() in (
        "меню",
        "menu",
        "начало",
        "главное меню",
    ):
        await send_menu(update.effective_message)
        return

    try:
        await update.effective_message.reply_text(
            "🤖 Получаю данные и подготавливаю анализ..."
        )

        data, begin_date, end_date = await asyncio.to_thread(
            get_report_data,
            "week",
        )

        answer = await ask_openai(
            question=question,
            data=data,
            begin_date=begin_date,
            end_date=end_date,
        )

        await send_long_message(
            update.effective_message,
            answer,
            main_menu(),
        )

    except Exception:
        logger.exception(
            "Ошибка обработки текстового запроса"
        )

        await update.effective_message.reply_text(
            "❌ Не удалось обработать запрос. "
            "Проверьте доступность Bitcop.",
            reply_markup=main_menu(),
        )


# ============================================================
# ОБРАБОТКА КНОПОК
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not is_director(update):
        await query.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )
        return

    await query.answer()

    message = query.message

    if query.data == "menu":
        await message.reply_text(
            "📊 Главное меню:",
            reply_markup=main_menu(),
        )

    elif query.data == "today":
        await show_today(message)

    elif query.data == "week":
        await show_week(message)

    elif query.data == "lateness":
        await show_lateness(message)

    elif query.data == "activity":
        await show_activity(message)

    elif query.data == "ai_analysis":
        await show_ai_analysis(message)


# ============================================================
# ЗАПУСК
# ============================================================

def main():
    logger.info("Telegram AI Bot запускается")
    logger.info("Bitcop: %s", BITCOP_BASE_URL)
    logger.info("OpenAI model: %s", OPENAI_MODEL)

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN не найден в .env"
        )

    if not BITCOP_API_KEY:
        raise ValueError(
            "BITCOP_API_KEY не найден в .env"
        )

    if not OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY не найден в .env"
        )

    if not DIRECTOR_CHAT_ID:
        raise ValueError(
            "TELEGRAM_CHAT_ID не найден в .env"
        )

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("today", today)
    )

    application.add_handler(
        CommandHandler("week", week)
    )

    application.add_handler(
        CommandHandler("lateness", lateness)
    )

    application.add_handler(
        CommandHandler("activity", activity)
    )

    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler,
        )
    )

    logger.info("Бот запущен и ожидает сообщения")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()