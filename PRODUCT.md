# Product

<!-- impeccable:product-schema 1 -->

> Written 2026-10-14 from the repository (`docs/V2_UX_DESIGN.md`, `docs/V2_STATUS.md`,
> `companion/DECISIONS.md`, the shipped UI) and the owner's redesign brief. The owner
> asked for no interview, so every fact below is inferred from that evidence. Lines
> marked *(inferred)* have no direct confirmation from the owner.

## Platform

desktop

Windows desktop app built with Python 3 + CustomTkinter, shipped as a PyInstaller
onedir exe (`FC26_Companion.exe` + `_internal_v2/`). No browser is involved; the
optional `--web` UI is a separate, secondary client.

## Users

One primary user: a FC 26 Career Mode player who runs EA's game with the community
Live Editor (LE) mod and wants to manage their save without memory hacking. They run
the Companion beside the game, usually on the same PC, alt-tabbing between the two,
often in long evening sessions *(inferred)*. They are an enthusiast power user:
comfortable with player IDs, OVR/POT, PlayStyles and FUT card variants, impatient with
ceremony, and protective of a save that represents many hours of play.

## Product Purpose

Make the jobs a Career Mode manager actually sits down to do fast and safe:

- read the live squad from the open save;
- change one player (card-based upgrade, manual attributes, AI-assisted plan);
- sign a player from a 238,000-card library spanning FIFA 18 to FC 26;
- plan several players at once with an AI model and review the plan;
- scan and heal injuries;
- prepare the squad before a match (fitness, sharpness, form, morale);
- see what was queued, what ran, and what it changed.

Success is a change that lands in the save exactly as reviewed, with the user never
unsure whether it has landed yet.

## Positioning

The Companion never injects into the game. Every change becomes a job file that a
resident Lua worker inside Live Editor runs on the next safe Career Mode event, and
the app only reports "applied" when the worker returns proof. That queue-and-proof
model, plus a full card catalogue and squad-level planning, is what an LE cheat table
cannot offer.

## Operating Context

- FC 26 must be started through Live Editor's launcher; the worker auto-arms from
  LE's `lua/autorun`.
- Jobs wait in `queue/` until a settled Career screen event fires (advance a day,
  open Team Management, return to the hub). "Queued" is a legitimate resting state.
- Data on disk: `state.sqlite` (prefs, job history, squad snapshots),
  `card_db/universe.sqlite` + `catalog.sqlite` + `teams.sqlite`, `portraits/`,
  `squad_plans/`, `snapshots/`.
- AI planning uses a user-supplied OpenAI key (Windows Credential Manager) or a Codex
  login.
- The owner tests every release in their live Career save, then rebuilds and redeploys
  the exe *(from session memory)*.

## Capabilities and Constraints

- Surfaces: Club (squad board), Player (one-player workstation), Sign (search, bag,
  checkout, review), Injuries, Matchday, Library (card search), Activity drawer
  (queue, history, plans, health, assets), Settings, command palette (Ctrl+K).
- The apply pipeline (write job, queue, worker, game) is non-negotiable and must not
  change shape.
- Five honest job states: staged, queued, running, applied, failed. Never conflated.
- CustomTkinter has no shadows, no compositing, no virtualised list and no native
  focus rings; anything beyond its widgets is built from frames, canvases and labels.
- Minimum window 1040 x 720. The UI scale preference ranges 0.85 to 1.5.
- Static skeletons only; animation loops froze v1 and are banned.
- Terminology the user knows: OVR, POT, PlayStyles, Career Mode, Live Editor, worker,
  queue, card variant, signing bag, checkout.

## Brand Commitments

- Name: "FC26 Companion" (exe `FC26_Companion.exe`). Older strings say
  "LE Companion"; the owner's current builds and README use FC26 Companion.
- A dark, dense, broadcast-graphics football tool: real football signals (OVR tiers,
  positions, crests, portraits) over decoration.
- The in-house 96-icon pack in `assets/icons/` (cyan stroke on charcoal).
- The OVR colour ramp that mirrors FUT card tiers is the signature people read first.

## Evidence on Hand

- Real squad snapshots in the owner's `state.sqlite` (for example Real Madrid,
  39 players) and real card data in `card_db/`.
- Signed portraits in `portraits/`.
- No testimonials, metrics, or user research exist. Nothing of that kind may be
  invented.

## Product Principles

1. Honest state over reassuring state. Queued is said as queued, with its trigger.
2. Show the change before making it. No queue write without a reviewed diff or an
   explicit blast-radius confirmation.
3. Never display or write a value that was not read.
4. One verb, one meaning, one primary action per screen.
5. Fast for a power user: dense data, keyboard reach, no ceremony on repeat tasks.

## Accessibility & Inclusion

Spec targets from `docs/V2_UX_DESIGN.md` section 8.1: contrast at least 4.5:1 for body
text and 3:1 for chips and borders, colour never the only signal, every disabled
control states why, visible focus, keyboard reach for every action, UI scale setting.
