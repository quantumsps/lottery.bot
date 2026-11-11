import random
import json
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import BadRequest
from upstash_redis import Redis

# ================= تنظیمات اصلی =================
TOKEN = "8676784310:AAF4JTKeH_VKjbd58sUJ1EdeKixWQ-f5OIU"
ADMIN_ID = 8898908182
BOT_USERNAME = "quantumghorebot"

REDIS_URL = "https://relieved-fawn-197670.upstash.io"
REDIS_TOKEN = "gQAAAAAAAwQmAAIgcDE2MmFlM2JjYTBmY2M0MzMyYjRiNzRlMTEyOWYyOGY4OA"

WINNER_STICKER_ID = "CAACAgIAAxkBAAE... (آیدی استیکر جایزه - اختیاری)"

CHANNELS = [
    {"id": "@quantumvaulte", "link": "https://t.me/quantumvaulte"}
]
# ================================================

redis = Redis(url=REDIS_URL, token=REDIS_TOKEN)
app = FastAPI()
ptb_app = None

all_users = set() 
lottery = {
    "status": "INACTIVE", 
    "topic": "تعیین نشده",
    "winners_count": 1,
    "prizes": [],
    "end_dt": None,
    "start_dt": None
}
participants = {} 
admin_states = {}
admin_temp_data = {}
user_states = {}

# -------- توابع دیتابیس ابری (Upstash Redis) --------
def save_data():
    try:
        data = {
            "all_users": list(all_users),
            "lottery": {
                "status": lottery["status"],
                "topic": lottery["topic"],
                "winners_count": lottery["winners_count"],
                "prizes": lottery["prizes"],
                "start_dt": lottery["start_dt"].isoformat() if lottery["start_dt"] else None,
                "end_dt": lottery["end_dt"].isoformat() if lottery["end_dt"] else None
            },
            "participants": {str(k): v for k, v in participants.items()},
            "admin_states": {str(k): v for k, v in admin_states.items()},
            "admin_temp_data": admin_temp_data,
            "user_states": {str(k): v for k, v in user_states.items()}
        }
        redis.set("lottery_db", json.dumps(data))
    except Exception as e:
        print("Error saving data:", e)

def load_data():
    global all_users, lottery, participants, admin_states, admin_temp_data, user_states
    try:
        raw = redis.get("lottery_db")
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            all_users = set(data.get("all_users", []))
            
            l_data = data.get("lottery", {})
            lottery["status"] = l_data.get("status", "INACTIVE")
            lottery["topic"] = l_data.get("topic", "تعیین نشده")
            lottery["winners_count"] = l_data.get("winners_count", 1)
            lottery["prizes"] = l_data.get("prizes", [])
            
            s_dt = l_data.get("start_dt")
            e_dt = l_data.get("end_dt")
            lottery["start_dt"] = datetime.fromisoformat(s_dt) if s_dt else None
            lottery["end_dt"] = datetime.fromisoformat(e_dt) if e_dt else None
            
            participants = {int(k): v for k, v in data.get("participants", {}).items()}
            admin_states = {int(k): v for k, v in data.get("admin_states", {}).items()}
            admin_temp_data = data.get("admin_temp_data", {})
            user_states = {int(k): v for k, v in data.get("user_states", {}).items()}
    except Exception as e:
        print("Error loading data:", e)
# ----------------------------------------------------

def get_admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 ایجاد قرعه‌کشی جدید", callback_data="admin_new"),
         InlineKeyboardButton("⏳ ویرایش زمان", callback_data="admin_edit_time")],
        [InlineKeyboardButton("📢 پیام همگانی", callback_data="admin_broadcast"),
         InlineKeyboardButton("👥 لیست شرکت‌کنندگان", callback_data="admin_list")],
        [InlineKeyboardButton("🎯 پایان و قرعه‌کشی فوری", callback_data="admin_draw_now")],
        [InlineKeyboardButton("📊 وضعیت", callback_data="admin_status"),
         InlineKeyboardButton("📝 راهنمای جامع ربات", callback_data="admin_help")]
    ])

