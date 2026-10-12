import os
import requests
from dotenv import load_dotenv
from datetime import date, timedelta

load_dotenv()

BASE_URL = os.getenv("BITCOP_BASE_URL")
API_KEY = os.getenv("BITCOP_API_KEY")


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


# Прошлая неделя: понедельник - воскресенье
today = date.today()

this_monday = today - timedelta(days=today.weekday())
last_monday = this_monday - timedelta(days=7)
last_sunday = this_monday - timedelta(days=1)

# Формат Bitcop:
# 20260928T000000
# 20261005T000000
begin = last_monday.strftime("%Y%m%dT000000")
end = this_monday.strftime("%Y%m%dT000000")

print("Период:")
print(f"{last_monday} — {last_sunday}")
print(f"begin = {begin}")
print(f"end   = {end}")
print()


try:
    data = bitcop_request(
        "activity",
        {
            "begin": begin,
            "end": end
        }
    )

    print("Ответ Bitcop:")
    print(data)

    if data.get("status") == "success":
        print("\n✅ Статистика успешно получена!")

        print("\nСотрудники:")

        for employee in data.get("items", []):
            print(
                f"\n{employee['name']}"
                f"\n  Общее время: {employee['totalTime']} сек."
                f"\n  Активное время: {employee['activeTime']} сек."
                f"\n  Простой: {employee['downTime']} сек."
                f"\n  Опоздания: {employee['lateCount']}"
                f"\n  Ранние уходы: {employee['earlyEndCount']}"
                f"\n  Переработка: {employee['overTime']} сек."
                f"\n  Прогулы: {employee['absenteism']}"
                f"\n  Отработано дней: {employee['workDayCount']}"
                f"\n  Рабочих дней по графику: {employee['scheduleDayCount']}"
            )

    else:
        print("\n❌ Bitcop вернул ошибку:")
        print(data)

except requests.exceptions.HTTPError as e:
    print("\n❌ Ошибка HTTP:")
    print(e)

    if e.response is not None:
        print(e.response.text)

except Exception as e:
    print("\n❌ Ошибка:")
    print(e)