import os
import time
import requests
import webbrowser
from datetime import date, timedelta
from html import escape

from dotenv import load_dotenv
from google import genai


# ============================================================
# НАСТРОЙКИ
# ============================================================

load_dotenv()

BASE_URL = os.getenv("BITCOP_BASE_URL")
API_KEY = os.getenv("BITCOP_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BASE_URL:
    raise Exception("Не указан BITCOP_BASE_URL в файле .env")

if not API_KEY:
    raise Exception("Не указан BITCOP_API_KEY в файле .env")

if not GEMINI_API_KEY:
    raise Exception("Не указан GEMINI_API_KEY в файле .env")


# Gemini
gemini_client = genai.Client(
    api_key=GEMINI_API_KEY
)

GEMINI_MODEL = "gemini-3.8-flash"


# ============================================================
# ПОЛУЧЕНИЕ ДАННЫХ ИЗ BITCOP
# ============================================================

def bitcop_request(endpoint, params=None):

    if params is None:
        params = {}

    params["apikey"] = API_KEY

    url = f"{BASE_URL}/{endpoint}"

    response = requests.get(
        url,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# РАСЧЁТ ПРОШЛОЙ НЕДЕЛИ
# ============================================================

today = date.today()

this_monday = today - timedelta(
    days=today.weekday()
)

last_monday = this_monday - timedelta(days=7)

last_sunday = this_monday - timedelta(days=1)

begin = last_monday.strftime(
    "%Y%m%dT000000"
)

end = this_monday.strftime(
    "%Y%m%dT000000"
)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def seconds_to_time(seconds):

    if seconds is None:
        seconds = 0

    seconds = abs(int(seconds))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours} ч {minutes} мин"

    return f"{minutes} мин"


def seconds_to_hours(seconds):

    if seconds is None:
        seconds = 0

    return round(
        abs(int(seconds)) / 3600,
        2
    )


def activity_percent(active_time, total_time):

    if not total_time or total_time <= 0:
        return 0

    return round(
        (active_time / total_time) * 100,
        1
    )


def get_activity_level(percent):

    if percent >= 70:
        return "Высокая"

    if percent >= 40:
        return "Средняя"

    if percent >= 20:
        return "Низкая"

    return "Очень низкая"


def html_ai_text(text):

    """
    Безопасно переводим ответ Gemini в HTML.
    """

    if not text:
        return "AI-анализ не получен."

    text = escape(text)

    paragraphs = text.split("\n")

    result = []

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if paragraph:
            result.append(
                f"<p>{paragraph}</p>"
            )

    return "\n".join(result)


# ============================================================
# GEMINI — АНАЛИЗ СОТРУДНИКА
# ============================================================

def analyze_employee(employee):

    name = employee["name"]

    total_time = employee["total_time"]
    active_time = employee["active_time"]
    down_time = employee["down_time"]

    active_percent = employee["active_percent"]

    late_count = employee["late_count"]
    early_end_count = employee["early_end_count"]

    absenteeism = employee["absenteeism"]

    work_days = employee["work_days"]
    schedule_days = employee["schedule_days"]

    prompt = f"""
Ты являешься аналитиком рабочего времени сотрудников.

Нужно провести аккуратный анализ данных сотрудника за одну рабочую неделю.

ВАЖНО:
- Не придумывай факты, которых нет в данных.
- Не называй процент активности напрямую производительностью.
- Низкая компьютерная активность не обязательно означает плохую работу.
- Учитывай, что сотрудник может выполнять задачи вне компьютера.
- Не делай медицинских, психологических или личных выводов.
- Не обвиняй сотрудника в нарушениях.
- Формулируй выводы как рекомендации для руководителя.
- Не нужно повторять все цифры отдельным списком.

Данные:

Сотрудник: {name}

Рабочее время: {seconds_to_time(total_time)}
Активное время: {seconds_to_time(active_time)}
Простой: {seconds_to_time(down_time)}

Активность: {active_percent}%

Опоздания: {late_count}
Ранние уходы: {early_end_count}

Пропуски: {absenteeism}

Рабочие дни: {work_days}
Запланированные рабочие дни: {schedule_days}

Сформируй анализ в следующем формате:

Оценка:
Краткая общая оценка ситуации.

Что хорошо:
Что выглядит положительно по имеющимся данным.

На что обратить внимание:
Какие показатели требуют внимания.

Рекомендация:
Что руководителю стоит проверить или обсудить.

Пиши кратко, профессионально, на русском языке.
Не используй Markdown-заголовки.
"""

    for attempt in range(3):

        try:

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )

            if response.text:
                return response.text.strip()

        except Exception as e:

            error_text = str(e)

            print(
                f"⚠️ Ошибка Gemini для {name}: {error_text}"
            )

            # 503 — временная перегрузка
            if "503" in error_text:

                if attempt < 2:

                    wait_time = 5 * (attempt + 1)

                    print(
                        f"Повтор через {wait_time} сек..."
                    )

                    time.sleep(wait_time)

                    continue

            break

    return (
        "AI-анализ временно недоступен. "
        "Основные показатели доступны в отчёте."
    )


