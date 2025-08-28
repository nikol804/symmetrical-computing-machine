from django.db import models
from django.contrib.auth.models import User


class TelegramUser(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    telegram_id = models.CharField(max_length=255, unique=True)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"{self.user.username} ({self.telegram_id})"


class WagerMatch(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Ожидание второго игрока'),
        ('active', 'Активный'),
        ('completed', 'Завершен'),
        ('cancelled', 'Отменен'),
    ]

    player1 = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='wager_matches_as_player1')
    player2 = models.ForeignKey(TelegramUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='wager_matches_as_player2')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    winner = models.ForeignKey(TelegramUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='wager_matches_won')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wager Match {self.id} - {self.player1.user.username} vs {self.player2.user.username if self.player2 else ''}"


class Transaction(models.Model):
    TYPE_CHOICES = [
        ('deposit', 'Депозит'),
        ('payout', 'Выплата'),
        ('wager_in', 'Ставка (вход)'),
        ('wager_out', 'Ставка (выход)'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Ожидает'),
        ('completed', 'Завершен'),
        ('failed', 'Неудачно'),
    ]

    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    yookassa_payment_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Transaction {self.id} - {self.user.user.username} - {self.type} - {self.amount}"