def get_user_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 ثبت نام در قرعه‌کشی", callback_data="user_register")],
        [InlineKeyboardButton("🔗 دریافت لینک دعوت من (شانس بیشتر)", callback_data="user_invite")],
        [InlineKeyboardButton("👤 وضعیت و بلیط من", callback_data="user_status")]
    ])

def get_join_menu():
    buttons = []
    for i, ch in enumerate(CHANNELS):
        buttons.append([InlineKeyboardButton(f"📣 عضویت در کانال اسپانسر {i+1}", url=ch["link"])])
    buttons.append([InlineKeyboardButton("✅ عضو شدم، ثبت‌نام کن", callback_data="user_register")])
    return InlineKeyboardMarkup(buttons)

def get_time_left(target_date):
    if not target_date: return "نامشخص"
    now = datetime.now()
    if now >= target_date: return "پایان یافته"
    diff = target_date - now
    days = diff.days
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days} روز و {hours} ساعت و {minutes} دقیقه"

def get_weighted_winners(weights_dict, count):
    winners = []
    pool = list(weights_dict.keys())
    chances = [weights_dict[uid] for uid in pool]
    while len(winners) < count and pool:
        chosen = random.choices(pool, weights=chances, k=1)[0]
        winners.append(chosen)
        idx = pool.index(chosen)
        pool.pop(idx)
        chances.pop(idx)
    return winners

# بررسی خودکار زمان شروع و پایان قرعه‌کشی در فضای ابری
async def check_auto_schedule(bot: Bot):
    global lottery
    now = datetime.now()
    if lottery["status"] == "PENDING" and lottery["start_dt"] and now >= lottery["start_dt"]:
        lottery["status"] = "ACTIVE"
        save_data()
        try: await bot.send_message(chat_id=ADMIN_ID, text="🟢 زمان شروع فرا رسید! ثبت‌نام باز شد.")
        except: pass
        
    if lottery["status"] == "ACTIVE" and lottery["end_dt"] and now >= lottery["end_dt"]:
        await execute_draw(bot)

