import os
import pandas as pd
from datetime import datetime
import pytz
from flask import Flask, request, abort
import telebot

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

def fetch_schedule_for_group(group_name, day_index, current_pair):
    try:
        df = pd.read_csv(SHEET_CSV_URL, header=None)
        
        group_search = group_name.strip().lower()
        group_col_idx = -1
        
        # Шукаємо колонку з групою
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
        
        # Нормалізація римських цифр
        df[pair_col] = df[pair_col].astype(str).str.upper().str.replace('І', 'I').str.replace('В', 'V')
        
        mask = (df[day_col].astype(str).str.capitalize().str.contains(current_day_str, na=False)) & \
               (df[pair_col].str.strip() == search_pair)
               
        current_lessons = df[mask]
        
        if current_lessons.empty:
            return f"Наразі для твоєї групи немає пар у розкладі."
            
        # Витягуємо дані знайдених рядків
        types = current_lessons.iloc[:, type_col].tolist()
        subjects = current_lessons.iloc[:, subject_col].tolist()
        audits = current_lessons.iloc[:, audit_col].tolist()
        
        num_subjects, num_audits = [], []
        den_subjects, den_audits = [], []
        current_mode = 'num'
        
        # БЕЗПЕЧНИЙ ЦИКЛ: гарантовано конвертуємо все в текст перед strip()
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

        # Функція для красивого оформлення одного блоку
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

        response = f"📅 <b>{current_day_str}</b> | ⏰ <b>Пара {search_pair}</b>\n\n"
        
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
    bot.reply_to(message, "Привіт! Напиши мені свою групу, і я скажу, де у тебе зараз пара (покажу і чисельник, і знаменник). 🎓")

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    try:
        print(f"\n[LOG] Отримано запит для групи: {message.text}")
        group_name = message.text.strip()
        
        tz = pytz.timezone('Europe/Kyiv')
        now = datetime.now(tz)
        day_of_week = now.weekday()
        
        if day_of_week > 4: 
            bot.reply_to(message, "Сьогодні вихідний! Пар немає. 🎮")
            return
            
        current_pair = get_current_pair(day_of_week)
        print(f"[LOG] Визначено поточну пару: {current_pair}")
        
        if not current_pair:
            bot.reply_to(message, "⏳ Зараз навчання ще не почалося, або вже завершилось. Відпочивай!")
            return
            
        # 1. Отримуємо дані для поточної пари
        current_answer = fetch_schedule_for_group(group_name, day_of_week, current_pair)
        
        # Якщо група написана з помилкою, одразу повертаємо текст помилки
        if "❌" in current_answer or "помилка" in current_answer.lower():
            bot.reply_to(message, current_answer, parse_mode="HTML")
            return

        final_message = f"🎯 <b>ЗАРАЗ:</b>\n{current_answer}"
        
        # 2. Шукаємо наступну пару (максимум пар за розкладом - 5)
        next_pair = current_pair + 1
        if next_pair <= 5: 
            next_answer = fetch_schedule_for_group(group_name, day_of_week, next_pair)
            
            # Перевіряємо, чи є наступна пара в розкладі
            if "Наразі для твоєї групи немає пар" in next_answer:
                final_message += "\n\n──────────────────\n🚀 <b>НАСТУПНА ПАРА:</b>\n🎉 Далі пар немає (або вікно)!"
            else:
                final_message += f"\n\n──────────────────\n🚀 <b>НАСТУПНА ПАРА:</b>\n{next_answer}"
        else:
            final_message += "\n\n──────────────────\n🚀 <b>НАСТУПНА ПАРА:</b>\n🎉 Це остання пара на сьогодні!"
            
        bot.reply_to(message, final_message, parse_mode="HTML")
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