# Развёртывание

Выбранная инфраструктура: сервер Timeweb Cloud в российском регионе, база и offsite-копии в РФ; Kinescope для видеотрафика; GetCourse для оплаты; Unisender Go Web API для почты.

## Запуск

1. Создайте сервер Linux с Docker Compose и направьте домен на него. Закройте внешние порты PostgreSQL/Redis; Compose их не публикует. Открыты только 80/443 и управляемый SSH.
2. Скопируйте `.env.example` в `.env`. Задайте сильные SECRET_KEY, GC_WEBHOOK_SECRET и пароль базы. DATABASE_URL и POSTGRES_PASSWORD должны совпадать. ENVIRONMENT=production, PUBLIC_URL=https://домен, SITE_HOST=домен. Секреты вводятся на сервере и не попадают в GitHub.
3. `docker compose up -d db redis`.
4. `docker compose run --rm api alembic upgrade head`.
5. `docker compose run --rm api python -m app.manage seed`.
6. `docker compose run --rm api python -m app.manage admin --email ваш-email` (пароль вводится интерактивно).
7. `docker compose up -d --build`.
8. Откройте `/admin`, подключите ID видео, длительности и советы; публикуйте после проверки. Для каждого цикла нужны 21 короткое видео; для продаж доптарифа — 21 полное.

Caddy получает HTTPS-сертификат для SITE_HOST. В production сервер отклоняет слабый ключ, SQLite и HTTP PUBLIC_URL.

## Kinescope

Включите шифрование у видео и внешнюю авторизацию с URL `https://домен/api/kinescope/authorize`, strict=true, Basic username/password, соответствующими KINESCOPE_USERNAME/KINESCOPE_PASSWORD. Ограничьте встраивание доменом сайта. Используйте именно ID видео из API, соответствующий параметру id в DRM-callback. Для iframe API допустим короткий код; при несовпадении кода плеера и UUID DRM храните UUID видео как video_id. Проверьте поддержку UUID встроенным плеером до публикации.

[Официальная инструкция Kinescope](https://docs.kinescope.ru/instrukcii-dlya-razrabotchikov/avtorizatsionnyy-bekend-dlya-kontrolya-dostupa-k-video/).

## Почта

Настройте отправляющий домен, SPF, DKIM и DMARC. Введите MAIL_API_KEY и MAIL_SENDER. Для нового аккаунта проверьте лимиты и прогрев домена. Сервис использует Web API и не зависит от SMTP-портов хостинга. Напоминания требуют подтверждённого адреса; подтверждение не блокирует покупку. Запускайте только один Beat.

## Обновления и резервные копии

Перед миграциями: `sh deploy/backup.sh`. Копируйте результат на отдельное хранилище в РФ. Настройте ежедневный запуск и срок хранения. Пример восстановления проверяется на отдельной базе:

```sh
gunzip -c backups/выбранная-копия.sql.gz | docker compose exec -T db psql -U vypryamis -d восстановительная-база
```

Для релиза используйте конкретный проверенный commit; на сервер доставляйте `git archive`, выполните бэкап, миграции, пересборку и `/api/health`. Возврат к предыдущему образу не откатывает миграции автоматически: учитывайте совместимость схемы.

Ошибки API пишет Uvicorn, фоновых задач — Celery. Проверяйте `docker compose logs api worker beat`. Настройте ротацию Docker-логов, уведомление о падении healthcheck и внешний мониторинг. Не включайте логирование URL внешних API с ключами и тела callback с секретами. `/api/admin/integrations` показывает наличие настроек, а не результат живого запроса к провайдеру.

## Пределы проверки

Локально проверены логика сервиса, сборка интерфейса и браузерное превью. Docker на машине разработки недоступен, поэтому полный production Compose требует прогона на сервере. До рекламы обязательны сквозная покупка на реальном GetCourse, проверка доставки Mail.ru/Яндекс, DRM, возврата, восстановления базы и физического iPhone/Safari. Демо и автоматические тесты не заменяют эти проверки.