# ============================================================
# GEMINI — ОБЩИЙ АНАЛИЗ КОМАНДЫ
# ============================================================

def analyze_team(report_data, overall_percent):

    employees_text = ""

    for employee in report_data:

        employees_text += f"""
Сотрудник: {employee["name"]}
Рабочее время: {seconds_to_time(employee["total_time"])}
Активное время: {seconds_to_time(employee["active_time"])}
Простой: {seconds_to_time(employee["down_time"])}
Активность: {employee["active_percent"]}%
Опоздания: {employee["late_count"]}
Ранние уходы: {employee["early_end_count"]}
Рабочие дни: {employee["work_days"]}/{employee["schedule_days"]}
"""

    prompt = f"""
Ты являешься аналитиком рабочего времени команды.

Проанализируй недельные показатели сотрудников.

Общая активность команды:
{overall_percent}%

Данные сотрудников:

{employees_text}

ВАЖНЫЕ ПРАВИЛА:

- Не называй процент активности производительностью.
- Не делай вывод, что сотрудник плохо работает только из-за низкой активности.
- Учитывай возможность работы вне компьютера.
- Не придумывай причины.
- Не обвиняй сотрудников.
- Не используй медицинские или психологические характеристики.
- Выделяй только тенденции, которые действительно видны в данных.

Сформируй:

Общая оценка:
Краткий вывод по команде.

Положительные моменты:
Что выглядит хорошо.

Зоны внимания:
Что руководителю стоит проверить.

Рекомендации:
Что можно сделать для улучшения контроля рабочего времени.

Пиши кратко и профессионально на русском языке.
Не используй Markdown-заголовки.
"""

    for attempt in range(3):

        try:

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )

            if response.text:
                return response.text.strip()

        except Exception as e:

            error_text = str(e)

            print(
                f"⚠️ Ошибка общего анализа Gemini: {error_text}"
            )

            if "503" in error_text:

                if attempt < 2:

                    wait_time = 5 * (attempt + 1)

                    print(
                        f"Повтор через {wait_time} сек..."
                    )

                    time.sleep(wait_time)

                    continue

            break

    return (
        "Общий AI-анализ временно недоступен. "
        "Используйте показатели таблицы для анализа."
    )


# ============================================================
# НАЧАЛО
# ============================================================

print("=" * 60)
print("BITCOP — НЕДЕЛЬНЫЙ AI-ОТЧЁТ")
print("=" * 60)

print()

print(
    f"Период: "
    f"{last_monday.strftime('%d.%m.%Y')} — "
    f"{last_sunday.strftime('%d.%m.%Y')}"
)

print()

print("Получаем данные из Bitcop...")


# ============================================================
# BITCOP
# ============================================================

try:

    data = bitcop_request(
        "activity",
        {
            "begin": begin,
            "end": end
        }
    )

except requests.exceptions.RequestException as e:

    print()
    print("❌ Ошибка подключения к Bitcop:")
    print(e)

    exit()


# ============================================================
# ПРОВЕРКА
# ============================================================

if data.get("status") != "success":

    print()
    print("❌ Bitcop вернул ошибку:")
    print(data)

    exit()


