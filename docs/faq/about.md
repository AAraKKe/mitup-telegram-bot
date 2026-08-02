---
description: "Mitup is an open-source Telegram bot for organizing meetups with friends, built by volunteers, free of ads, and funded on Patreon."
icon: material/account-group-outline
---

# About Mitup

Mitup is a Telegram bot for organising meetups with friends. You create a meeting, share one message, and the bot keeps track of who's coming. No group spreadsheet, no third bot in the chat, no "can everyone confirm by Friday" thread.

## Why it exists

Sorting out a brunch, a climbing trip, or a board-game night in a group chat is noisy. The message with the details scrolls away, half the replies are reactions, and nobody's sure who actually said yes.

Mitup moves that part into a private chat between you and the bot. You set the title, the time, and the options, and Mitup posts a card your friends can join with a tap. RSVPs, the waiting list, reminders, and timezones are handled for you, so the group chat stays a group chat.

## How it's built

Mitup lives inside Telegram and runs as a private DM with each person. It never joins your group, and it only sees the commands and replies sent straight to it. The [privacy page](privacy.md) spells out exactly what that means for your data.

The bot is open source under the MIT license, written in Python, and developed in the open on GitLab. If you want to see how any of it works, the code is at [gitlab.com/meetupbot/mitup-telegram-bot](https://gitlab.com/meetupbot/mitup-telegram-bot).

Mitup is free to use, with no ads. It's maintained by a small group of volunteers, and it stays online thanks to the people who back it on Patreon; backing it also raises the [limits](../user-guide/limits.md) on your account. If you'd like to help cover hosting, the [supporting Mitup](../collaborate/donation.md) page has the details.

## Get involved

Mitup is a community project. Whether you write code, translate, or report bugs, there's a place to help.

* [Contribute code](../collaborate/code_contributor.md): the project is Python and Postgres, with good-first-issues tagged on GitLab.
* [Translate Mitup](../collaborate/translator.md): help bring the bot to your language on Crowdin.

Everyone who takes part agrees to the [code of conduct](../collaborate/code_of_conduct.md).

## Get in touch

There are three places to go, depending on what you're after.

* The [user guide](../user-guide/index.md) covers every screen the bot shows you, page by page. It answers most questions on its own, so it's the place to start.
* Email [support@mitup.social](mailto:support@mitup.social) for anything you'd rather ask privately, or when the guide leaves you stuck.
* The [community group on Telegram](https://t.me/mitupgroup) is the open group for people who use Mitup.

Bugs and feature ideas are best filed as [an issue on GitLab](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues), where you can see what's already been reported.