async def execute_draw(bot: Bot):
    global lottery
    lottery["status"] = "INACTIVE"
    lottery["end_dt"] = None
    save_data()

    if not participants:
        await bot.send_message(chat_id=ADMIN_ID, text="🔴 زمان قرعه‌کشی تمام شد اما هیچکس ثبت‌نام نکرده بود!", reply_markup=get_admin_menu())
        return

    await bot.send_message(chat_id=ADMIN_ID, text="⏳ در حال بررسی تقلب‌ها و پردازش کاربران...")

    final_weights = {}
    for uid, info in participants.items():
        valid_chances = 2 if info['is_premium'] else 1 
        for ref_uid in info['referrals']:
            if ref_uid in participants:
                is_valid = True
                for ch in CHANNELS:
                    try:
                        member = await bot.get_chat_member(chat_id=ch["id"], user_id=ref_uid)
                        if member.status not in ['member', 'creator', 'administrator']:
                            is_valid = False; break
                    except: is_valid = False; break
                if is_valid: valid_chances += 1
        final_weights[uid] = valid_chances

    win_count = min(lottery["winners_count"], len(final_weights))
    winners_ids = get_weighted_winners(final_weights, win_count)

    admin_text = f"🎊 <b>قرعه‌کشی پایان یافت!</b>\n\n👥 کل شرکت‌کنندگان: {len(participants)} نفر\n🏆 <b>لیست برندگان:</b>\n\n"
    
    for i, wid in enumerate(winners_ids):
        w_info = participants[wid]
        un = f"@{w_info['username']}" if w_info['username'] != "ندارد" else "بدون آیدی"
        safe_name = w_info['name'].replace('<', '').replace('>', '')
        admin_text += f"👤 {safe_name} | بلیط: {w_info['ticket']} | {un} | شانس: {final_weights[wid]} | <code>{wid}</code>\n"
        
        try:
            if WINNER_STICKER_ID.startswith("CAAC"):
                await bot.send_sticker(chat_id=wid, sticker=WINNER_STICKER_ID)
        except Exception: pass

        if lottery["prizes"] and i < len(lottery["prizes"]):
            winner_msg = f"<tg-emoji emoji-id=\"4956596167451346576\">🎉</tg-emoji> <b>تبریک!</b>\nشماره بلیط <code>{w_info['ticket']}</code> برنده شد! <tg-emoji emoji-id=\"5280615440928758599\">🎁</tg-emoji>\n\nجایزه اختصاصی شما:\n<code>{lottery['prizes'][i]}</code>"
        else:
            winner_msg = f"<tg-emoji emoji-id=\"4956596167451346576\">🎉</tg-emoji> <b>تبریک!</b>\nشماره بلیط <code>{w_info['ticket']}</code> برنده شد! <tg-emoji emoji-id=\"5280615440928758599\">🎁</tg-emoji>"

        try: await bot.send_message(chat_id=wid, text=winner_msg, parse_mode='HTML')
        except: pass

    if lottery["prizes"]: admin_text += "\n✅ جوایز ارسال شد!"
    await bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode='HTML', reply_markup=get_admin_menu())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    await check_auto_schedule(context.bot)
    user = update.effective_user
    if user.id not in all_users:
        all_users.add(user.id)
    
    if user.id not in user_states:
        user_states[user.id] = {}

    args = context.args
    if args and args[0].startswith("ref_") and user.id != ADMIN_ID:
        try:
            ref_id = int(args[0].split("_")[1])
            if ref_id != user.id: 
                user_states[user.id]['referrer'] = ref_id
        except ValueError: pass

    if user.id == ADMIN_ID:
        admin_states[user.id] = "IDLE"
        save_data()
        msg = f"<tg-emoji emoji-id=\"5819154994967874788\">🧑‍💻</tg-emoji> <b>پنل مدیریت پیشرفته:</b>"
        await update.message.reply_text(msg, reply_markup=get_admin_menu(), parse_mode='HTML')
    else:
        save_data()
        topic_text = lottery["topic"] if lottery["status"] != "INACTIVE" else "در حال حاضر قرعه‌کشی نداریم"
        msg = (f"<tg-emoji emoji-id=\"6320811474402943057\">✨</tg-emoji> به ربات قرعه کشی <b>quantum</b> خوش اومدید.\n"
               f"موضوع قرعه کشی: {topic_text}\n\n"
               f"<tg-emoji emoji-id=\"5470177992950946662\">👇</tg-emoji> برای ثبت نام گزینه زیر را بزنید.")
        
        await update.message.reply_text(msg, reply_markup=get_user_menu(), parse_mode='HTML')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    await check_auto_schedule(context.bot)
    query = update.callback_query
    user = query.from_user
    data = query.data
    global lottery, participants, all_users

    if user.id not in user_states:
        user_states[user.id] = {}

    await query.answer()

    if data == "user_register":
        if lottery["status"] == "INACTIVE":
            await query.edit_message_text("❌ قرعه‌کشی فعالی وجود ندارد.", reply_markup=get_user_menu())
            return
        elif lottery["status"] == "PENDING":
            await query.edit_message_text(f"⏳ در انتظار شروع...\nزمان تا شروع: {get_time_left(lottery['start_dt'])}", reply_markup=get_user_menu())
            return
        elif user.id in participants:
            await query.edit_message_text("⚠️ شما قبلا ثبت‌نام کرده‌اید! برای دیدن بلیط خود به بخش «وضعیت» بروید.", reply_markup=get_user_menu())
            return

        all_joined = True
        for ch in CHANNELS:
            try:
                member = await context.bot.get_chat_member(chat_id=ch["id"], user_id=user.id)
                if member.status not in ['member', 'creator', 'administrator']:
                    all_joined = False
                    break
            except BadRequest: pass

        if not all_joined:
            await query.edit_message_text("⚠️ برای شرکت در قرعه‌کشی باید در <b>تمامی کانال‌های زیر</b> عضو شوید:", parse_mode='HTML', reply_markup=get_join_menu())
            return

        num1, num2 = random.randint(1, 10), random.randint(1, 10)
        user_states[user.id]['captcha'] = num1 + num2
        save_data()
        options = [num1 + num2, num1 + num2 + random.randint(1, 5), num1 + num2 - random.randint(1, 4), num1 + num2 + random.randint(6, 10)]
        random.shuffle(options)
        
        captcha_kb = [[InlineKeyboardButton(str(opt), callback_data=f"cap_{opt}") for opt in options]]
        await query.edit_message_text(f"🤖 لطفا حاصل جمع زیر را انتخاب کنید:\n\n{num1} + {num2} = ?", reply_markup=InlineKeyboardMarkup(captcha_kb))

    elif data.startswith("cap_"):
        try:
            chosen_ans = int(data.split("_")[1])
            correct_ans = user_states.get(user.id, {}).get('captcha')
            
            if chosen_ans == correct_ans:
                is_prem = getattr(user, 'is_premium', False)
                ticket_id = f"#Q-{random.randint(100000, 999999)}"
                
                participants[user.id] = {
                    "name": user.first_name, 
                    "username": user.username or "ندارد", 
                    "referrals": [],
                    "ticket": ticket_id,
                    "is_premium": is_prem
                }
                
                ref_id = user_states.get(user.id, {}).get('referrer')
                if ref_id and ref_id in participants:
                    if user.id not in participants[ref_id]["referrals"]:
                        participants[ref_id]["referrals"].append(user.id)
                        user_states[user.id]['referrer'] = None
                        try: await context.bot.send_message(chat_id=ref_id, text="🎉 یک نفر با لینک شما ثبت‌نام کرد!")
                        except: pass
                
                save_data()
                
                success_msg = f"<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> <b>ثبت‌نام شما با موفقیت انجام شد!</b>\n\n<tg-emoji emoji-id=\"5418010521309815154\">🎫</tg-emoji> شماره بلیط اختصاصی شما: <code>{ticket_id}</code>\n"
                if is_prem:
                    success_msg += "\n💎 <i>کاربر ویژه: به دلیل داشتن اکانت تلگرام پرمیوم، شما با 2 شانس در قرعه‌کشی ثبت‌نام شدید!</i>"
                
                await query.edit_message_text(success_msg, parse_mode='HTML', reply_markup=get_user_menu())
                
                un_txt = f"@{user.username}" if user.username else "ندارد"
                safe_name = user.first_name.replace('<', '').replace('>', '')
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"🔔 ثبت‌نام جدید:\nنام: {safe_name}\nآیدی: {un_txt}\nپرمیوم: {'بله 💎' if is_prem else 'خیر'}")
            else:
                await query.edit_message_text("❌ پاسخ اشتباه بود! لطفا دوباره روی دکمه ثبت نام کلیک کنید.", reply_markup=get_user_menu())
        except Exception: pass

    elif data == "user_invite":
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.id}"
        msg = f"🔗 لینک دعوت اختصاصی شما:\n\n{link}\n\nبا دعوت هر نفر، 2 شانس بیشتر در قرعه‌کشی می‌گیرید!"
        await query.edit_message_text(msg, reply_markup=get_user_menu())

    elif data == "user_status":
        if user.id in participants:
            info = participants[user.id]
            invited = len(info['referrals'])
            time_left = get_time_left(lottery['end_dt']) if lottery['status'] == "ACTIVE" else "قرعه‌کشی در جریان نیست"
            
            msg = (f"✅ وضعیت شما: <b>ثبت‌نام شده</b>\n"
                   f"🎫 شماره بلیط: <code>{info['ticket']}</code>\n"
                   f"💎 اکانت پرمیوم: {'بله (+۲ شانس)' if info['is_premium'] else 'خیر'}\n"
                   f"👥 افراد دعوت شده: {invited} نفر\n"
                   f"🎯 <i>(مجموع شانس پس از بررسی فیک‌ها محاسبه می‌شود)</i>\n\n"
                   f"⏳ زمان تا پایان:\n{time_left}")
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_user_menu())
        else:
            await query.edit_message_text("<tg-emoji emoji-id=\"5852812849780362931\">❌</tg-emoji> شما ثبت‌نام نکرده‌اید.", parse_mode='HTML', reply_markup=get_user_menu())

    elif user.id == ADMIN_ID:
        if data == "admin_new":
            admin_states[user.id] = "WAIT_TOPIC"
            save_data()
            await query.edit_message_text("<tg-emoji emoji-id=\"5334882760735598374\">📝</tg-emoji> موضوع قرعه‌کشی چیست؟", parse_mode='HTML')
            
        elif data == "admin_edit_time":
            if lottery["status"] == "INACTIVE":
                await query.edit_message_text("❌ قرعه‌کشی فعالی وجود ندارد که زمانش را تغییر دهید.", reply_markup=get_admin_menu())
            else:
                admin_states[user.id] = "WAIT_EDIT_START"
                save_data()
                await query.edit_message_text("<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> تاریخ <b>شروع جدید</b> را وارد کنید (مثال: 2026/11/12 18:30):", parse_mode='HTML')

        elif data == "admin_broadcast":
            admin_states[user.id] = "WAIT_BROADCAST"
            save_data()
            await query.edit_message_text("📢 لطفا پیام خود را بفرستید:")
            
        elif data == "admin_draw_now":
            if lottery["status"] == "INACTIVE":
                await query.edit_message_text("❌ هیچ قرعه‌کشی فعالی برای پایان دادن وجود ندارد.", reply_markup=get_admin_menu())
            else:
                await execute_draw(context.bot)

        elif data == "admin_list":
            if not participants:
                await query.edit_message_text("هنوز کسی ثبت‌نام نکرده.", reply_markup=get_admin_menu())
                return
            text = "<tg-emoji emoji-id=\"5318803171978526909\">👥</tg-emoji> <b>لیست شرکت‌کنندگان:</b>\n\n"
            for uid, info in participants.items():
                un = f"@{info['username']}" if info['username'] != "ندارد" else "بدون آیدی"
                safe_name = info['name'].replace('<', '').replace('>', '')
                prem_icon = "💎" if info['is_premium'] else "👤"
                text += f"{prem_icon} {safe_name} | {un} | دعوت: {len(info['referrals'])} | <code>{uid}</code>\n"
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=get_admin_menu())
        
        elif data == "admin_status":
            st_map = {
                "INACTIVE": "<tg-emoji emoji-id=\"5972265777296838427\">🔹</tg-emoji> غیرفعال", 
                "PENDING": "⏳ در انتظار شروع", 
                "ACTIVE": "<tg-emoji emoji-id=\"5852871561983299073\">✅</tg-emoji> در حال ثبت‌نام (فعال)"
            }
            msg = (f"وضعیت: {st_map[lottery['status']]}\n"
                   f"موضوع: {lottery['topic']}\n"
                   f"برندگان: {lottery['winners_count']}\n"
                   f"شرکت‌کنندگان: {len(participants)} نفر")
            await query.edit_message_text(msg, parse_mode='HTML', reply_markup=get_admin_menu())
            
        elif data == "admin_help":
            help_text = (
                "📚 <b>راهنمای جامع سیستم VIP</b>\n\n"
                "<b>۱. سیستم پرمیوم:</b> کاربران پرمیوم با 2 شانس استارت می‌زنند.\n"
                "<b>۲. ضد تقلب:</b> بررسی خروج کاربران هنگام پایان قرعه‌کشی.\n"
                "<b>۳. ایموجی متحرک:</b> ارسال ایموجی پرمیوم به ربات = دریافت کد HTML.\n"
                "<b>۴. پاکسازی کیبورد مزاحم:</b> ارسال دستور <code>/clear</code>\n"
            )
            await query.edit_message_text(help_text, parse_mode='HTML', reply_markup=get_admin_menu())

