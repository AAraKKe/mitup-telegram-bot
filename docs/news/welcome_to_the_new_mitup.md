---
icon: material/newspaper-variant-outline
hide:
    - toc
---

# Welcome to the new Mitup

<span class="news-meta">16 July 2026</span>

I'm Juanpe, and I've been the one person behind Mitup this whole time. These days it's two: my brother Enrique and I build it in whatever spare time we can find. This is the first time I'm telling the story of how it started.

## When Mitup was born

Back in 2015 I built the first version of Mitup. It wasn't even called Mitup. Our Destiny clan, a big group of Spanish players, had already been getting together for a while to play the weekly events and raids, and the Telegram Bot API had just launched. It was a small script using it to help manage the group. Managing over 100 people in groups of 6, with limited spots for the weekly raid, was hard (I still remember Crota's End and King's Fall like it was yesterday). At some point I got tired of the messages you had to send to get your name on the list for a raid group:

<div class="mitup-annotated">
  <div class="mitup-annotated__chat">
    <div class="mitup-annotated__body">
      <div class="mitup-bot-msg">
        <div class="mitup-bot-msg__content">
          <div class="mitup-bot-msg__sender">Diego</div>
          <div class="mitup-bot-msg__text">
            Hi all! I am looking for a group to do Crota tonight. Copy the message with your name on it and send it again<br/><br/>
            - Ander<br/>
            - Eli
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

We already had the group on Telegram because it handled big groups better than anything else, so I thought: why not build a small bot to organize this? Bots had only just come out. How hard could it be?

The first version was a small script that only worked while my desktop was on, storing my group's meetings in a file on my disk. It worked. It did the job. It was live.

Over time I started seeing other people's meetings appear in that file. Wait a minute. Who are these people? How do they know about the bot? If people who weren't my friends were finding it by chance and using it, this could not keep living on my computer.

That is how the first Mitup bot was born: put together over a weekend and deployed on a free host (I was doing my PhD and could barely afford food, let alone servers). It was not very powerful, and if nobody used it for 30 minutes it went to sleep, but hey, it worked.

More and more people started using it, and it needed paid hosting. By 2016 I was in my post-doc and could afford a small server to run it on. Not that expensive. I could pay for it.

And more and more people kept coming. I added support so people could ask for help and I would answer them. How do I share a meeting? I think there is a bug: my friends are being kicked out of the meeting. I would like the bot in a different language, could I help with that? Wait, what? You want to help translate the bot? So Mitup got multiple languages, all thanks to you, the community.

Over the years it needed to scale further, but the piece of code I had put together in a weekend could not keep up, and with over 40,000 users it was barely hanging by a thread. People were even asking to help build the bot, which was more than I ever imagined, but the code was barely maintainable. Something had to change.

## The new Mitup

I wanted to give more, and neither the code nor the infrastructure running the bot would let me. By 2019 I was ready to start from scratch and build something better. Then something happened in 2020 and... I didn't.

Fast forward to 2022. I was working at AWS and wanted to make it happen, but my schedule was insane and I struggled to find the time. Until my brother Enrique, finishing college, needed a final project to graduate. That was it. Rewrite the bot in a way that scales, deploy it to AWS, and build it so we can maintain it, test it, and ship the features people ask for. And off we went, rewriting everything from scratch, bit by bit, on infrastructure that lets it run better. After a few more turns in life, Enrique is now working, I am back in Spain, and the new Mitup is out.

!!! quote "Enrique"

    A final year project is supposed to take one semester. This one took a few more.

<figure markdown>
  ![The Mitup logo](../assets/images/logo-stacked-transparent-800.png){ width="320" }
</figure>

The new Mitup feels the same, but runs better. I hope it is like coming back to the home you always knew. If you hit an issue, email us at [support@mitup.social](mailto:support@mitup.social) and we can take it from there. The bot is finally open source: you can find it in the [Mitup GitLab repo](https://gitlab.com/meetupbot/mitup-telegram-bot) and [contribute to it](../collaborate/code_contributor.md), so we can maintain it more reliably and add new features.

Running the bot has never been free for long: it went from a free host to 20, then 30, then 60, and lately up to 120 euros a month, depending on traffic. So we also added Patreon support. If you [become a Mitup Host](../collaborate/donation.md), you help keep the bot running. If you can't, you can still use it. The only difference for non-supporters is [a few limits](../user-guide/limits.md) meant to keep costs down. The old Mitup had no limits, and the cost was that many people would abuse it and impact everyone else. The limits should be more than enough for the average group. If you use Mitup often, I hope you consider becoming a Mitup Host.

!!! quote "Enrique"

    I'm the one who gets notified and has to go debug it when a group finds the edge of those limits. Please become a Host.

We hope you enjoy the new Mitup and keep using it to get together with the people who matter in your life. That is what the bot is for. Thank you to everyone in this community who helped build it over the years with your proposals, your support messages, your reports, and your patience, and for sharing it with your own groups. The PhD student who built it for his group of friends would have never imagined it could help so many people get together.

Thanks a lot, from both of us,<br/>
Juanpe (and Enrique).
