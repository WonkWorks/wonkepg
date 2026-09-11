# WonkEPG Positioning and Documentation Strategy

## Purpose

This memo summarizes the current XMLTV/IPTV/DVR tool landscape and recommends how WonkEPG should position itself in its README, documentation, screenshots, examples, and GitHub presence.

The principal conclusion is:

**WonkEPG should not position itself as another IPTV manager, another HDHomeRun emulator, another EPG source, or merely another XMLTV merger.**

Its strongest and most differentiated position is:

> **WonkEPG is an EPG intelligence layer that reconciles multiple imperfect guide sources into one normalized, enriched, DVR-ready XMLTV feed.**

The practical problem it addresses is not primarily “how do I watch IPTV?” It is:

> **How do I give Plex, Jellyfin, Emby, or another DVR sufficiently reliable programme metadata to behave like a consumer-grade DVR?**

---

# 1. Market finding

There are already mature solutions at several neighboring layers.

### IPTV transport / tuner virtualization

Threadfin, xTeVe, Dispatcharr, and related projects solve the problem of turning IPTV/M3U streams into something Plex, Jellyfin, Emby, and other media servers can consume.

Dispatcharr is particularly ambitious. It describes itself as an IPTV and stream-management platform and includes multiple IPTV sources, channel management, HDHomeRun emulation, EPG handling, VOD, stream management, and integration with Plex/Jellyfin/Emby. citeturn926578search6turn926578search5

This is **not a layer WonkEPG needs to recreate**.

A core architectural principle should therefore remain:

> **Do not replace the part that already works.**

WonkEPG should be equally comfortable sitting beside Threadfin, Dispatcharr, xTeVe, or no tuner middleware at all.

---

# 2. XMLTV merging already exists — but mostly at the schedule level

WonkEPG must be careful not to claim that combining multiple XMLTV files is novel.

The longstanding XMLTV toolset includes `tv_merge`. Its documented behavior is to merge schedules by adding/replacing/deleting programmes. Importantly, its own documentation explicitly states that programmes from the incoming file replace programmes in the master and that data are **not updated within programmes**. citeturn926578search0turn926578search4

That creates an excellent distinction for WonkEPG.

Traditional merge:

```text
Source A programme
Source B programme
        ↓
choose/replace one programme record
```

WonkEPG:

```text
Source A programme
Source B programme
Source C programme
        ↓
determine whether they describe the same airing
        ↓
compare available information
        ↓
construct one canonical programme record
```

This distinction should appear very early in the README.

A suggested formulation:

> **WonkEPG is not an XML concatenator.**
>
> Traditional XMLTV mergers combine schedules or select one source when programmes overlap. WonkEPG reconciles matching programmes across sources and builds a canonical record from the best available metadata.

That is much more defensible than “WonkEPG merges multiple EPG feeds.”

---

# 3. The real market gap is programme-level reconciliation

The apparent whitespace is not XMLTV parsing, EPG downloading, or multiple-source support individually.

The less mature area is:

**programme-level identity and field-level reconciliation across several imperfect sources.**

Example:

```text
Provider EPG
Bravo — 8:00 PM
title ✓
subtitle ✓
description ✓
season/episode ✗
credits ✗
artwork ✗

Source B
same airing
title ✓
subtitle ✓
season/episode ✓
original air date ✓
credits ✓
ratings ✓

Source C
same airing
title ✓
description ✓
artwork ✓
```

A source-priority system tends to answer:

```text
Use Source B for Bravo.
```

WonkEPG should answer:

```text
These records describe the same airing.

Schedule          ← most trustworthy/confirmed source
Title             ← normalized consensus
Subtitle          ← best available value
Description       ← preferred/best available value
Season/Episode    ← source with reliable episode identity
Original air date ← best authoritative source
Credits           ← available enrichment source
Rating            ← available enrichment source
Artwork           ← available artwork source
```

This should be the central conceptual diagram of the project.

The difference can be summarized in one sentence:

> **Adding several EPG sources is not the same thing as reconciling several EPG sources.**

---

# 4. DVR behavior is the reason this matters

The README should avoid presenting guide metadata as merely something that makes the television grid prettier.

For a DVR, XMLTV is operational data.

Jellyfin's own XMLTV parser explicitly processes fields including episode numbers, `new`, `previously-shown`, ratings, credits, images, and related programme semantics. citeturn926578search3

