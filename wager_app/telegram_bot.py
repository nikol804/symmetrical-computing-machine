import logging
import os
import uuid

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from django.contrib.auth.models import User
from wager_app.models import TelegramUser, WagerMatch, Transaction
from asgiref.sync import sync_to_async
from yookassa import Configuration, Payment
from django.conf import settings

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')


async def get_or_create_telegram_user(update: Update):
    user_data = update.effective_user
    telegram_user, created = await sync_to_async(TelegramUser.objects.get_or_create)(
        telegram_id=str(user_data.id),
        defaults={'user': await sync_to_async(User.objects.get_or_create)(username=user_data.username or user_data.id)[0]}
    )
    return telegram_user


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await get_or_create_telegram_user(update)
    await update.message.reply_text('Привет! Я бот для организации wager-матчей.')


async def wager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user = await get_or_create_telegram_user(update)
    try:
        amount = float(context.args[0])
        if amount <= 0:
            await update.message.reply_text("Сумма ставки должна быть положительным числом.")
            return
        if telegram_user.balance < amount:
            await update.message.reply_text(f"Недостаточно средств. Ваш баланс: {telegram_user.balance}")
            return

        # Создать новый WagerMatch
        wager_match = await sync_to_async(WagerMatch.objects.create)(
            player1=telegram_user,
            amount=amount,
            status='pending'
        )
        await update.message.reply_text(
            f"Wager-матч на {amount} создан! Ожидаем второго игрока. ID матча: {wager_match.id}"
        )
    except (IndexError, ValueError):
        await update.message.reply_text("Пожалуйста, укажите сумму ставки. Например: `/wager 100`")


async def join_wager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user = await get_or_create_telegram_user(update)
    try:
        match_id = int(context.args[0])
        wager_match = await sync_to_async(WagerMatch.objects.get)(id=match_id)

        if wager_match.status != 'pending':
            await update.message.reply_text("Этот wager-матч уже не ожидает игроков или завершен.")
            return

        if wager_match.player1 == telegram_user:
            await update.message.reply_text("Вы не можете присоединиться к собственному wager-матчу.")
            return

        if telegram_user.balance < wager_match.amount:
            await update.message.reply_text(f"Недостаточно средств. Ваш баланс: {telegram_user.balance}")
            return

        wager_match.player2 = telegram_user
        wager_match.status = 'active'
        await sync_to_async(wager_match.save)()

        # Создание транзакций для обоих игроков (уменьшение баланса)
        await sync_to_async(Transaction.objects.create)(
            user=wager_match.player1,
            type='wager_out',
            amount=-wager_match.amount, # Отрицательное значение для списания
            status='completed'
        )
        await sync_to_async(Transaction.objects.create)(
            user=wager_match.player2,
            type='wager_out',
            amount=-wager_match.amount, # Отрицательное значение для списания
            status='completed'
        )
        # Обновление баланса игроков
        wager_match.player1.balance -= wager_match.amount
        wager_match.player2.balance -= wager_match.amount
        await sync_to_async(wager_match.player1.save)()
        await sync_to_async(wager_match.player2.save)()

        await update.message.reply_text(
            f"Вы успешно присоединились к wager-матчу {match_id} на {wager_match.amount}. Матч активен!"
        )
        # Отправить уведомление первому игроку
        await context.bot.send_message(
            chat_id=wager_match.player1.telegram_id,
            text=f"Игрок {telegram_user.user.username} присоединился к вашему wager-матчу {match_id}! Матч активен."
        )

    except (IndexError, ValueError):
        await update.message.reply_text("Пожалуйста, укажите ID матча. Например: `/join 123`")
    except WagerMatch.DoesNotExist:
        await update.message.reply_text("Wager-матч с таким ID не найден.")


async def win_wager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user = await get_or_create_telegram_user(update)
    try:
        match_id = int(context.args[0])
        wager_match = await sync_to_async(WagerMatch.objects.get)(id=match_id)

        if wager_match.status != 'active':
            await update.message.reply_text("Этот wager-матч неактивен.")
            return

        if telegram_user not in [wager_match.player1, wager_match.player2]:
            await update.message.reply_text("Вы не участвуете в этом wager-матче.")
            return

        # Предполагаем, что игрок, который вызвал /win, является победителем
        winner = telegram_user
        loser = wager_match.player1 if winner == wager_match.player2 else wager_match.player2

        wager_match.winner = winner
        wager_match.status = 'completed'
        await sync_to_async(wager_match.save)()

        # Выплата победителю
        winnings = wager_match.amount * 2 # Сумма ставок обоих игроков
        winner.balance += winnings
        await sync_to_async(winner.save)()

        await sync_to_async(Transaction.objects.create)(
            user=winner,
            type='payout',
            amount=winnings,
            status='completed'
        )

        await update.message.reply_text(
            f"Поздравляем! Вы выиграли {winnings} в wager-матче {match_id}. Ваш новый баланс: {winner.balance}"
        )
        # Уведомить проигравшего
        await context.bot.send_message(
            chat_id=loser.telegram_id,
            text=f"Вы проиграли wager-матч {match_id}. Победитель: {winner.user.username}. Ваш новый баланс: {loser.balance}"
        )

    except (IndexError, ValueError):
        await update.message.reply_text("Пожалуйста, укажите ID матча. Например: `/win 123`")
    except WagerMatch.DoesNotExist:
        await update.message.reply_text("Активный wager-матч с таким ID не найден.")