employees = data.get(
    "items",
    []
)

if not employees:

    print()
    print("⚠️ За выбранный период данных нет.")

    exit()


print()
print(
    f"✅ Получены данные по сотрудникам: "
    f"{len(employees)}"
)


# ============================================================
# ПОДГОТОВКА ДАННЫХ
# ============================================================

report_data = []


for employee in employees:

    name = employee.get(
        "name",
        "Неизвестный сотрудник"
    )

    total_time = int(
        employee.get("totalTime", 0) or 0
    )

    active_time = int(
        employee.get("activeTime", 0) or 0
    )

    down_time = int(
        employee.get("downTime", 0) or 0
    )

    late_count = int(
        employee.get("lateCount", 0) or 0
    )

    early_end_count = int(
        employee.get("earlyEndCount", 0) or 0
    )

    absenteeism = int(
        employee.get("absenteism", 0) or 0
    )

    work_days = int(
        employee.get("workDayCount", 0) or 0
    )

    schedule_days = int(
        employee.get("scheduleDayCount", 0) or 0
    )

    active_percent = activity_percent(
        active_time,
        total_time
    )

    activity_level = get_activity_level(
        active_percent
    )

    report_data.append({

        "name": name,

        "total_time": total_time,

        "active_time": active_time,

        "down_time": down_time,

        "late_count": late_count,

        "early_end_count": early_end_count,

        "absenteeism": absenteeism,

        "work_days": work_days,

        "schedule_days": schedule_days,

        "active_percent": active_percent,

        "activity_level": activity_level,

        "ai_analysis": ""

    })


# ============================================================
# СОРТИРОВКА
# ============================================================

report_data.sort(
    key=lambda x: x["active_percent"]
)


# ============================================================
# AI-АНАЛИЗ СОТРУДНИКОВ
# ============================================================

print()
print("=" * 60)
print("🤖 AI-АНАЛИЗ СОТРУДНИКОВ")
print("=" * 60)


for index, employee in enumerate(
    report_data,
    start=1
):

    print()
    print(
        f"[{index}/{len(report_data)}] "
        f"Анализируем: {employee['name']}"
    )

    employee["ai_analysis"] = analyze_employee(
        employee
    )

    print("✅ Готово")


# ============================================================
# ОБЩАЯ СТАТИСТИКА
# ============================================================

total_employees = len(
    report_data
)


total_work_time = sum(
    employee["total_time"]
    for employee in report_data
)


total_active_time = sum(
    employee["active_time"]
    for employee in report_data
)


total_down_time = sum(
    employee["down_time"]
    for employee in report_data
)


total_late = sum(
    employee["late_count"]
    for employee in report_data
)


total_early = sum(
    employee["early_end_count"]
    for employee in report_data
)


overall_percent = activity_percent(
    total_active_time,
    total_work_time
)


# ============================================================
# ОБЩИЙ AI-АНАЛИЗ
# ============================================================

print()
print("=" * 60)
print("🤖 AI-АНАЛИЗ КОМАНДЫ")
print("=" * 60)

team_analysis = analyze_team(
    report_data,
    overall_percent
)

print("✅ Общий анализ готов")


# ============================================================
# HTML — ТАБЛИЦА
# ============================================================

table_rows = ""


for employee in report_data:

    percent = employee["active_percent"]

    if percent >= 70:
        percent_class = "good"

    elif percent >= 40:
        percent_class = "medium"

    elif percent >= 20:
        percent_class = "low"

    else:
        percent_class = "critical"


    table_rows += f"""
        <tr>

            <td>
                <strong>
                    {escape(employee["name"])}
                </strong>
            </td>

            <td>
                {seconds_to_time(employee["total_time"])}
            </td>

            <td>
                {seconds_to_time(employee["active_time"])}
            </td>

            <td>
                {seconds_to_time(employee["down_time"])}
            </td>

            <td>
                <span class="percent {percent_class}">
                    {percent}%
                </span>
            </td>

            <td>
                {employee["late_count"]}
            </td>

            <td>
                {employee["early_end_count"]}
            </td>

            <td>
                {employee["work_days"]}
                /
                {employee["schedule_days"]}
            </td>

        </tr>
    """