The XMLTV schema itself contains programme-level concepts including `episode-num`, `previously-shown`, `premiere`, `new`, ratings, images, credits, categories, and subtitles. citeturn926578search2

This supports a key WonkEPG message:

> **For live IPTV, the stream is the product. For DVR, the guide is part of the control plane.**

Missing or inconsistent metadata can affect:

- series recording;
- episode identity;
- repeat detection;
- “new episode” behavior;
- duplicate suppression;
- library classification;
- recording names;
- guide horizon;
- programme descriptions;
- artwork;
- ratings;
- downstream matching.

The project should therefore describe its output as **DVR-ready XMLTV**, not merely “clean XMLTV.”

---

# 5. The user problem is already visible in the community

Reddit and self-hosted communities repeatedly surface users who are:

- manually editing XMLTV;
- writing Python scripts to repair guide data;
- combining multiple sources;
- extending short guide horizons;
- adding season and episode numbers;
- trying to determine why “Record Series” appears or disappears;
- troubleshooting repeat/new semantics;
- correcting channel mappings;
- trying one EPG after another because each has different deficiencies.

A recent HTPC/Jellyfin discussion explicitly notes that useful DVR operation requires external guide data and that short free guide windows may be materially less useful than longer ones. citeturn926578reddit48

The important positioning lesson is:

**WonkEPG is productizing a class of fixes that technically capable users are already building for themselves.**

The README should use the language of those problems rather than developer terminology wherever possible.

Good:

> One source has the schedule. Another knows the episode. Another has the artwork.

Less useful:

> Hierarchical multi-source XMLTV enrichment engine with deterministic merge policies.

The second may belong later in technical documentation. It should not be the first thing users see.

---

# 6. Recommended primary positioning

Suggested core descriptor:

> **WonkEPG is an EPG intelligence layer for Plex, Jellyfin, Emby, and other XMLTV consumers. It reconciles multiple guide sources into one normalized, enriched, DVR-ready XMLTV feed.**

Suggested short tagline:

> **Your IPTV works. Your DVR needs better data.**

Alternative:

> **IPTV gives you the channels. WonkEPG gives your DVR enough information to understand them.**

Another strong explanatory sentence:

> **One guide may have the correct schedule, another the correct episode identity, and another better artwork or metadata. WonkEPG combines what each source knows instead of forcing you to choose one.**

These should be favored over:

> XMLTV aggregator

or:

> EPG merger

because both dramatically undersell the project.

---

# 7. Recommended README opening structure

The first screenful of the README should answer four questions immediately.

## What is the problem?

Example:

> IPTV providers often include an EPG, but guide quality varies dramatically. A feed may have correct start times while lacking season and episode numbers, original air dates, artwork, useful descriptions, or enough future coverage for reliable DVR scheduling.

## What does WonkEPG do?

> WonkEPG ingests multiple guide sources, identifies matching channels and programmes, reconciles their metadata, and serves one stable XMLTV feed downstream.

## What does it work with?

Prominently name:

- Plex
- Jellyfin
- Emby
- Threadfin
- Dispatcharr
- xTeVe
- generic XMLTV consumers

Avoid implying Threadfin is required.

## What does it deliberately not do?

Something close to:

> WonkEPG does not proxy IPTV streams, emulate an HDHomeRun, or replace your media server. Keep the tuner/stream stack that already works.

This is an important design boundary and a differentiator.

---

# 8. Recommended architecture diagram

A diagram similar to the following should appear near the top:

```text
Provider XMLTV ─────┐
                    │
EPG source #2 ──────┼────► WonkEPG ─────► one DVR-ready XMLTV feed
                    │                         │
EPG source #3 ──────┘                         ├──► Plex
                                              ├──► Jellyfin
IPTV streams ─► Threadfin / Dispatcharr ──────┤
                                              └──► Emby
```

This diagram makes three important points without requiring explanation:

1. WonkEPG is independent of stream transport.
2. Multiple sources feed WonkEPG.
3. WonkEPG serves established downstream applications rather than replacing them.

---

# 9. Recommended “why not just…” section

The README or documentation should explicitly answer obvious objections.

### Why not just use the IPTV provider's EPG?

Because provider EPGs often differ in metadata depth, coverage, artwork, episode identity, descriptions, and reliability.

### Why not just use EPGTalk, EPGShare, or another single guide?

