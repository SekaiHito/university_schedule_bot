import os
import pandas as pd
from datetime import datetime
import pytz
from flask import Flask, request, abort
import telebot
import time

_cached_df = None
_cache_time = 0
# --- Налаштування ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "ТВІЙ_ТОКЕН_БОТА")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://твій-домен.com") 

# Посилання на CSV-формат (дуже важливо використовувати саме output=csv)
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRXoaJQBhXhCpW5p3nvhnJ0hs9718BH2rWbty0D0sNaE9iGg8PMnPamZOA0oI4yxf5jGpptV-FSSyeA/pub?output=csv"

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
app = Flask(__name__)

# Розклад для понеділка
MON_SCHEDULE = [
    {"pair": 1, "start": "10:00", "end": "11:20"},
    {"pair": 2, "start": "11:35", "end": "12:55"},
    {"pair": 3, "start": "13:35", "end": "14:55"},
    {"pair": 4, "start": "15:10", "end": "16:30"},
    {"pair": 5, "start": "16:45", "end": "18:05"},
]

# Розклад для вівторка, середи, четверга та п'ятниці
# (Перевір, чи правильний тут час, я підставив стандартні проміжки після 09:00)
OTHER_DAYS_SCHEDULE = [
    {"pair": 1, "start": "09:00", "end": "10:20"},
    {"pair": 2, "start": "10:35", "end": "11:55"},
    {"pair": 3, "start": "12:15", "end": "13:35"},
    {"pair": 4, "start": "13:50", "end": "15:10"},
    {"pair": 5, "start": "15:25", "end": "16:45"},
]

def get_current_pair(day_of_week):
    """
    Перевіряє, яка зараз пара, враховуючи день тижня.
    Приймає day_of_week (0 - Понеділок, 1 - Вівторок і т.д.).
    """
    tz = pytz.timezone('Europe/Kyiv')
    now = datetime.now(tz)
    
    # Вибираємо правильний розклад залежно від дня тижня
    if day_of_week == 0:  # Понеділок
        schedule = MON_SCHEDULE
    else:                 # Всі інші дні
        schedule = OTHER_DAYS_SCHEDULE

    current_minutes = now.hour * 60 + now.minute
    
    for i, p in enumerate(schedule):
        start_h, start_m = map(int, p["start"].split(":"))
        start_minutes = start_h * 60 + start_m
        
        end_h, end_m = map(int, p["end"].split(":"))
        end_minutes = end_h * 60 + end_m
        
        # Перерва до пари також вважається "поточною парою"
        if i == 0:
            window_start = start_minutes - 30 
        else:
            prev_end_h, prev_end_m = map(int, schedule[i-1]["end"].split(":"))
            window_start = prev_end_h * 60 + prev_end_m
            
        if window_start <= current_minutes <= end_minutes:
            return p["pair"]
            
    return None

def get_schedule_df():
    global _cached_df, _cache_time
    # Оновлюємо таблицю раз на 10 хвилин (600 секунд)
    if _cached_df is None or (time.time() - _cache_time) > 600:
        _cached_df = pd.read_csv(SHEET_CSV_URL, header=None)
        _cache_time = time.time()
    return _cached_df.copy()

