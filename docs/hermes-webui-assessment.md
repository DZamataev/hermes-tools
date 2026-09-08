# Оценка Hermes WebUI как замены OpenWebUI для сессий Hermes Desktop

Дата проверки: 2026-09-07. Проверена стабильная версия Hermes WebUI
[`v0.52.113`](https://github.com/nesquena/hermes-webui/releases/tag/v0.52.113),
текущий `master` на коммите
[`e168b67`](https://github.com/nesquena/hermes-webui/commit/e168b67e4278df618d1cab61fdb3a8dc55b29a81),
исходники и открытые upstream issues/PR.

## Короткий вывод

**Сегодня Hermes WebUI не является полной заменой задуманному OpenWebUI bridge для
доступа к тем же живым сессиям Desktop.** Он гораздо ближе к Hermes и умеет читать
`state.db`, импортировать историю и продолжать CLI/TUI/Desktop-сессию с тем же
`session_id`, но:

1. на текущем upstream Desktop-сессии вообще не попадают в сайдбар; исправление
   находится в открытом, ещё не смерженном [PR #7415](https://github.com/nesquena/hermes-webui/pull/7415);
2. WebUI сохраняет собственную JSON-копию сессии и по умолчанию запускает отдельный
   `AIAgent` внутри своего процесса, то есть не подключается к уже живущему runtime и
   event stream Desktop;
3. upstream сам признаёт, что единое хранилище ещё не реализовано, а при одновременной
   записи из двух клиентов возможна рассинхронизация и потеря промежуточных сообщений.

Практическое решение: **не убирать OpenWebUI/идею live bridge прямо сейчас**. Hermes
WebUI стоит испытать как более нативный браузерный клиент для *последовательного*
переключения между Desktop и браузером после merge `#7415`; считать его заменой можно
только если нам не нужны одновременное отображение текущего turn в двух клиентах,
единая очередь и гарантированная синхронизация live-состояния.

## Что именно значит «та же сессия»

| Свойство | Hermes WebUI сейчас | Требование нашего live bridge |
|---|---|---|
| Увидеть историю из Hermes `state.db` | Да для поддерживаемых источников; Desktop сломан до `#7415` | Да |
| Сохранить исходный `session_id` | Да | Да |
| Продолжить сохранённый transcript | Да, после materialize/import | Да |
| Использовать тот же живой объект агента, что Desktop | Нет по умолчанию | Да |
| Видеть в браузере turn, начатый в Desktop, в реальном времени | Не гарантируется; это не общий event transport | Да |
| Безопасно писать одновременно из Desktop и браузера | Нет подтверждённой гарантии; есть открытый desync bug | Да, с очередью/idempotency/reconciliation |
| Единый источник истины без второй записи сессии | Нет, `state.db` + WebUI JSON sidecar | Hermes authoritative, UI — реплика |

## Модель сессий и storage

Hermes WebUI хранит собственное состояние в `$HERMES_HOME/webui`: отдельный JSON-файл
на сессию плюс `settings.json`, `projects.json` и другие файлы
([README, строки 646–648](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/README.md#L646-L648),
[ARCHITECTURE, строки 105–112](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/ARCHITECTURE.md#L105-L112)).
Одновременно он читает Hermes Agent `state.db`: README называет это «CLI session
bridge» — запись появляется в сайдбаре, импортируется с полной историей, после чего в
неё можно отвечать
([README, строка 230](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/README.md#L230)).

В коде импорт описан точнее: CLI/TUI/Desktop row с сообщениями превращается в
writeable объект `Session`, который **обязательно сохраняется как WebUI-owned
sidecar**, при этом исходный ID остаётся тем же
([routes.py, строки 7895–7910](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/routes.py#L7895-L7910),
[строки 7942–7945](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/routes.py#L7942-L7945),
[строки 21863–21873](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/routes.py#L21863-L21873)).
Messaging, cron, gateway, subagent и некоторые внешние источники остаются read-only и
не могут быть «захвачены» WebUI
([routes.py, строки 21881–21897](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/routes.py#L21881-L21897)).

Это не единое хранилище. Открытый [issue #498](https://github.com/nesquena/hermes-webui/issues/498)
прямо формулирует текущую архитектуру: WebUI и CLI используют разные stores, после
импорта существуют две записи одной беседы, а переход полностью на общий `SessionDB`
ещё только предлагается.

### Что это значит на нашей машине

Read-only запрос к `/Users/frenzy/.hermes/state.db` показал:

```text
desktop    140
subagent    12
api_server   7
tool         5
telegram     1
```

То есть наша основная масса сессий попадает ровно в проблемный `source=desktop`.
Открытый [PR #7415](https://github.com/nesquena/hermes-webui/pull/7415) сообщает, что
текущий нормализатор относит `cli`, `tui` и `acp` к интерактивным сессиям, но не
`desktop`, поэтому Desktop rows исключаются из обеих обычных sidebar views. PR
добавляет классификацию Desktop и тесты, но на дату проверки не смержен. Даже после
исправления стандартная выборка интерактивных внешних сессий ограничена 20 записями
([models.py, строки 46–52](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/models.py#L46-L52)).

## Backend и transport

По умолчанию это не тонкий UI над Desktop/Gateway. WebUI импортирует модули Hermes
Agent, создаёт/кеширует `AIAgent` в собственном Python-процессе, передаёт ему историю
из своего `Session` и запускает `run_conversation()`; браузер получает события через
собственные in-memory очереди и SSE
([README, строки 152–164](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/README.md#L152-L164),
[ARCHITECTURE, строки 240–260](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/ARCHITECTURE.md#L240-L260),
[строки 286–296](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/ARCHITECTURE.md#L286-L296)).
Это отдельный runtime-владелец, а не присоединение к retained agent/event route
Desktop.

Есть opt-in режим `HERMES_WEBUI_CHAT_BACKEND=gateway`. В нём WebUI вызывает
`/v1/runs` или `/v1/chat/completions`, передаёт исходный `session_id` через body и
`X-Hermes-Session-Id`, а затем адаптирует gateway SSE в свой формат
([gateway_chat.py, строки 361–376](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/gateway_chat.py#L361-L376),
[строки 419–434](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/gateway_chat.py#L419-L434),
[строки 830–840](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/gateway_chat.py#L830-L840)).
Это лучше для продолжения durable session через Hermes API, но README всё ещё
помечает полную передачу agent loop как не реализованную
([README, строки 162–164](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/README.md#L162-L164)).

Открытый [RFC #1925](https://github.com/nesquena/hermes-webui/issues/1925) объясняет
проблему напрямую: сейчас WebUI является параллельным Hermes runtime, владеет live
streams/cancellation/agent threads, а цель сделать его disposable thin client над
Hermes runtime ещё не достигнута.

## Синхронизация с Desktop

Для работы «по очереди» механизм уже близок к нужному: WebUI читает сообщения из
`state.db`, сохраняет тот же ID, а gateway mode умеет продолжать этот ID через Hermes
API. Но для работы одновременно есть существенные риски:

- открытый [issue #6299](https://github.com/nesquena/hermes-webui/issues/6299) описывает
  потерю промежуточных сообщений при отправке в одну беседу из WebUI и desktop-клиента;
  предполагаемая причина — конкуренция WebUI JSON sidecar и `state.db`/in-memory view;
- исправляющий [PR #6422](https://github.com/nesquena/hermes-webui/pull/6422) на дату
  проверки открыт и не смержен;
- открытый [issue #7219](https://github.com/nesquena/hermes-webui/issues/7219) показывает
  ещё один разрыв двух stores: после `/compress` в CLI/TUI WebUI может перестать
  обновлять уже импортированную сессию, потому что укороченный canonical transcript
  отвергается prefix/length guard в sidecar refresh;
- переименование из Desktop/CLI обратно в WebUI всё ещё не синхронизируется, что
  отслеживается в открытом [issue #3986](https://github.com/nesquena/hermes-webui/issues/3986).

Следовательно, «same persisted transcript ID» здесь в основном есть, а «same live
session owned by Desktop» — нет. Для одновременной работы нужен именно наш задуманный
плагин/bridge с одним владельцем runtime, очередью, idempotency и reconciliation.

## Auth, security и remote access

Hermes WebUI рассчитан на одного оператора, а не на OpenWebUI-подобную многопользовательскую
инсталляцию: это явно зафиксировано в Docker security model
([docker.md, строки 24–40](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/docs/docker.md#L24-L40)).

- bind по умолчанию — `127.0.0.1`; password auth по умолчанию выключен
  ([README, строки 360–365](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/README.md#L360-L365));
- поддерживаются пароль, passkeys/WebAuthn и OIDC
  ([README, строки 262–270](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/README.md#L262-L270));
- рекомендуемый удалённый доступ — SSH tunnel либо Tailscale; при bind на `0.0.0.0`
  документация требует включить пароль
  ([remote-access.md, строки 5–23](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/docs/remote-access.md#L5-L23),
  [строки 27–52](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/docs/remote-access.md#L27-L52));
- cookie `HttpOnly`, `SameSite=Lax`, `Secure` включается в secure context
  ([auth.py, строки 740–750](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/api/auth.py#L740-L750)).

Важный операционный нюанс: WebUI тесно импортирует внутренности конкретной версии
Hermes Agent и читает его state layout напрямую. Upstream требует обновлять/пиновать
обе части вместе и считает несовпадающие версии неподдерживаемыми
([README, строки 652–664](https://github.com/nesquena/hermes-webui/blob/c67fd2dd270a1128c2754200406bca58e9d9a25a/README.md#L652-L664)).

## Рекомендация и критерии пилота

1. Дождаться merge/release `#7415` либо тестировать его ветку только в отдельном
   checkout/state dir.
2. Подключить тот же `HERMES_HOME`, но сначала запускать WebUI на копии `state.db` и
   отдельном `HERMES_WEBUI_STATE_DIR`, потому что import создаёт sidecar и дальнейшие
   действия могут писать в общую базу.
3. Для реального пилота включить gateway backend, password auth и доступ только через
   loopback + SSH/Tailscale.
4. Проверить на одной тестовой Desktop-сессии: появление в sidebar, полный transcript,
   один browser turn, затем обновление Desktop. **Не отправлять одновременно.**
5. Условие замены OpenWebUI для простого персонального browser access: 20–30
   последовательных переключений Desktop ↔ WebUI без пропусков, дублей, смены ID и
   потери tool/reasoning сообщений.
6. Если условие включает live turn в обоих UI, очередь во время активного turn или
   multi-user access, Hermes WebUI пока не подходит; сохраняем архитектуру live bridge.

## Итоговая позиция

- **Для личного браузерного UI к сохранённым сессиям с переключением по очереди:**
  вероятная замена OpenWebUI после `#7415` и успешного пилота.
- **Для браузера как второго экрана той же активной Desktop-сессии:** не замена.
- **Для нескольких пользователей:** не замена OpenWebUI; upstream security model
  однопользовательский.