async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user = await get_or_create_telegram_user(update)
    try:
        amount = float(context.args[0])
        if amount <= 0:
            await update.message.reply_text("Сумма депозита должна быть положительным числом.")
            return

        Configuration.account_id = settings.YOOKASSA_SHOP_ID
        Configuration.secret_key = settings.YOOKASSA_SECRET_KEY

        idempotence_key = str(uuid.uuid4())
        payment = Payment.create({
            "amount": {
                "value": str(amount),
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://wager-telegram-bot.onrender.com/telegram/yookassa-webhook/" # Замените на URL вашего бота
            },
            "capture": True,
            "description": f"Депозит для пользователя {telegram_user.user.username}",
            "metadata": {
                "telegram_user_id": telegram_user.telegram_id
            }
        }, idempotence_key)

        confirmation_url = payment.confirmation.confirmation_url
        payment_id = payment.id

        await sync_to_async(Transaction.objects.create)(
            user=telegram_user,
            type='deposit',
            amount=amount,
            status='pending',
            yookassa_payment_id=payment_id
        )

        await update.message.reply_text(
            f"Для пополнения баланса на {amount} RUB перейдите по ссылке: {confirmation_url}"
        )

    except (IndexError, ValueError):
        await update.message.reply_text("Пожалуйста, укажите сумму депозита. Например: `/deposit 500`")
    except Exception as e:
        logger.error("Error creating YooKassa payment: %s", e)
        await update.message.reply_text("Произошла ошибка при создании платежа. Попробуйте позже.")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user = await get_or_create_telegram_user(update)
    await update.message.reply_text(f"Ваш текущий баланс: {telegram_user.balance} RUB.")


async def cancel_wager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user = await get_or_create_telegram_user(update)
    try:
        match_id = int(context.args[0])
        wager_match = await sync_to_async(WagerMatch.objects.get)(id=match_id)

        if wager_match.status != 'pending':
            await update.message.reply_text("Можно отменить только ожидающий wager-матч.")
            return

        if wager_match.player1 != telegram_user:
            await update.message.reply_text("Вы можете отменить только созданный вами wager-матч.")
            return

        wager_match.status = 'cancelled'
        await sync_to_async(wager_match.save)()

        await update.message.reply_text(f"Wager-матч {match_id} отменен.")

    except (IndexError, ValueError):
        await update.message.reply_text("Пожалуйста, укажите ID матча. Например: `/cancel 123`")
    except WagerMatch.DoesNotExist:
        await update.message.reply_text("Wager-матч с таким ID не найден.")


async def payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user = await get_or_create_telegram_user(update)
    try:
        amount = float(context.args[0])
        if amount <= 0:
            await update.message.reply_text("Сумма для вывода должна быть положительным числом.")
            return
        if telegram_user.balance < amount:
            await update.message.reply_text(f"Недостаточно средств. Ваш баланс: {telegram_user.balance}")
            return

        # Здесь должна быть логика взаимодействия с YooKassa API для вывода средств.
        # Для полноценной реализации потребуется настроить приватный ключ YooKassa и использовать API для выплат.
        # Сейчас это просто заглушка.
        telegram_user.balance -= amount
        await sync_to_async(telegram_user.save)()

        await sync_to_async(Transaction.objects.create)(
            user=telegram_user,
            type='payout',
            amount=-amount,  # Отрицательное значение для списания
            status='completed' # В реальной системе статус будет 'pending' до подтверждения YooKassa
        )

        await update.message.reply_text(
            f"Запрос на вывод {amount} RUB отправлен. Ваш новый баланс: {telegram_user.balance} RUB.\n" +
            "Для полноценной интеграции выплат YooKassa требуется дополнительная настройка API (приватный ключ и т.д.)."
        )

    except (IndexError, ValueError):
        await update.message.reply_text("Пожалуйста, укажите сумму для вывода. Например: `/payout 100`")
    except Exception as e:
        logger.error("Error processing payout: %s", e)
        await update.message.reply_text("Произошла ошибка при обработке вывода средств. Попробуйте позже.")


def setup_bot():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("wager", wager))
    application.add_handler(CommandHandler("join", join_wager))
    application.add_handler(CommandHandler("win", win_wager))
    application.add_handler(CommandHandler("deposit", deposit))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("cancel", cancel_wager))
    application.add_handler(CommandHandler("payout", payout))

    return application
