import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from telegram import Update

from .telegram_bot import setup_bot
from .models import Transaction
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


@csrf_exempt
async def telegram_webhook(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            update = Update.de_json(data, bot=setup_bot().bot)
            await setup_bot().process_update(update)
            return JsonResponse({"status": "ok"})
        except Exception as e:
            logger.error("Error processing telegram webhook: %s", e)
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    return JsonResponse({"status": "error", "message": "Invalid request method"}, status=405)


@csrf_exempt
async def yookassa_webhook(request):
    if request.method == "POST":
        try:
            event_json = json.loads(request.body.decode("utf-8"))
            notification_type = event_json.get('event')
            payment_id = event_json.get('object', {}).get('id')
            payment_status = event_json.get('object', {}).get('status')

            if notification_type == 'payment.succeeded' and payment_id:
                transaction = await sync_to_async(Transaction.objects.get)(yookassa_payment_id=payment_id)
                if transaction.status == 'pending':
                    transaction.status = 'completed'
                    await sync_to_async(transaction.save)()

                    user = transaction.user
                    user.balance += transaction.amount
                    await sync_to_async(user.save)()

                    logger.info(f"User {user.user.username} (ID: {user.telegram_id}) deposited {transaction.amount}. New balance: {user.balance}")
                    # Опционально: отправить уведомление пользователю через Telegram

            elif notification_type == 'payment.canceled' and payment_id:
                transaction = await sync_to_async(Transaction.objects.get)(yookassa_payment_id=payment_id)
                if transaction.status == 'pending':
                    transaction.status = 'failed'
                    await sync_to_async(transaction.save)()
                    logger.warning(f"YooKassa payment {payment_id} cancelled for user {transaction.user.telegram_id}")
                    # Опционально: отправить уведомление пользователю

            return JsonResponse({"status": "ok"})
        except Transaction.DoesNotExist:
            logger.error("Transaction with yookassa_payment_id %s not found.", payment_id)
            return JsonResponse({"status": "error", "message": "Transaction not found"}, status=404)
        except Exception as e:
            logger.error("Error processing YooKassa webhook: %s", e)
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    return JsonResponse({"status": "error", "message": "Invalid request method"}, status=405)