Because choosing one source trades one set of strengths and weaknesses for another.

WonkEPG's premise is that no single source needs to be complete.

### Why not use XMLTV `tv_merge`?

Because conventional schedule merging selects/replaces programme records; WonkEPG's differentiator is reconciliation **within matching programmes**. XMLTV's own `tv_merge` documentation explicitly notes that it does not update data within programmes. citeturn926578search0

### Why not let Threadfin/xTeVe do it?

Those applications primarily solve channel/tuner/stream middleware and basic EPG plumbing. WonkEPG is intentionally specialized around guide intelligence.

### Why not use Dispatcharr?

Dispatcharr is a broad IPTV management platform and explicitly integrates with Plex and Jellyfin as a tuner/EPG backend. citeturn926578search5turn926578search6

WonkEPG should not position Dispatcharr as an enemy or inferior product.

The answer is:

> Dispatcharr can manage the television backend. WonkEPG specializes in constructing the best guide possible. They can be used together.

---

# 10. Claims the project should avoid

Do not claim:

> “The first XMLTV merger.”

False and unnecessary.

Do not claim:

> “The only multi-source EPG tool.”

Also false or at least indefensible.

Do not claim:

> “Fixes Plex DVR.”

Too absolute. Plex itself has proprietary behavior and may change.

Prefer:

> “Produces XMLTV intended to improve DVR metadata quality and compatibility.”

Do not make WonkEPG dependent in its public identity on grey-market IPTV.

Its legitimate general-purpose use case is much broader:

- OTA;
- free streaming services;
- legitimate IPTV;
- community guide feeds;
- regional guide sources;
- self-generated XMLTV;
- Plex/Jellyfin/Emby DVR systems.

This will make the project much easier to discuss in r/selfhosted, Jellyfin communities, GitHub, Docker communities, and future package repositories.

---

# 11. Consumer profiles should become part of the architecture

A potentially strong long-term feature is explicit downstream output profiles:

```text
Generic XMLTV
Plex
Jellyfin
Emby
```

The canonical programme model remains independent of the consumer.

Serialization can then account for known downstream expectations or quirks.

This would reinforce WonkEPG's role as the semantic layer.

For Jellyfin in particular, downstream behavior is inspectable because Jellyfin's XMLTV parser is open source. It explicitly processes episode numbering, new/repeat state, credits, images, ratings and other fields. citeturn926578search3

For Plex, behavior may need to be established experimentally and documented as tested compatibility rather than authoritative internal behavior.

---

# 12. Feature hierarchy for documentation

Not every feature should receive equal prominence.

## Core identity

These define what WonkEPG is:

1. Multiple XMLTV sources.
2. Cross-source channel matching.
3. Programme-to-programme matching.
4. Field-level enrichment.
5. Conflict resolution.
6. Episode/series identity normalization.
7. Guide horizon extension.
8. Stable unified XMLTV output.

## DVR intelligence

These explain why the project matters:

9. Season/episode normalization.
10. Repeat/new/premiere handling.
11. Original-air-date reconciliation.
12. Duplicate-airing detection.
13. Conflict detection.
14. Consumer-specific output normalization.
15. Movie/series/programme classification where supported.

## Operational maturity

These help the project become something people depend on:

16. Source health monitoring.
17. Graceful degradation when one source fails.
18. Cached last-known-good output.
19. Scheduled refresh.
20. Provenance/debugging.
21. Docker-first installation.
22. Stable HTTP XMLTV endpoint.

The README should emphasize the first two groups.

The detailed docs should explain the third.

---

# 13. Provenance could become an important differentiator

One unusually useful feature for advanced users would be the ability to inspect:

> Why does this programme contain this value?

For example:

```text
Title:
  Hive
  confidence: high

Season/Episode:
  EPGTalk
  matched by title + subtitle + schedule

Artwork:
  EPGShare

Description:
  Hive
  preferred according to source policy
```

This is particularly valuable because multi-source reconciliation inevitably produces disagreements.

Instead of hiding them, WonkEPG can make its decisions inspectable.

That transforms the product from a black-box XML merger into an **explainable guide engine**.

This may also make GitHub bug reports far more useful because users can identify exactly which source/matcher/field produced an unexpected result.

---

# 14. The Bravo example should be retained as a canonical demo

