---
description: "What each Mitup release brings, newest first: a short summary of what you will notice, with the complete list of changes folded underneath."
icon: material/new-box
---

# What's new

Every release of Mitup gets a section here, newest first. Each one opens with what you will notice in the bot, and the complete list of changes is folded underneath. The longer stories live on the [Blog](../blog/index.md).

<!-- releases -->

## v3.0.0

_15 November 2026_

From this release on, you can keep up with what changes in each version of Mitup.

- The [main menu](../user-guide/main_menu.md#account) shows the version of Mitup you are using, with *What's new*{.button-like} beside it, which opens this page.
- Links in a meeting's title or description can be tapped on the [meeting card](../user-guide/create_a_meeting.md#the-meeting-card).

??? note "Full changelog"

    **New**

    - The main menu shows the version of Mitup you are using, with a What's new link beside it that opens the list of what each release brings. ([!785](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/785))

    **Changed**

    - Links in meeting titles and descriptions are kept. Pasted addresses, links behind text and email addresses can be tapped on the meeting card. ([!778](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/778))
    - When a meeting cannot be shared, the share panel says it is not a shareable meeting instead of showing up empty. ([!781](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/781))
    - The website's news posts live on a blog at /blog/, old /news/ links lead to the same post there, and a new What's new page lists what each release brings. ([!784](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/784))

    **Fixed**

    - When something goes wrong after a tap on a meeting card in a group, only the person who tapped sees the error, as a pop-up. An inline search that hits an error returns empty results instead of loading forever. ([!777](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/777))
    - The cards of a meeting show it as deleted however the meeting was deleted, including the automatic cleanup, deleting all past meetings, and the deletion of its owner's account. ([!779](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/779))
    - When someone deletes their account, they leave the meetings they had joined: the meeting cards update and the next person on the waiting list gets the place. ([!782](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/782))
    - The supporting Mitup and limits pages say what the Brewer tier adds: the same caps as a free account, plus photos on your meeting cards. ([!792](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/792))

    **Under the hood**

    - The daylight saving test for the one-week end limit uses Beirut, whose clock rules are stable across timezone database versions. ([!780](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/780))
    - Every merge request records its change in a changelog fragment under changelog.d/, checked by CI, and towncrier compiles the fragments into CHANGELOG.md. ([!783](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/783))
    - A release is prepared in a merge request that compiles the changelog and adds the version to the What's new page, and is then tagged with mb release. ([!786](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/786))
    - Renovate and the Crowdin sync write their own changelog fragments, the changelog check requires them, and preparing a release fills in their merge request number. ([!787](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/787))
    - Tapping the note on a refused share opens the main menu, and a share query with a number too large to be a meeting id is refused like any other instead of raising a fault. ([!788](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/788))
    - The release skill asks for a shorter summary written for users: a word limit per release size, one sentence per point, and fixes only when people were running into them. ([!793](https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests/793))

Releases before v3.0.0 are listed on the [GitLab releases page](https://gitlab.com/meetupbot/mitup-telegram-bot/-/releases).