# ============================================================
# КАРТОЧКИ СОТРУДНИКОВ
# ============================================================

cards_html = ""


for employee in report_data:

    percent = employee["active_percent"]

    if percent >= 70:
        card_class = "good"

    elif percent >= 40:
        card_class = "medium"

    elif percent >= 20:
        card_class = "low"

    else:
        card_class = "critical"


    cards_html += f"""
        <div class="employee-card {card_class}">

            <div class="employee-name">
                {escape(employee["name"])}
            </div>

            <div class="employee-percent">
                {percent}%
            </div>

            <div class="employee-status">
                {employee["activity_level"]} активность
            </div>

            <div class="employee-info">

                <div>
                    <span>Рабочее время</span>
                    <strong>
                        {seconds_to_time(
                            employee["total_time"]
                        )}
                    </strong>
                </div>

                <div>
                    <span>Активность</span>
                    <strong>
                        {seconds_to_time(
                            employee["active_time"]
                        )}
                    </strong>
                </div>

                <div>
                    <span>Простой</span>
                    <strong>
                        {seconds_to_time(
                            employee["down_time"]
                        )}
                    </strong>
                </div>

                <div>
                    <span>Опоздания</span>
                    <strong>
                        {employee["late_count"]}
                    </strong>
                </div>

                <div>
                    <span>Ранние уходы</span>
                    <strong>
                        {employee["early_end_count"]}
                    </strong>
                </div>

            </div>

        </div>
    """


# ============================================================
# AI-КАРТОЧКИ
# ============================================================

ai_cards_html = ""


for employee in report_data:

    ai_cards_html += f"""
        <div class="ai-card">

            <div class="ai-card-header">

                <span class="ai-icon">
                    🤖
                </span>

                <strong>
                    {escape(employee["name"])}
                </strong>

            </div>

            <div class="ai-content">

                {html_ai_text(
                    employee["ai_analysis"]
                )}

            </div>

        </div>
    """


# ============================================================
# HTML
# ============================================================

