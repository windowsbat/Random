import logging
import asyncio
import random
import os  # <-- НОВЫЙ ИМПОРТ ДЛЯ РАБОТЫ С ПЕРЕМЕННЫМИ ОКРУЖЕНИЯ
from datetime import datetime

# Импорты aiogram v2
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.exceptions import ChatNotFound 

# Импорт планировщика
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO)

# !!! БЕЗОПАСНОСТЬ: Токен считывается из переменной окружения Railway/ОС !!!
API_TOKEN = os.getenv('TELEGRAM_TOKEN') 

# Проверка, что токен был установлен
if not API_TOKEN:
    logging.error("КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения 'TELEGRAM_TOKEN' не установлена.")
    # Используем sys.exit(1) для завершения, если токен не найден
    import sys
    sys.exit(1)

# !!! ВАЖНО: Замените на фактический @username вашего бота !!!
BOT_USERNAME = "@RandomGiveBBot" 

# Инициализация бота, диспетчера и планировщика
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
scheduler = AsyncIOScheduler()

# --- ХРАНИЛИЩЕ ДАННЫХ ---
# Key: channel_id (ID канала, где проводится розыгрыш)
CONTESTS = {} 


# --- 1. ФУНКЦИИ ПРОВЕРКИ И УПРАВЛЕНИЯ КОНКУРСОМ ---

