---
icon: material/sitemap-outline
---

# Architecture

This page is the map you read before the code. It follows one Telegram update from the moment it lands on the webhook to the moment the bot replies, and names the object responsible at each hop. Once you can trace this path, most of `mitup_bot/` reads in order.

## The update flow

Every interaction, a tapped button, a typed command, a shared location, arrives as a single Telegram update and walks the same path:

<div class="uflow">
  <div class="uflow__node uflow__node--end"><span class="uflow__title">Telegram</span></div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span><span class="uflow__gap-label">HTTP POST to your webhook</span></div>

  <div class="uflow__node">
    <span class="uflow__title">POST /telegram</span>
    <span class="uflow__src">mitup_bot/web/telegram.py</span>
    <span class="uflow__desc">Checks the secret header, parses the JSON, and always answers 204.</span>
  </div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span><span class="uflow__gap-label">enqueue and return; work happens out of band</span></div>

  <div class="uflow__node">
    <span class="uflow__title">app.update_queue</span>
    <span class="uflow__src">python-telegram-bot</span>
    <span class="uflow__desc">An in-memory queue holding updates until the processor drains them.</span>
  </div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span></div>

  <div class="uflow__node">
    <span class="uflow__title">PerUserUpdateProcessor</span>
    <span class="uflow__src">mitup_bot/update_processor.py</span>
    <span class="uflow__desc">Drains the queue and serializes updates that share a (user, chat) key.</span>
  </div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span></div>

  <div class="uflow__node">
    <span class="uflow__title">callback_with_metrics</span>
    <span class="uflow__src">mitup_bot/handlers/registry.py</span>
    <span class="uflow__desc">Binds log context, times the handler, and routes faults to the error handler.</span>
  </div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span></div>

  <div class="uflow__node">
    <span class="uflow__title">guards</span>
    <span class="uflow__src">mitup_bot/guards.py</span>
    <span class="uflow__desc">Validate and narrow the raw update into the exact type the handler needs.</span>
  </div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span></div>

  <div class="uflow__node">
    <span class="uflow__title">@with_session</span>
    <span class="uflow__src">mitup_bot/db.py</span>
    <span class="uflow__desc">Opens one database session for the handler, read or write.</span>
  </div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span></div>

  <div class="uflow__node">
    <span class="uflow__title">view</span>
    <span class="uflow__src">mitup_bot/views/</span>
    <span class="uflow__desc">Builds the message text and inline keyboard for the screen to show.</span>
  </div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span></div>

  <div class="uflow__node">
    <span class="uflow__title">context.api reply</span>
    <span class="uflow__src">mitup_bot/custom_context.py</span>
    <span class="uflow__desc">Sends the rendered view back to the user over the Bot API.</span>
  </div>
  <div class="uflow__gap"><span class="uflow__arrow">&darr;</span></div>

  <div class="uflow__node uflow__node--end"><span class="uflow__title">Telegram</span></div>
</div>

<style>
.uflow {
  max-width: 440px;
  margin: 1.5rem auto;
  padding: 1.5rem;
  border: 1px solid var(--mitup-line);
  border-radius: 14px;
  background: var(--mitup-paper);
  display: flex;
  flex-direction: column;
  align-items: stretch;
}
.uflow__node {
  background: #ffffff;
  border: 1px solid var(--mitup-line);
  border-left: 4px solid var(--mitup-blue);
  border-radius: 10px;
  padding: 0.7rem 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.uflow__title { font-weight: 700; color: var(--mitup-ink); font-size: 0.9rem; line-height: 1.2; }
.uflow__src { font-family: ui-monospace, 'JetBrains Mono', monospace; font-size: 0.72rem; color: var(--mitup-blue-deep); }
.uflow__desc { font-size: 0.78rem; color: var(--mitup-ink-2); line-height: 1.4; }
.uflow__node--end {
  align-self: center;
  background: var(--mitup-blue-soft);
  border: 1px solid var(--mitup-blue-line);
  border-radius: 999px;
  padding: 0.35rem 1.3rem;
}
.uflow__node--end .uflow__title { color: var(--mitup-blue-deep); font-size: 0.85rem; }
.uflow__gap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.1rem;
  padding: 0.3rem 0;
}
.uflow__arrow { color: var(--mitup-ink-3); font-size: 1.2rem; line-height: 1; }
.uflow__gap-label { font-size: 0.7rem; color: var(--mitup-ink-3); text-align: center; line-height: 1.3; }
</style>

Two properties are worth holding onto. The webhook answers `204` for every well-formed request, even one it cannot parse, so Telegram never retries a poison-pill update in a tight loop. And the endpoint hands off to the queue instead of processing inline, so the HTTP request returns in milliseconds while the handler runs on its own.

## MitupRuntime, the composition root

[`mitup_bot/app.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/app.py) holds `MitupRuntime`, the single place where the whole bot is wired together. Its constructor builds the config, configures logging, metrics, the database, and the timezone API, then assembles the PTB `Application`: the bot token, the custom `MitupContext`, the rate limiter, and the `PerUserUpdateProcessor`. `HandlersRegistry.bind(app)` registers every handler onto that application. `run()` picks polling or webhook mode from config and starts the server. Nothing else constructs these pieces, so if you want to know what depends on what, start here.

## The webhook host

[`mitup_bot/web/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/web) holds the FastAPI app factory and the `POST /telegram` route. The runtime serves it through uvicorn with `workers=1`. That single worker is not an oversight: the PTB `Application` owns in-memory state (conversation states, per-user data) that cannot be shared across processes, so exactly one worker may exist. The endpoint validates the Telegram secret header, parses the update, puts it on `app.update_queue`, and returns.

## Concurrency in one paragraph

Because there is one worker, all concurrency lives inside that one event loop. PTB's own semaphore caps how many updates run at once through `bot.concurrent_updates`, which defaults to `1` and keeps processing observably sequential. `PerUserUpdateProcessor` adds a second guarantee: updates that share the same `(user, chat)` key always run one at a time, so a user's own actions never race each other even when the cap is raised above `1`. In-process locks are enough precisely because there is only ever one worker holding them.

!!! note "Going deeper on the web layer"

    This page stays at map altitude. For the FastAPI app factory, the lifespan that owns PTB's `initialize`/`start`/`set_webhook`/`stop`/`shutdown` sequence, secret-token validation, and the hard rules that keep the webhook from breaking silently, read the [web-conventions skill](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/web-conventions/SKILL.md).