async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        file_id = update.message.sticker.file_id
        await update.message.reply_text(f"آیدی استیکر:\n\n<code>{file_id}</code>", parse_mode='HTML')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    await check_auto_schedule(context.bot)
    user = update.effective_user
    global lottery, participants, all_users
    if user.id != ADMIN_ID: return
    
    if update.message.entities and any(ent.type == "custom_emoji" for ent in update.message.entities):
        msg = "✨ <b>کد ایموجی‌های پرمیوم شما ساخته شد:</b>\n\n"
        for ent in update.message.entities:
            if ent.type == "custom_emoji":
                emoji_char = update.message.text[ent.offset : ent.offset + ent.length]
                msg += f"ایموجی: {emoji_char}\nکد برای کپی کردن:\n<code>&lt;tg-emoji emoji-id=\"{ent.custom_emoji_id}\"&gt;{emoji_char}&lt;/tg-emoji&gt;</code>\n\n"
        await update.message.reply_text(msg, parse_mode='HTML')
        return

    state = admin_states.get(user.id)
    if state == "WAIT_BROADCAST":
        admin_states[user.id] = "IDLE"
        save_data()
        success = 0
        await update.message.reply_text("⏳ در حال ارسال...")
        for uid in list(all_users):
            try:
                await update.message.copy(chat_id=uid)
                success += 1
            except Exception: pass
        await update.message.reply_text(f"✅ برای {success} نفر ارسال شد!", reply_markup=get_admin_menu())
        return

    text = update.message.text
    if not text: return

    if state == "WAIT_TOPIC":
        admin_temp_data["topic"] = text
        admin_states[user.id] = "WAIT_WINNERS"
        save_data()
        await update.message.reply_text("<tg-emoji emoji-id=\"5318803171978526909\">👥</tg-emoji> تعداد برنده‌ها را وارد کنید:", parse_mode='HTML')
        
    elif state == "WAIT_WINNERS":
        try:
            admin_temp_data["winners"] = int(text)
            admin_states[user.id] = "WAIT_PRIZES"
            save_data()
            await update.message.reply_text("<tg-emoji emoji-id=\"5280615440928758599\">🎁</tg-emoji> جوایز را در خطوط مجزا بفرستید (یا 0 بفرستید):", parse_mode='HTML')
        except ValueError: await update.message.reply_text("❌ فقط عدد بفرستید.")
        
    elif state == "WAIT_PRIZES":
        if text == "0": admin_temp_data["prizes"] = []
        else:
            prizes = text.split('\n')
            if len(prizes) != admin_temp_data["winners"]:
                await update.message.reply_text(f"❌ تعداد جوایز باید {admin_temp_data['winners']} باشد.")
                return
            admin_temp_data["prizes"] = prizes
        admin_states[user.id] = "WAIT_START_DATE"
        save_data()
        await update.message.reply_text("<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> تاریخ <b>شروع</b> (مثال: 2026/11/12 18:30):", parse_mode='HTML')
        
    elif state == "WAIT_START_DATE":
        try:
            datetime.strptime(text, "%Y/%m/%d %H:%M")
            admin_temp_data["start_date"] = text
            admin_states[user.id] = "WAIT_END_DATE"
            save_data()
            await update.message.reply_text("<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> تاریخ <b>پایان</b> (مثال: 2026/11/15 18:30):", parse_mode='HTML')
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است.")
        
    elif state == "WAIT_END_DATE":
        try:
            end_dt = datetime.strptime(text, "%Y/%m/%d %H:%M")
            start_dt = datetime.strptime(admin_temp_data["start_date"], "%Y/%m/%d %H:%M")
            now = datetime.now()
            if end_dt <= start_dt or end_dt <= now:
                await update.message.reply_text("❌ تاریخ پایان نامعتبر است!")
                return

            lottery["topic"] = admin_temp_data["topic"]
            lottery["winners_count"] = admin_temp_data["winners"]
            lottery["prizes"] = admin_temp_data["prizes"]
            lottery["start_dt"] = start_dt
            lottery["end_dt"] = end_dt
            participants.clear()
            admin_states[user.id] = "IDLE"

            if (start_dt - now).total_seconds() <= 0:
                lottery["status"] = "ACTIVE"
                save_data()
                await update.message.reply_text(f"✅ فعال شد!\nپایان: {end_dt}", reply_markup=get_admin_menu())
            else:
                lottery["status"] = "PENDING"
                save_data()
                await update.message.reply_text(f"✅ برنامه‌ریزی شد.\nشروع: {start_dt}\nپایان: {end_dt}", reply_markup=get_admin_menu())
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است.")

    elif state == "WAIT_EDIT_START":
        try:
            datetime.strptime(text, "%Y/%m/%d %H:%M")
            admin_temp_data["edit_start_date"] = text
            admin_states[user.id] = "WAIT_EDIT_END"
            save_data()
            await update.message.reply_text("<tg-emoji emoji-id=\"5319207491609838861\">⏰</tg-emoji> تاریخ <b>پایان جدید</b> را وارد کنید (مثال: 2026/11/15 18:30):", parse_mode='HTML')
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است. لطفا دوباره وارد کنید:")
        
    elif state == "WAIT_EDIT_END":
        try:
            end_dt = datetime.strptime(text, "%Y/%m/%d %H:%M")
            start_dt = datetime.strptime(admin_temp_data["edit_start_date"], "%Y/%m/%d %H:%M")
            now = datetime.now()
            if end_dt <= start_dt or end_dt <= now:
                await update.message.reply_text("❌ تاریخ پایان نامعتبر است! باید بعد از زمان شروع و زمان حال باشد.")
                return
            
            lottery["start_dt"] = start_dt
            lottery["end_dt"] = end_dt
            admin_states[user.id] = "IDLE"
            
            if (start_dt - now).total_seconds() <= 0:
                lottery["status"] = "ACTIVE"
                save_data()
                await update.message.reply_text(f"✅ زمان با موفقیت ویرایش شد!\nوضعیت: در حال ثبت‌نام\nپایان جدید: {end_dt}", reply_markup=get_admin_menu())
            else:
                lottery["status"] = "PENDING"
                save_data()
                await update.message.reply_text(f"✅ زمان ویرایش شد!\nشروع جدید: {start_dt}\nپایان جدید: {end_dt}", reply_markup=get_admin_menu())
        except ValueError: await update.message.reply_text("❌ فرمت اشتباه است.")