def fetch_schedule_for_group(group_name, day_index, current_pair):
    try:
        df = get_schedule_df()
        
        group_search = group_name.strip().lower()
        group_col_idx = -1
        
        for row_idx in range(10):
            for col_idx in range(len(df.columns)):
                cell_val = str(df.iloc[row_idx, col_idx]).strip().lower()
                if cell_val == group_search:
                    group_col_idx = col_idx
                    break
            if group_col_idx != -1:
                break
                
        if group_col_idx == -1:
            return f"❌ Групу '{group_name}' не знайдено."

        day_col = 1          
        pair_col = 2         
        type_col = 4         
        subject_col = group_col_idx      
        audit_col = group_col_idx + 1    
        
        df[day_col] = df[day_col].ffill()
        df[pair_col] = df[pair_col].ffill()
        
        days_map = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Нд"}
        pairs_map = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}
        
        current_day_str = days_map.get(day_index)
        search_pair = pairs_map.get(current_pair)
        
        # Визначаємо час початку і кінця пари
        time_str = ""
        sched = MON_SCHEDULE if day_index == 0 else OTHER_DAYS_SCHEDULE
        for p in sched:
            if p["pair"] == current_pair:
                time_str = f"{p['start']} - {p['end']}"
                break
        
        df[pair_col] = df[pair_col].astype(str).str.upper().str.replace('І', 'I').str.replace('В', 'V')
        
        mask = (df[day_col].astype(str).str.capitalize().str.contains(current_day_str, na=False)) & \
               (df[pair_col].str.strip() == search_pair)
               
        current_lessons = df[mask]
        
        if current_lessons.empty:
            return f"Наразі для твоєї групи немає пар у розкладі."
            
        types = current_lessons.iloc[:, type_col].tolist()
        subjects = current_lessons.iloc[:, subject_col].tolist()
        audits = current_lessons.iloc[:, audit_col].tolist()
        
        num_subjects, num_audits = [], []
        den_subjects, den_audits = [], []
        current_mode = 'num'
        
        for t, s, a in zip(types, subjects, audits):
            t_str = str(t).lower()
            s_str = str(s).strip()
            a_str = str(a).strip()
            
            if 'знам' in t_str:
                current_mode = 'den'
            elif 'чис' in t_str:
                current_mode = 'num'
                
            s_clean = s_str if s_str != 'nan' else ''
            a_clean = a_str if a_str != 'nan' else ''
            
            if current_mode == 'num':
                num_subjects.append(s_clean)
                num_audits.append(a_clean)
            else:
                den_subjects.append(s_clean)
                den_audits.append(a_clean)

        def format_half_pair(sub_list, aud_list):
            title = sub_list[0] if len(sub_list) > 0 else ""
            sg1_subj = sub_list[1] if len(sub_list) > 1 else ""
            sg1_aud = aud_list[1] if len(aud_list) > 1 else ""
            sg2_subj = sub_list[2] if len(sub_list) > 2 else ""
            sg2_aud = aud_list[2] if len(aud_list) > 2 else ""

            if not title and not sg1_subj and not sg2_subj:
                return "Вільна пара / Вікно"

            res = ""
            if title:
                res += f"📚 <b>{title}</b>\n"
            
            if sg1_subj and not sg2_subj:
                res += f"👨‍🏫 {sg1_subj}\n"
                if sg1_aud: res += f"🚪 Аудиторія: {sg1_aud}\n"
            elif sg1_subj and sg2_subj:
                res += f"👥 <b>1 підгрупа:</b> {sg1_subj} " + (f"(ауд. {sg1_aud})" if sg1_aud else "") + "\n"
                res += f"👥 <b>2 підгрупа:</b> {sg2_subj} " + (f"(ауд. {sg2_aud})" if sg2_aud else "") + "\n"
                
            return res.strip()

        num_text = format_half_pair(num_subjects, num_audits)
        den_text = format_half_pair(den_subjects, den_audits)

        # Додаємо час до заголовка
        response = f"📅 <b>{current_day_str}</b> | ⏰ <b>Пара {search_pair}</b> ({time_str})\n\n"
        
        if num_text == den_text:
            if num_text == "Вільна пара / Вікно":
                response += "🎉 Зараз вікно (вільна пара)!"
            else:
                response += f"📘 <b>На обидва тижні (спільна):</b>\n{num_text}"
        else:
            response += f"🔹 <b>По чисельнику:</b>\n{num_text}\n\n"
            response += f"🔸 <b>По знаменнику:</b>\n{den_text}"
            
        return response

    except Exception as e:
        print(f"Помилка парсингу: {e}")
        return "Виникла помилка під час читання таблиці."

