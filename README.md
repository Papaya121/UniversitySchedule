# UniversitySchedule

Утреннее расписание отправляется в 09:00 либо за 90 минут до первой пары,
если рассчитанное время получается раньше 09:00. В день без пар отправка
остаётся в 09:00.

При запросе расписания на день текущая пара выделяется жирным целиком,
на перемене выделяется следующая. До первой пары выделение появляется за
30 минут, после последней пары его нет. В расписании на неделю выделяется
весь текущий день. Время определяется часовым поясом бота (`TIMEZONE`).
Выделение включено по умолчанию для новых и существующих пользователей,
отключается в «⚙️ Настройки» и не требует отдельного вопроса при запуске.

Защита расписания:

- полностью пустая выдача сайта не заменяет ранее сохранённое расписание;
- администратор получает одно предупреждение с названием затронутой группы;
- новые дни с парами объединяются в одно уведомление о диапазоне дат;
- изменения дня, где пары уже были, отправляются подробным уведомлением.

Резервные копии базы:

- при каждом запуске и ежедневно в 03:00 создаётся согласованный снимок SQLite;
- копии хранятся в `backups/`, автоматически проверяются и удаляются через 14 дней;
- время, каталог и срок хранения настраиваются переменными `BACKUP_*` из `.env`;
- Docker сохраняет каталог копий на хосте через bind mount. Для защиты от потери
  всего сервера каталог `backups/` нужно дополнительно синхронизировать во внешнее
  хранилище или подключить к `/app/backups` отдельный сетевой диск.

## Развёртывание без Docker

Бот работает как пользовательский systemd-сервис `university-schedule.service`.
Код хранится в `/home/papaya/UniversitySchedule`, а `.env`, SQLite-база и резервные
копии не копируются из Git и не удаляются при развёртывании.

Проверка состояния и просмотр журнала:

```bash
systemctl --user status university-schedule.service
journalctl --user -u university-schedule.service -f
```

Чтобы пользовательские сервисы запускались сразу после перезагрузки сервера, один
раз выполните на сервере:

```bash
sudo loginctl enable-linger papaya
```

Workflow `.github/workflows/deploy.yml` запускается только после push в `main` или
вручную. Self-hosted runner должен иметь дополнительную метку
`university-schedule`. Перед каждым обновлением workflow запускает тесты, делает
согласованную резервную копию SQLite и только после этого перезапускает бота.

Push в `dev` запускает отдельный workflow `.github/workflows/deploy-dev.yml`.
Он обновляет только `/home/papaya/UniversitySchedule-dev` и пользовательский сервис
`university-schedule-dev.service`. Токен берётся из секрета репозитория
`TELEGRAM_BOT_TOKEN_DEV` и записывается в отдельный `.env`. База SQLite и список
пользователей у тестового бота отдельные и при первом запуске пустые. ID
администратора dev-бота — `959026123`; он обновляется при каждом деплое. Продакшен
остаётся в `/home/papaya/UniversitySchedule` под сервисом
`university-schedule.service`. Для проверки тестового бота:

```bash
systemctl --user status university-schedule-dev.service
journalctl --user -u university-schedule-dev.service -f
```

## Экстренное отключение

В `/admin` → «Отключения» есть пауза для пользователей и полное отключение.
Пауза сохраняется в базе и переживает перезапуск: обычные пользователи не могут
пользоваться ботом и не получают автоматические уведомления. Администратор
продолжает работать и может отправить ручную рассылку. Для включения паузы
нужно подтверждение; вернуть обычный режим можно там же.

Полное отключение требует двух подтверждений и точной фразы. Оно создаёт
`data/bot-disabled`, останавливает и отключает текущий systemd-сервис. Пока
маркер существует, бот не запустится даже после нового деплоя. Для ручного
восстановления через SSH на соответствующей машине удалите маркер и включите
нужный сервис, например для продакшена:

```bash
rm /home/papaya/UniversitySchedule/data/bot-disabled
systemctl --user enable --now university-schedule.service
```

Для dev используйте `/home/papaya/UniversitySchedule-dev/data/bot-disabled` и
`university-schedule-dev.service`. Если до полного отключения была включена
пауза, она останется включённой после восстановления, пока администратор не
выключит её в панели.

Runner установлен в `/home/papaya/actions-runner-university-schedule` и работает
как пользовательский сервис `github-actions-runner.service`. Для регистрации нужен
одноразовый токен из `Settings → Actions → Runners → New self-hosted runner`:

```bash
cd /home/papaya/actions-runner-university-schedule
./config.sh --unattended \
  --url https://github.com/Papaya121/UniversitySchedule \
  --token ONE_TIME_TOKEN \
  --name acer-ubuntu \
  --labels university-schedule \
  --work _work

mkdir -p ~/.config/systemd/user
install -m 0644 \
  /home/papaya/UniversitySchedule/deploy/github-actions-runner.service \
  ~/.config/systemd/user/github-actions-runner.service
systemctl --user daemon-reload
systemctl --user enable --now github-actions-runner.service
```