The existing Bravo test is unusually useful because it demonstrates the problem with real data rather than a hypothetical example.

The sample documentation should show:

```text
Hive
→ good baseline schedule

EPGTalk
→ richer episode metadata / credits / ratings / longer coverage

EPGShare
→ supplemental metadata/artwork
```

Then show one programme before and after reconciliation.

This should probably become:

- README example;
- screenshot/demo fixture;
- integration test;
- sample configuration;
- possibly documentation walkthrough.

A repeatable real-world fixture is much stronger than a fabricated example.

---

# 15. GitHub positioning

The repository should look like a project intended for other people from day one.

Recommended visible elements:

- concise README;
- screenshots;
- architecture diagram;
- Docker quick start;
- example configuration;
- CONTRIBUTING.md;
- LICENSE;
- SECURITY.md;
- changelog/releases;
- issue templates;
- clear support boundary;
- sample XMLTV fixture;
- documentation explaining merge decisions;
- compatibility matrix.

Avoid a README that reads like development notes from the original household setup.

The repository should communicate:

> **This began as one user's problem, but the software is deliberately generalized.**

---

# 16. Contributor positioning

The likely first contributors are not necessarily generic Plex users.

The strongest contributor pool appears to be technically inclined Jellyfin/self-hosted users who already:

- manipulate XMLTV;
- write Python scripts;
- build provider-specific guide generators;
- troubleshoot programme identity;
- create channel matchers;
- maintain Docker stacks.

Documentation should make extension points obvious.

For example:

```text
Want to add:
• a source adapter?
• a matcher?
• a normalization rule?
• a consumer profile?
• a metadata provider?

See CONTRIBUTING.md
```

The goal is to convert:

> “I wrote a Python script for my weird regional EPG.”

into:

> “I added support for that case to WonkEPG.”

That is the transition from personal project to open-source ecosystem.

---

# 17. Desired relationship with Threadfin and Dispatcharr

WonkEPG should remain deliberately neutral.

Threadfin:

> mature/simple virtual tuner and IPTV bridge.

Dispatcharr:

> broader IPTV management/backend platform with HDHR, channel, EPG, stream and related functionality. citeturn926578search5turn926578search6

WonkEPG:

> specialized guide intelligence.

Recommended wording:

> **Use the IPTV backend you prefer. WonkEPG focuses on the guide.**

That prevents the project from becoming obsolete if the community moves from Threadfin toward Dispatcharr or another future tuner layer.

---

# 18. Long-term positioning statement

The project should aspire to become the answer to this question:

> “My IPTV channels work in Plex/Jellyfin, but the guide data isn't good enough for reliable DVR. What do I put in front of it?”

Desired community answer:

> **Use WonkEPG.**

That is a much better success metric than attempting to become an all-in-one IPTV application.

---

# Recommended README language

## One-line description

> **WonkEPG reconciles multiple XMLTV guide sources into one normalized, enriched, DVR-ready EPG for Plex, Jellyfin, Emby, and other XMLTV consumers.**

## Short explanation

> IPTV guide sources are rarely complete. One may have the correct schedule, another better season and episode information, and another richer descriptions, artwork, ratings, or longer coverage. WonkEPG identifies matching programmes across those sources and combines what each knows into a single canonical guide.

## Scope statement

> WonkEPG does not proxy streams, emulate tuners, or replace your media server. Use Threadfin, Dispatcharr, xTeVe, or whatever television backend already works for you. WonkEPG focuses on making the guide better.

## Core differentiator

> **Multiple sources are easy. Reconciling what they say is the hard part.**

---

# Final recommendation

WonkEPG should resist feature pressure that turns it into an IPTV server.

The emerging self-hosted television ecosystem already contains several projects competing to own:

- streams;
- tuners;
- M3U;
- Xtream;
- transcoding;
- buffering;
- DVR;
- VOD;
- viewers.

There is little advantage in reproducing those systems.

The stronger niche is narrower:

> **WonkEPG understands guide data.**

It should become the component that other projects do not need to reinvent.

Architecturally:

```text
stream tools should be excellent at streams
media servers should be excellent at playback and DVR
WonkEPG should be excellent at describing what is on television
```

If that boundary is preserved, WonkEPG can remain useful regardless of whether the downstream ecosystem ultimately standardizes around Plex, Jellyfin, Emby, Threadfin, Dispatcharr, or something that does not exist yet.