# --- Обробники повідомлень Telegram ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    try:
        bot.reply_to(message, "Привіт! Напиши мені свою групу, і я скажу, де у тебе зараз пара (покажу і чисельник, і знаменник). 🎓")
    except Exception as e:
        # Якщо користувач заблокував бота, просто ігноруємо і виводимо лог
        print(f"[ERROR] Не вдалося надіслати welcome-повідомлення: {e}")
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    try:
        print(f"\n[LOG] Отримано запит для групи: {message.text}")
        group_name = message.text.strip()
        
        tz = pytz.timezone('Europe/Kyiv')
        now = datetime.now(tz)
        day_of_week = now.weekday()
        current_minutes = now.hour * 60 + now.minute
        
        target_day = day_of_week
        start_pair = 1
        context_msg = ""
        is_mid_day = False
        
        # Перевірка на вихідні
        if day_of_week > 4: 
            target_day = 0 
            context_msg = "🏖️ <b>Сьогодні вихідний!</b> Ось розклад на Понеділок:\n\n"
        else:
            schedule = MON_SCHEDULE if day_of_week == 0 else OTHER_DAYS_SCHEDULE
            # Межі навчального дня
            first_start_h, first_start_m = map(int, schedule[0]["start"].split(":"))
            first_start_minutes = first_start_h * 60 + first_start_m - 30 
            
            last_end_h, last_end_m = map(int, schedule[-1]["end"].split(":"))
            last_end_minutes = last_end_h * 60 + last_end_m
            
            # Аналізуємо, в якій частині дня ми знаходимося
            if current_minutes < first_start_minutes:
                context_msg = "🌅 <b>Навчання ще не почалося!</b> Ось твій розклад на сьогодні:\n\n"
            elif current_minutes > last_end_minutes:
                target_day = (day_of_week + 1) if day_of_week < 4 else 0
                days_names = {0: "Понеділок", 1: "Вівторок", 2: "Середу", 3: "Четвер", 4: "П'ятницю"}
                context_msg = f"🌙 <b>Пари вже закінчились!</b> Ось розклад на {days_names[target_day]}:\n\n"
            else:
                is_mid_day = True
                curr_p = get_current_pair(day_of_week)
                start_pair = curr_p if curr_p else 1
        
        # Збираємо всі пари масивом від поточної до кінця дня
        pairs_responses = []
        for p_num in range(start_pair, 6):
            ans = fetch_schedule_for_group(group_name, target_day, p_num)
            if "❌" in ans or "помилка" in ans.lower():
                bot.reply_to(message, ans, parse_mode="HTML")
                return
            pairs_responses.append((p_num, ans))
            
        # Обрізаємо пусті пари в кінці дня (щоб бот не спамив "Пара 4: немає, Пара 5: немає")
        while pairs_responses and "немає пар" in pairs_responses[-1][1]:
            pairs_responses.pop()
            
        if not pairs_responses:
            bot.reply_to(message, context_msg + "🎉 У цей день пар немає (або вільно)!", parse_mode="HTML")
            return

        final_message = context_msg
        
        # Форматуємо видачу
        for i, (p_num, ans) in enumerate(pairs_responses):
            if is_mid_day and i == 0:
                final_message += f"🎯 <b>ПОТОЧНА ПАРА:</b>\n{ans}\n"
                if len(pairs_responses) > 1:
                    final_message += "\n──────────────────\n🚀 <b>НАСТУПНІ ПАРИ:</b>\n\n"
            else:
                final_message += f"{ans}\n\n"
            
        bot.reply_to(message, final_message.strip(), parse_mode="HTML")
        print("[LOG] Відповідь успішно надіслано!")
        
    except Exception as e:
        print(f"[ERROR] Сталася помилка: {e}")
        bot.reply_to(message, f"Упс, сталася помилка: {e}")

# --- Маршрути Flask ---
@app.route(f'/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/set_webhook')
def set_webhook():
    bot.remove_webhook()
    success = bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")
    if success:
        return "Webhook успішно встановлено!", 200
    else:
        return "Помилка встановлення Webhook.", 400

@app.route('/')
def index():
    return "Сервер бота працює!", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)