async def check_subscription(user_id: int, channel_id: str) -> bool:
    """
    Проверяет, подписан ли пользователь на указанный канал.
    Бот должен быть АДМИНОМ в целевом канале!
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        # Если статус 'member', 'administrator', 'creator', значит, подписан
        return member.status in ['member', 'administrator', 'creator']
    except ChatNotFound:
        logging.error(f"Канал {channel_id} не найден.")
        return False
    except Exception as e:
        # Ловим общие ошибки API, включая ошибки прав
        error_message = str(e)
        if 'not an administrator' in error_message or 'not member' in error_message:
            logging.error(f"Бот не является администратором в канале {channel_id} для проверки подписки.")
        else:
            logging.error(f"Неизвестная ошибка API при проверке подписки: {e}")
        return False


def cancel_contest_logic(contest_id: str, channel_id: str, is_active: bool) -> bool:
    """
    Удаляет все запланированные задания и данные о конкурсе.
    Возвращает True, если конкурс был найден и удален.
    """
    if contest_id not in CONTESTS:
        return False
    
    try:
        # 1. Удаляем запланированные задания
        publish_job = scheduler.get_job(f"publish_{contest_id}")
        end_job = scheduler.get_job(f"end_{contest_id}")
        
        if publish_job:
            scheduler.remove_job(f"publish_{contest_id}")
        if end_job:
            scheduler.remove_job(f"end_{contest_id}")
            
        # 2. Удаляем данные из хранилища
        del CONTESTS[contest_id]

        # 3. Отправляем уведомление (если это активный розыгрыш)
        if is_active:
            # Используем asyncio.create_task, чтобы не блокировать основное выполнение
            asyncio.create_task(
                bot.send_message(
                    channel_id, 
                    "🛑 **РОЗЫГРЫШ ОТМЕНЕН АДМИНИСТРАТОРОМ** 🛑\n\nПриносим извинения за неудобства.", 
                    parse_mode=types.ParseMode.MARKDOWN
                )
            )
        
        return True
        
    except Exception as e:
        logging.error(f"Ошибка при отмене конкурса {contest_id}: {e}")
        return False


async def select_winners(contest_id: str):
    """
    Выбирает победителей для завершившегося конкурса.
    """
    contest_data = CONTESTS.get(contest_id)
    if not contest_data:
        logging.error(f"Конкурс {contest_id} не найден для подведения итогов.")
        return

    channel_id = contest_data['channel_id']
    winners_count = contest_data['winners_count']
    participants = contest_data['participants']
    
    valid_participants = []
    
    # 1. Фильтрация: Проверка подписки
    logging.info(f"Начинаем проверку подписок для конкурса {contest_id}...")
    for user_id in list(participants.keys()):
        if await check_subscription(user_id, channel_id):
            valid_participants.append(user_id)
    
    logging.info(f"Найдено действительных участников: {len(valid_participants)}")

    if len(valid_participants) < winners_count:
        await bot.send_message(channel_id, 
                               f"Конкурс завершен! Недостаточно участников ({len(valid_participants)}), чтобы выбрать {winners_count} победителей.")
        del CONTESTS[contest_id]
        return

    # 2. Выбор победителей
    winners_ids = random.sample(valid_participants, k=winners_count)
    
    winners_mentions = []
    for user_id in winners_ids:
        try:
            user_info = await bot.get_chat_member(channel_id, user_id)
            # Форматирование упоминания: [Имя](tg://user?id=ID)
            mention = f"[{user_info.user.full_name}](tg://user?id={user_id})"
            winners_mentions.append(mention)
        except Exception:
            winners_mentions.append(f"Пользователь с ID `{user_id}`")
    
    # 3. Публикация результатов
    results_text = (
        f"🎉 **РОЗЫГРЫШ ЗАВЕРШЕН!** 🎉\n\n"
        f"Поздравляем наших {winners_count} победителей:\n\n"
        f"{' '.join(winners_mentions)}\n\n"
        f"Всего участников, выполнивших условие: {len(valid_participants)}"
    )

    await bot.send_message(channel_id, results_text, parse_mode=types.ParseMode.MARKDOWN)
    
    # Удаляем конкурс из активных
    del CONTESTS[contest_id]
    logging.info(f"Конкурс {contest_id} успешно завершен и удален.")


# --- 2. ФУНКЦИИ ПЛАНИРОВАНИЯ И ПУБЛИКАЦИИ ---

async def publish_contest(contest_id: str):
    """
    Публикует конкурс в указанном канале и добавляет кнопку "Участвую".
    """
    contest_data = CONTESTS.get(contest_id)
    if not contest_data: return

    channel_id = contest_data['channel_id']
    post_text = contest_data['post_text']
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    callback_data = f"participate_{contest_id}"
    keyboard.add(InlineKeyboardButton(text="🎁 Участвую!", callback_data=callback_data))

    try:
        msg = await bot.send_message(
            channel_id, 
            post_text, 
            reply_markup=keyboard,
            parse_mode=types.ParseMode.MARKDOWN
        )
        contest_data['post_message_id'] = msg.message_id
        logging.info(f"Конкурс {contest_id} опубликован в канале {channel_id}")

        # Планируем завершение конкурса
        end_time = contest_data['end_time']
        scheduler.add_job(
            select_winners, 
            'date', 
            run_date=end_time,
            args=[contest_id],
            id=f"end_{contest_id}" 
        )
        
    except Exception as e:
        logging.error(f"Не удалось опубликовать конкурс {contest_id}: {e}")
        # Если публикация не удалась, удаляем данные конкурса
        del CONTESTS[contest_id]
        # Удаляем задачу публикации
        if scheduler.get_job(f"publish_{contest_id}"):
            scheduler.remove_job(f"publish_{contest_id}")


# --- 3. ОБРАБОТЧИКИ КОМАНД ---

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    # Я убрал из этого сообщения длинный пример команды, чтобы не засорять приветствие.
    # Команды и примеры лучше в /new_contest и /cancel_contest.
    await message.reply(
        "Привет! Я бот для проведения розыгрышей. "
        "Для создания нового конкурса используй команду /new_contest"
        "/cancel_contest [ID канала] для отмены розыгрыша , пример: /cancel_contest -1001234567890"
    )

@dp.message_handler(commands=['new_contest'])
async def start_new_contest(message: types.Message):
    """
    Запрос информации для нового конкурса. 
    """
    if message.chat.type != types.ChatType.PRIVATE:
        await message.reply("Настраивать конкурс нужно в личных сообщениях со мной.")
        return

    await message.reply(
        "**Введите данные для конкурса в формате (через новую строку):**\n"
        "1. **Время публикации (YYYY-MM-DD HH:MM):**\n"
        "2. **Время окончания (YYYY-MM-DD HH:MM):**\n"
        "3. **Юзернейм Канала (@channel_username):** (Бот должен быть админом!)\n"
        "4. **Количество победителей (число):**\n"
        "5. **Текст поста:** (начинается с новой строки)\n\n"
        "**Пример:**\n"
        "2025-12-31 18:00\n"
        "2026-01-01 12:00\n"
        "@MyTestChannel\n"
        "3\n"
        "Участвуй в нашем розыгрыше!"
    )

@dp.message_handler(commands=['cancel_contest'])
async def cancel_contest_handler(message: types.Message):
    """
    Обработчик для отмены запланированного или активного конкурса.
    """
    if message.chat.type != types.ChatType.PRIVATE:
        await message.reply("Отменять конкурс нужно в личных сообщениях со мной.")
        return

    args = message.get_args().strip()
    
    if not args:
        # Если аргументов нет, выводим список активных/запланированных
        if not CONTESTS:
            await message.reply("Нет активных или запланированных розыгрышей для отмены.")
            return

        # Формируем список для пользователя
        contest_list = []
        for contest_id, data in CONTESTS.items():
            
            is_active = data['post_message_id'] is not None
            status = "✅ Активный" if is_active else "⏳ Запланирован"
            
            # Окончательно исправленный блок: Явная конкатенация строк
            contest_entry = "**Канал:** " + data['channel_username'] + f" ({status})" + "\n"
            contest_entry += f"**ID:** `{data['channel_id']}`"
            
            contest_list.append(contest_entry)
            
        list_text = (
            "**Активные и запланированные розыгрыши:**\n\n"
            + "---\n".join(contest_list) 
            + "\n\nЧтобы отменить, введите команду: `/cancel_contest -100xxxxxxxxxx`"
        )
        await message.reply(list_text, parse_mode=types.ParseMode.MARKDOWN)
        return
        
    # Пытаемся отменить конкурс
    channel_id_to_cancel = args
    
    if channel_id_to_cancel not in CONTESTS:
        await message.reply(f"❌ Розыгрыш для ID канала `{channel_id_to_cancel}` не найден.")
        return

    contest_data = CONTESTS[channel_id_to_cancel]
    is_active = contest_data['post_message_id'] is not None

    if cancel_contest_logic(channel_id_to_cancel, contest_data['channel_id'], is_active):
        status_msg = "Активный розыгрыш" if is_active else "Запланированный розыгрыш"
        
        # Попытка удалить пост из канала
        if is_active and contest_data['post_message_id']:
            try:
                await bot.delete_message(contest_data['channel_id'], contest_data['post_message_id'])
            except Exception as e:
                logging.error(f"Не удалось удалить сообщение из канала: {e}")
                
        await message.reply(f"✅ {status_msg} в канале **{contest_data['channel_username']}** успешно отменен.", 
                            parse_mode=types.ParseMode.MARKDOWN)
    else:
        await message.reply(f"❌ Не удалось отменить розыгрыш для канала **{contest_data['channel_username']}**.", 
                            parse_mode=types.ParseMode.MARKDOWN)


@dp.message_handler()
async def process_contest_input(message: types.Message):
    """
    Обработка введенных настроек конкурса.
    """
    if message.chat.type != types.ChatType.PRIVATE:
        return 
        
    lines = message.text.strip().split('\n')
    if len(lines) < 5:
        return 

    try:
        publish_time_str = lines[0].strip()
        end_time_str = lines[1].strip()
        channel_username = lines[2].strip()
        winners_count = int(lines[3].strip())
        post_text = '\n'.join(lines[4:]).strip()
        
        publish_time = datetime.strptime(publish_time_str, "%Y-%m-%d %H:%M")
        end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")

        if end_time <= publish_time:
            await message.reply("❌ Время окончания должно быть позже времени публикации.")
            return

        chat_info = await bot.get_chat(channel_username)
        channel_id = str(chat_info.id)
        
        contest_id = channel_id
        
        if contest_id in CONTESTS:
             await message.reply("❌ В этом канале уже запущен или запланирован другой конкурс.")
             return
             
        CONTESTS[contest_id] = {
            'end_time': end_time,
            'channel_username': channel_username,
            'channel_id': channel_id,
            'winners_count': winners_count,
            'post_text': post_text,
            'participants': {}, 
            'post_message_id': None,
        }

        now = datetime.now()
        
        if publish_time <= now:
             await publish_contest(contest_id)
             await message.reply("Время публикации прошло, конкурс опубликован немедленно.")
        else:
            scheduler.add_job(
                publish_contest, 
                'date', 
                run_date=publish_time,
                args=[contest_id],
                id=f"publish_{contest_id}"
            )
            await message.reply(
                f"✅ Конкурс запланирован!\n"
                f"Публикация: **{publish_time_str}** в канале **{channel_username}**.\n"
                f"Завершение: **{end_time_str}**."
            )

    except ValueError:
        await message.reply("❌ Ошибка формата данных. Проверьте дату/время (YYYY-MM-DD HH:MM) и количество победителей (число).")
    except ChatNotFound:
        await message.reply(f"❌ Канал с юзернеймом {channel_username} не найден. Проверьте правильность ввода.")
    except Exception as e:
        logging.exception("Ошибка при обработке ввода конкурса:")
        await message.reply(f"❌ Произошла непредвиденная ошибка: {e}")


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('participate_'))
async def process_participation(callback_query: types.CallbackQuery):
    """
    Обработка нажатия кнопки "Участвую!"
    """
    contest_id = callback_query.data.split('_')[1]
    user_id = callback_query.from_user.id
    
    contest_data = CONTESTS.get(contest_id)
    
    if not contest_data:
        await bot.answer_callback_query(callback_query.id, "❌ Этот конкурс неактивен.", show_alert=True)
        return

    # Проверка подписки перед регистрацией
    if not await check_subscription(user_id, contest_id):
        channel_username = contest_data['channel_username'].lstrip('@')
        channel_url = f"https://t.me/{channel_username}"
        
        check_keyboard = InlineKeyboardMarkup().add(
            InlineKeyboardButton(text="👉 Подписаться", url=channel_url),
            InlineKeyboardButton(text="✅ Проверить подписку", callback_data=f"checksub_{contest_id}")
        )
        
        await bot.answer_callback_query(
            callback_query.id, 
            "🛑 Вы не выполнили обязательное условие! Проверьте ЛС бота.", 
            show_alert=True
        )
        
        await bot.send_message(
            user_id, 
            f"Для участия в конкурсе необходимо подписаться на канал {contest_data['channel_username']}.",
            reply_markup=check_keyboard
        )
        return

    if user_id in contest_data['participants']:
        await bot.answer_callback_query(callback_query.id, "Вы уже участвуете!", show_alert=False)
    else:
        contest_data['participants'][user_id] = True
        await bot.answer_callback_query(callback_query.id, "🎉 Вы успешно зарегистрированы!", show_alert=False)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('checksub_'))
async def process_check_subscription(callback_query: types.CallbackQuery):
    """
    Обработка нажатия кнопки "Проверить подписку" в личном сообщении.
    """
    contest_id = callback_query.data.split('_')[1]
    user_id = callback_query.from_user.id
    
    contest_data = CONTESTS.get(contest_id)
    
    if not contest_data:
        await bot.answer_callback_query(callback_query.id, "❌ Конкурс не найден.", show_alert=True)
        return

    if await check_subscription(user_id, contest_id):
        contest_data['participants'][user_id] = True
        
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text="✅ Подписка подтверждена! Вы участвуете в розыгрыше.",
                reply_markup=None
            )
        except Exception as e:
            # Перехватываем ошибки, включая "Message is not modified"
            if "message is not modified" not in str(e):
                logging.error(f"Ошибка при редактировании сообщения: {e}")
            pass 

        await bot.answer_callback_query(callback_query.id, "✅ Подписка подтверждена! Вы участвуете!", show_alert=True)
        
    else:
        await bot.answer_callback_query(callback_query.id, "🛑 Подписка не найдена. Попробуйте еще раз!", show_alert=True)


# --- ЗАПУСК БОТА ---

async def on_startup(dp):
    """
    Инициализация и запуск планировщика при старте бота.
    """
    scheduler.start()
    logging.info("Bot started and APScheduler initialized!")

if __name__ == '__main__':

    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)

