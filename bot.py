import os
import logging
import requests
import json
import time
from datetime import datetime, timedelta
from telegram import Update, ParseMode, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, ConversationHandler
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
load_dotenv()

# Конфигурация логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получение API ключей из переменных окружения
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Загрузка системного промта
with open('data/system_prompt.txt', 'r', encoding='utf-8') as f:
    SYSTEM_PROMPT = f.read()

# Словарь для хранения историй чатов пользователей и времени последней активности
user_chat_history = {}
user_last_active = {}

# Время неактивности в часах, после которого чат будет очищен
INACTIVE_HOURS = 24

# Создание клавиатуры с кнопками команд
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ['/start', '/help'],
            ['/reset']
        ],
        resize_keyboard=True
    )

def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start."""
    user_id = update.effective_user.id

    # Инициализация истории чата пользователя
    user_chat_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Обновление времени последней активности
    user_last_active[user_id] = datetime.now()

    # Приветственное сообщение
    welcome_message = (
        "👋 Привет! Я профориентационный бот Поволжского государственного университета сервиса.\n\n"
        "Я помогу вам выбрать подходящую программу обучения, задав несколько вопросов о ваших интересах и предпочтениях.\n\n"
        "Давайте начнем! Расскажите немного о себе и о том, какие предметы вам нравились в школе?"
    )

    update.message.reply_text(welcome_message, reply_markup=get_main_keyboard())


def handle_message(update: Update, context: CallbackContext) -> None:
    """Обработчик всех сообщений."""
    user_id = update.effective_user.id
    user_message = update.message.text

    # Проверяем, существует ли история чата для пользователя
    if user_id not in user_chat_history:
        user_chat_history[user_id] = [
            {"role": "system", "content": SYSTEM_PROMPT}]

    # Обновление времени последней активности
    user_last_active[user_id] = datetime.now()

    # Добавляем сообщение пользователя в историю
    user_chat_history[user_id].append(
        {"role": "user", "content": user_message})

    # Отправляем индикатор набора текста
    context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                 action='typing')

    # Настройка механизма повторных попыток
    max_retries = 3  # Максимальное количество попыток
    retry_count = 0
    initial_delay = 2  # Начальная задержка в секундах

    while retry_count < max_retries:
        try:
            # Отправка запроса к OpenRouter API с DeepSeek моделью
            response = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": user_chat_history[user_id]
                },
                timeout=60  # Увеличенный таймаут для больших ответов
            )

            # Обработка ответа
            if response.status_code == 200:
                bot_response = response.json()["choices"][0]["message"][
                    "content"]

                # Сохранение ответа бота в историю
                user_chat_history[user_id].append(
                    {"role": "assistant", "content": bot_response})

                # Отправка ответа пользователю
                update.message.reply_text(bot_response,
                                          parse_mode=ParseMode.MARKDOWN,
                                          reply_markup=get_main_keyboard())
                # Успешный ответ, выходим из цикла
                break

            elif response.status_code == 429:  # Too Many Requests
                # Увеличиваем задержку при ограничении запросов
                retry_delay = initial_delay * (
                            2 ** retry_count)  # Экспоненциальная задержка
                logger.warning(
                    f"Ограничение запросов API (429). Повтор через {retry_delay} секунд. Попытка {retry_count + 1}/{max_retries}")

                # Продолжаем показывать пользователю, что бот печатает
                context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                             action='typing')

                time.sleep(retry_delay)
                retry_count += 1

                # Если это была последняя попытка
                if retry_count == max_retries:
                    update.message.reply_text(
                        "Извините, сервер сейчас перегружен. Пожалуйста, повторите запрос через несколько минут.",
                        reply_markup=get_main_keyboard())

            else:
                # Другие ошибки API
                logger.error(
                    f"Ошибка API: {response.status_code} - {response.text}")

                # Для 5xx ошибок (ошибки сервера) делаем повторные попытки
                if 500 <= response.status_code < 600:
                    retry_delay = initial_delay * (2 ** retry_count)
                    logger.warning(
                        f"Ошибка сервера API ({response.status_code}). Повтор через {retry_delay} секунд. Попытка {retry_count + 1}/{max_retries}")

                    context.bot.send_chat_action(
                        chat_id=update.effective_chat.id, action='typing')

                    time.sleep(retry_delay)
                    retry_count += 1

                    # Если это была последняя попытка
                    if retry_count == max_retries:
                        update.message.reply_text(
                            "Извините, возникла ошибка на сервере. Пожалуйста, попробуйте позже.",
                            reply_markup=get_main_keyboard())
                else:
                    # Для других кодов ошибок не делаем повторных попыток
                    update.message.reply_text(
                        "Извините, возникла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже.",
                        reply_markup=get_main_keyboard())
                    break

        except requests.exceptions.Timeout:
            # Обработка таймаута
            retry_delay = initial_delay * (2 ** retry_count)
            logger.warning(
                f"Таймаут API. Повтор через {retry_delay} секунд. Попытка {retry_count + 1}/{max_retries}")

            context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                         action='typing')

            time.sleep(retry_delay)
            retry_count += 1

            if retry_count == max_retries:
                update.message.reply_text(
                    "Извините, сервер не отвечает. Ваш запрос слишком сложный или возникли проблемы с соединением. Пожалуйста, попробуйте еще раз или упростите ваш вопрос.",
                    reply_markup=get_main_keyboard())

        except (requests.exceptions.ConnectionError,
                requests.exceptions.RequestException) as e:
            # Обработка проблем с соединением
            retry_delay = initial_delay * (2 ** retry_count)
            logger.warning(
                f"Ошибка соединения: {str(e)}. Повтор через {retry_delay} секунд. Попытка {retry_count + 1}/{max_retries}")

            context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                         action='typing')

            time.sleep(retry_delay)
            retry_count += 1

            if retry_count == max_retries:
                update.message.reply_text(
                    "Извините, возникли проблемы с подключением к серверу. Пожалуйста, проверьте ваше соединение и попробуйте позже.",
                    reply_markup=get_main_keyboard())

        except Exception as e:
            # Обработка других непредвиденных ошибок
            logger.error(
                f"Непредвиденная ошибка при обработке сообщения: {str(e)}")

            # Для общих ошибок пробуем ещё раз с задержкой
            if retry_count < max_retries - 1:  # Если это не последняя попытка
                retry_delay = initial_delay * (2 ** retry_count)
                logger.warning(
                    f"Повтор через {retry_delay} секунд. Попытка {retry_count + 1}/{max_retries}")

                context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                             action='typing')
                time.sleep(retry_delay)
                retry_count += 1
            else:
                # На последней попытке выводим сообщение об ошибке
                update.message.reply_text(
                    "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте еще раз позже или обратитесь к администратору.",
                    reply_markup=get_main_keyboard())
                break


def reset(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /reset для сброса истории чата."""
    user_id = update.effective_user.id

    # Сброс истории чата
    user_chat_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Обновление времени последней активности
    user_last_active[user_id] = datetime.now()

    update.message.reply_text(
        "История беседы сброшена. Давайте начнем заново! Расскажите о своих интересах и предпочтениях.",
        reply_markup=get_main_keyboard()
    )


