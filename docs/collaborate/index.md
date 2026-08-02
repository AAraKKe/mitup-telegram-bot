---
description: "Ways to help keep Mitup free and ad-free: become a Host on Patreon, write code, translate the bot on Crowdin, or report a bug on GitLab."
icon: material/handshake-outline
hide:
    - toc
---

# Ways to help Mitup

Mitup is free, carries no ads, and is maintained by a small group of volunteers. It stays online because people chip in, and money is only one of the ways to do it.

<style>
/* Compact variant of the home page's Patreon strip, sized for the narrower documentation
   content column: pitch on the left, the three tiers as slim stacked rows on the right,
   tighter padding and type throughout. The three collaborate cards hold one row down to
   tablet width. */
.md-typeset .patreon-strip {
  grid-template-columns: 1.5fr 1fr;
  align-items: center;
  padding: 1.4rem 1.6rem;
  gap: 1.5rem;
  margin: 1.5rem 0;
}
.md-typeset .patreon-strip h2 { font-size: 1.25rem !important; }
.md-typeset .patreon-strip .patreon-tag { margin-bottom: 0.3rem; }
.md-typeset .patreon-tiers { grid-template-columns: 1fr; gap: 6px; }
.md-typeset .patreon-tier {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0 0.6rem;
  align-items: baseline;
  text-align: left;
  padding: 0.45rem 0.9rem;
}
.md-typeset .patreon-tier .tier-name { margin-bottom: 0; }
.md-typeset .patreon-tier .tier-amt { font-size: 1rem; }
.md-typeset .patreon-tier .tier-tag { grid-column: 1 / -1; margin-top: 0.1rem; }
.md-typeset .grid.cards.collab-grid { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 700px) {
  .md-typeset .patreon-strip { grid-template-columns: 1fr; }
  .md-typeset .grid.cards.collab-grid { grid-template-columns: 1fr; }
}
</style>

<div class="patreon-strip" markdown>

<div markdown>

<span class="patreon-tag">Support on Patreon</span>

## Become a Host

Monthly members pay for the servers, the database, and the domain. Every Host gets a badge in the bot and a seat in the members-only Telegram group, and the two bigger tiers raise the [limits](../user-guide/limits.md) on your account.

[See the tiers and how they work →](donation.md){.md-button .md-button--primary}

</div>

<div class="patreon-tiers" markdown>
<div class="patreon-tier">
<div class="tier-name">Brewer</div>
<div class="tier-amt">€3<span class="per">/mo</span></div>
<div class="tier-tag">A coffee a month</div>
</div>
<div class="patreon-tier featured">
<div class="tier-name">Gamemaster</div>
<div class="tier-amt">€5<span class="per">/mo</span></div>
<div class="tier-tag">Raises your limits</div>
</div>
<div class="patreon-tier">
<div class="tier-name">Commissioner</div>
<div class="tier-amt">€10<span class="per">/mo</span></div>
<div class="tier-tag">No limits at all</div>
</div>
</div>

</div>

## Other ways to help

<div class="grid cards collab-grid" markdown>

* :fontawesome-solid-code: **Contribute code**

    ---
    Mitup is MIT-licensed, Python and Postgres. Work starts from an issue a maintainer has accepted, so pick one up or open your own.

    [Code contributor guide →](code_contributor.md)

* :fontawesome-solid-language: **Translate Mitup**

    ---
    Six languages so far, all translated by the community on Crowdin. Fluency and basic English are enough, and no code is involved.

    [Translator guide →](translator.md)

* :fontawesome-solid-bug: **Bugs and ideas**

    ---
    File them [on GitLab](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues), where you can see what's already reported. A screen recording gets a bug fixed fastest.

    [Community group on Telegram →](https://t.me/mitupgroup)

</div>

Whichever way you pick, everyone taking part agrees to the [code of conduct](code_of_conduct.md).