async def send_prize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        user_id = int(context.args[0])
        prize_text = " ".join(context.args[1:])
        await context.bot.send_message(chat_id=user_id, text=f"🎁 پیام مدیریت:\n\n{prize_text}")
        await update.message.reply_text("✅ ارسال شد.")
    except: await update.message.reply_text("❌ خطا.")

async def clear_old_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ کیبوردهای قدیمی پاک شدند! حالا روی /start کلیک کنید.", reply_markup=ReplyKeyboardRemove())

async def get_ptb_app():
    global ptb_app
    if ptb_app is None:
        ptb_app = Application.builder().token(TOKEN).build()
        ptb_app.add_handler(CommandHandler("start", start))
        ptb_app.add_handler(CommandHandler("send_prize", send_prize))
        ptb_app.add_handler(CommandHandler("clear", clear_old_keyboard))
        ptb_app.add_handler(CallbackQueryHandler(button_handler))
        ptb_app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
        ptb_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.Sticker.ALL, handle_text))
        await ptb_app.initialize()
    return ptb_app

@app.post("/{full_path:path}")
async def telegram_webhook(full_path: str, request: Request):
    application = await get_ptb_app()
    update_dict = await request.json()
    update = Update.de_json(update_dict, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/{full_path:path}")
async def setup_webhook(full_path: str, request: Request):
    load_data()
    domain = f"https://{request.headers.get('host')}"
    bot = Bot(TOKEN)
    await bot.set_webhook(f"{domain}/")
    await check_auto_schedule(bot)
    return {"status": "Quantum Lottery Bot Connected!", "webhook": f"{domain}/"}