def help_command(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /help."""
    help_text = (
        "🔍 *Помощь по использованию бота*\n\n"
        "Я профориентационный бот ТолГАС, который поможет вам выбрать подходящую программу обучения.\n\n"
        "*Доступные команды:*\n"
        "/start - Начать новую беседу\n"
        "/reset - Сбросить историю беседы и начать заново\n"
        "/help - Показать это сообщение\n\n"
        "Просто отвечайте на мои вопросы, и в конце беседы я предложу вам наиболее подходящие программы обучения в ПВГУС."
    )

    # Обновление времени последней активности
    user_id = update.effective_user.id
    user_last_active[user_id] = datetime.now()

    update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN,
                              reply_markup=get_main_keyboard())


def error_handler(update: Update, context: CallbackContext) -> None:
    """Обработчик ошибок."""
    logger.error(f"Ошибка: {context.error} при обработке {update}")


def cleanup_inactive_chats(context: CallbackContext) -> None:
    """Функция для очистки неактивных чатов."""
    current_time = datetime.now()
    inactive_threshold = current_time - timedelta(hours=INACTIVE_HOURS)

    # Список ID пользователей для удаления
    users_to_remove = []

    for user_id, last_active in user_last_active.items():
        if last_active < inactive_threshold:
            users_to_remove.append(user_id)

    # Удаление неактивных чатов
    for user_id in users_to_remove:
        if user_id in user_chat_history:
            del user_chat_history[user_id]
        del user_last_active[user_id]

    if users_to_remove:
        logger.info(f"Очищено {len(users_to_remove)} неактивных чатов")


def main() -> None:
    """Запуск бота."""
    # Создание Updater и передача токена бота
    updater = Updater(TELEGRAM_BOT_TOKEN)

    # Получение диспетчера для регистрации обработчиков
    dispatcher = updater.dispatcher

    # Регистрация обработчиков команд
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("reset", reset))

    # Обработчик сообщений
    dispatcher.add_handler(
        MessageHandler(Filters.text & ~Filters.command, handle_message))

    # Регистрация обработчика ошибок
    dispatcher.add_error_handler(error_handler)

    # Добавление задачи на периодическую очистку неактивных чатов
    # Запускаем очистку каждые 6 часов
    updater.job_queue.run_repeating(cleanup_inactive_chats, interval=21600,
                                    first=21600)

    # Запуск бота
    updater.start_polling()
    logger.info("Бот запущен")

    # Работа бота до нажатия Ctrl-C
    updater.idle()


if __name__ == '__main__':
    main()