html = f"""
<!DOCTYPE html>

<html lang="ru">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>
Bitcop — недельный AI-отчёт
</title>


<style>

* {{
    box-sizing: border-box;
}}


body {{

    margin: 0;

    font-family:
        Arial,
        Helvetica,
        sans-serif;

    background: #f4f6f8;

    color: #222;
}}


.container {{

    max-width: 1400px;

    margin: 0 auto;

    padding: 30px;
}}


.header {{

    background: white;

    border-radius: 16px;

    padding: 30px;

    margin-bottom: 25px;

    box-shadow:
        0 3px 15px rgba(0,0,0,0.06);
}}


.header h1 {{

    margin: 0 0 10px 0;

    font-size: 30px;
}}


.header p {{

    margin: 5px 0;

    color: #666;

    font-size: 16px;
}}


.stats {{

    display: grid;

    grid-template-columns:
        repeat(5, 1fr);

    gap: 15px;

    margin-bottom: 25px;
}}


.stat {{

    background: white;

    border-radius: 14px;

    padding: 22px;

    box-shadow:
        0 3px 15px rgba(0,0,0,0.05);
}}


.stat-title {{

    color: #777;

    font-size: 14px;

    margin-bottom: 8px;
}}


.stat-value {{

    font-size: 25px;

    font-weight: bold;
}}


.section {{

    background: white;

    border-radius: 16px;

    padding: 25px;

    margin-bottom: 25px;

    box-shadow:
        0 3px 15px rgba(0,0,0,0.05);
}}


.section h2 {{

    margin-top: 0;

    margin-bottom: 20px;
}}


/* =========================================================
   AI
   ========================================================= */

.ai-section {{

    background:
        linear-gradient(
            135deg,
            #ffffff,
            #f5f7ff
        );
}}


.ai-summary {{

    background: #f8f9ff;

    border-left:
        5px solid #667eea;

    padding: 20px;

    border-radius: 10px;

    line-height: 1.6;

    margin-bottom: 25px;
}}


.ai-grid {{

    display: grid;

    grid-template-columns:
        repeat(auto-fit, minmax(400px, 1fr));

    gap: 18px;
}}


.ai-card {{

    background: #fff;

    border-radius: 14px;

    padding: 20px;

    border:
        1px solid #e5e7eb;

    box-shadow:
        0 3px 12px rgba(0,0,0,0.04);
}}


.ai-card-header {{

    display: flex;

    align-items: center;

    gap: 10px;

    font-size: 17px;

    margin-bottom: 15px;

    padding-bottom: 12px;

    border-bottom:
        1px solid #eee;
}}


.ai-icon {{

    font-size: 22px;
}}


.ai-content {{

    line-height: 1.6;

    color: #444;

    font-size: 14px;
}}


.ai-content p {{

    margin:
        0 0 12px 0;
}}


/* =========================================================
   СОТРУДНИКИ
   ========================================================= */

.employee-grid {{

    display: grid;

    grid-template-columns:
        repeat(auto-fit, minmax(280px, 1fr));

    gap: 18px;
}}


.employee-card {{

    border-radius: 14px;

    padding: 22px;

    background: #f8f9fa;

    border-left:
        6px solid #aaa;
}}


.employee-card.good {{

    border-left-color: #27ae60;
}}


.employee-card.medium {{

    border-left-color: #f39c12;
}}


.employee-card.low {{

    border-left-color: #e67e22;
}}


.employee-card.critical {{

    border-left-color: #e74c3c;
}}


.employee-name {{

    font-size: 19px;

    font-weight: bold;

    margin-bottom: 8px;
}}


.employee-percent {{

    font-size: 34px;

    font-weight: bold;

    margin-bottom: 4px;
}}


.employee-status {{

    color: #777;

    margin-bottom: 18px;
}}


.employee-info {{

    display: grid;

    grid-template-columns:
        1fr 1fr;

    gap: 12px;
}}


.employee-info div {{

    background: white;

    padding: 10px;

    border-radius: 8px;
}}


.employee-info span {{

    display: block;

    color: #777;

    font-size: 12px;

    margin-bottom: 4px;
}}


.employee-info strong {{

    font-size: 15px;
}}


/* =========================================================
   ТАБЛИЦА
   ========================================================= */

table {{

    width: 100%;

    border-collapse: collapse;
}}


th {{

    text-align: left;

    background: #f1f3f5;

    padding: 14px;

    font-size: 13px;

    color: #555;
}}


td {{

    padding: 14px;

    border-bottom:
        1px solid #eee;

    font-size: 14px;
}}


.percent {{

    display: inline-block;

    padding: 5px 10px;

    border-radius: 20px;

    font-weight: bold;
}}


.percent.good {{

    background: #dff5e7;

    color: #218838;
}}


.percent.medium {{

    background: #fff3cd;

    color: #856404;
}}


.percent.low {{

    background: #ffe5cc;

    color: #b35c00;
}}


.percent.critical {{

    background: #f8d7da;

    color: #a71d2a;
}}


.footer {{

    text-align: center;

    color: #888;

    padding: 20px;

    font-size: 13px;
}}


@media (max-width: 1000px) {{

    .stats {{

        grid-template-columns:
            repeat(2, 1fr);
    }}

}}


@media (max-width: 600px) {{

    .container {{

        padding: 15px;
    }}

    .stats {{

        grid-template-columns:
            1fr;
    }}

    .ai-grid {{

        grid-template-columns:
            1fr;
    }}

    table {{

        font-size: 12px;
    }}

    th,
    td {{

        padding: 8px;
    }}

}}

</style>

</head>


<body>


<div class="container">


    <!-- =====================================================
         ЗАГОЛОВОК
         ===================================================== -->

    <div class="header">

        <h1>
            📊 Недельный AI-отчёт Bitcop
        </h1>

        <p>
            Период:
            <strong>
                {last_monday.strftime("%d.%m.%Y")}
                —
                {last_sunday.strftime("%d.%m.%Y")}
            </strong>
        </p>

        <p>
            Сформирован:
            {today.strftime("%d.%m.%Y")}
        </p>

    </div>


    <!-- =====================================================
         ОБЩАЯ СТАТИСТИКА
         ===================================================== -->

    <div class="stats">


        <div class="stat">

            <div class="stat-title">
                Сотрудников
            </div>

            <div class="stat-value">
                {total_employees}
            </div>

        </div>


        <div class="stat">

            <div class="stat-title">
                Рабочее время
            </div>

            <div class="stat-value">
                {seconds_to_time(
                    total_work_time
                )}
            </div>

        </div>


        <div class="stat">

            <div class="stat-title">
                Активное время
            </div>

            <div class="stat-value">
                {seconds_to_time(
                    total_active_time
                )}
            </div>

        </div>


        <div class="stat">

            <div class="stat-title">
                Простой
            </div>

            <div class="stat-value">
                {seconds_to_time(
                    total_down_time
                )}
            </div>

        </div>


        <div class="stat">

            <div class="stat-title">
                Общая активность
            </div>

            <div class="stat-value">
                {overall_percent}%
            </div>

        </div>


    </div>


    <!-- =====================================================
         AI АНАЛИЗ КОМАНДЫ
         ===================================================== -->

    <div class="section ai-section">

        <h2>
            🤖 AI-анализ команды
        </h2>

        <div class="ai-summary">

            {html_ai_text(team_analysis)}

        </div>

    </div>


    <!-- =====================================================
         AI АНАЛИЗ СОТРУДНИКОВ
         ===================================================== -->

    <div class="section ai-section">

        <h2>
            🤖 AI-анализ сотрудников
        </h2>

        <div class="ai-grid">

            {ai_cards_html}

        </div>

    </div>


    <!-- =====================================================
         СОТРУДНИКИ
         ===================================================== -->

    <div class="section">

        <h2>
            👥 Сотрудники
        </h2>

        <div class="employee-grid">

            {cards_html}

        </div>

    </div>


    <!-- =====================================================
         ТАБЛИЦА
         ===================================================== -->

    <div class="section">

        <h2>
            📋 Подробная статистика
        </h2>

        <div style="overflow-x:auto;">

            <table>

                <thead>

                    <tr>

                        <th>
                            Сотрудник
                        </th>

                        <th>
                            Рабочее время
                        </th>

                        <th>
                            Активность
                        </th>

                        <th>
                            Простой
                        </th>

                        <th>
                            % активности
                        </th>

                        <th>
                            Опоздания
                        </th>

                        <th>
                            Ранние уходы
                        </th>

                        <th>
                            Дни
                        </th>

                    </tr>

                </thead>

                <tbody>

                    {table_rows}

                </tbody>

            </table>

        </div>

    </div>


    <!-- =====================================================
         ПОЯСНЕНИЕ
         ===================================================== -->

    <div class="section">

        <h2>
            ℹ️ Что означает активность
        </h2>

        <p>
            Процент активности рассчитывается как отношение
            активного времени к общему рабочему времени.
        </p>

        <p>
            Низкая компьютерная активность сама по себе
            не означает низкую производительность.
            Например, сотрудник может проводить встречи,
            звонки или выполнять задачи вне компьютера.
        </p>

        <p>
            AI-анализ является вспомогательным инструментом
            и не должен использоваться как единственное
            основание для кадровых решений.
        </p>

        <p>
            Данные получены автоматически из Bitcop.
        </p>

    </div>


    <div class="footer">

        Bitcop AI Analytics ·
        Gemini ·
        Автоматический недельный отчёт

    </div>


</div>


</body>

</html>
"""


# ============================================================
# СОХРАНЕНИЕ
# ============================================================

report_file = os.path.abspath(
    "report.html"
)


with open(
    report_file,
    "w",
    encoding="utf-8"
) as file:

    file.write(html)


# ============================================================
# ГОТОВО
# ============================================================

print()
print("=" * 60)
print("✅ AI-ОТЧЁТ СОЗДАН")
print("=" * 60)

print()

print(
    f"Файл:\n{report_file}"
)

print()

print(
    "Открываем отчёт в браузере..."
)


webbrowser.open(
    "file://" + report_file
)